"""Read-only workspace access for the local UI.

The manager is always constructed read-only.  That is the mechanism behind the
"this server records no canonical event" guarantee, not a convention layered on
top of a writable manager.
"""

from __future__ import annotations

import hashlib
import threading
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_commons.catalog import CATALOG_SECTIONS, load_role_catalog, write_role_catalog
from agent_commons.config import CommonsPaths
from agent_commons.domain.agents import PROFILE_NARROWING
from agent_commons.errors import ConfigurationError, ValidationError
from agent_commons.services.manager import CommonsManager
from agent_commons.ui.graph import build_graph
from agent_commons.views import bounded_copy, truncate_utf8


def _iso_now() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


def _session_state(session: Any) -> str:
    """Effective session state: a session whose TTL lapsed is not still active."""

    if session.status != "active":
        return str(session.status)
    return "expired" if session.expired else "active"


def ledger_fingerprint(paths: CommonsPaths) -> str:
    """Cheap change detector over immutable canonical files.

    Canonical events and manifests are append-only and never rewritten, so
    name, size, and mtime are sufficient: a full projection replay only runs
    when the ledger actually changed.
    """

    digest = hashlib.sha256()
    for root, pattern in ((paths.events, "*/*/*/evt.*.json"), (paths.manifests, "*/*/*.json")):
        if not root.exists():
            continue
        for path in sorted(root.glob(pattern)):
            try:
                info = path.stat()
            except OSError:  # pragma: no cover - file vanished mid-scan
                continue
            digest.update(
                f"{path.relative_to(root)}\0{info.st_size}\0{info.st_mtime_ns}\n".encode()
            )
    # Sessions are graph nodes but live in operational state, so a ledger-only
    # fingerprint would leave a closed or newly opened session on screen forever.
    sessions = paths.state_root / "sessions"
    if sessions.exists():
        for path in sorted(sessions.rglob("*")):
            if not path.is_file():
                continue
            try:
                info = path.stat()
            except OSError:  # pragma: no cover - file vanished mid-scan
                continue
            digest.update(
                f"{path.relative_to(sessions)}\0{info.st_size}\0{info.st_mtime_ns}\n".encode()
            )
    return "sha256:" + digest.hexdigest()


class UIContext:
    """Owns the read-only manager and turns a snapshot into a renderable graph."""

    def __init__(
        self,
        repo: Path,
        *,
        state_root: Path | None = None,
        state_base: Path | None = None,
        state_source: str = "default",
        writer_session_id: str | None = None,
        catalog_path: Path | None = None,
        catalog_editing: bool = False,
    ) -> None:
        self.repo = repo
        self._state_root = state_root
        self._state_base = state_base
        self._state_source = state_source
        self._catalog_path = catalog_path
        # A separate gate from --enable-writes on purpose.  Editing presets and
        # editing the set of things a child process may run are different
        # magnitudes of privilege, and one checkbox for both would hide that.
        self._catalog_editing = bool(catalog_editing)
        # A writable context is opt-in and needs a real operator session, the
        # same identity the CLI writes under.  Absent one, this stays the
        # read-only server it has always been.
        self.writer_session_id = writer_session_id
        self.server_instance_id = uuid.uuid4().hex
        # One poller runs per SSE connection, so sequence and graph are shared
        # mutable state across worker threads.
        self._guard = threading.RLock()
        self._seq = 0
        self._fingerprint = ""
        self._graph: dict[str, Any] | None = None

    @property
    def writes_enabled(self) -> bool:
        return self.writer_session_id is not None

    @property
    def catalog_editing_enabled(self) -> bool:
        return self._catalog_editing and self._catalog_path is not None

    def manager(self) -> CommonsManager:
        return CommonsManager(
            self.repo,
            state_root=self._state_root,
            state_base=self._state_base,
            state_source=self._state_source,
            read_only=True,
        )

    def writer(self) -> CommonsManager:
        """The one manager every mutating route goes through.

        There is no second write path: this is the same ``CommonsManager`` the
        CLI and the MCP adapter use, opened under an explicit operator session.
        """

        if self.writer_session_id is None:
            raise ConfigurationError(
                "this UI was started read-only; restart with --enable-writes to record events"
            )
        manager = CommonsManager(
            self.repo,
            session_id=self.writer_session_id,
            state_root=self._state_root,
            state_base=self._state_base,
            state_source=self._state_source,
        )
        # Invalidate the cached graph so the next poll reflects the write.
        return manager

    def invalidate(self) -> None:
        with self._guard:
            self._fingerprint = ""

    @property
    def seq(self) -> int:
        return self._seq

    def paths(self) -> CommonsPaths:
        return self.manager().paths

    def fingerprint(self) -> str:
        return ledger_fingerprint(self.paths())

    def meta(self) -> dict[str, Any]:
        from agent_commons import __version__
        from agent_commons.ui import META_SCHEMA, TRUST_NOTE, TRUTH_LAYERS

        manager = self.manager()
        return {
            "schema": META_SCHEMA,
            "agent_commons_version": __version__,
            "workspace_id": manager.workspace_id,
            "repo": str(self.repo),
            "read_only": not self.writes_enabled,
            "writes_enabled": self.writes_enabled,
            "writer_session_id": self.writer_session_id,
            "server_instance_id": self.server_instance_id,
            "trust_note": TRUST_NOTE,
            "truth_layers": list(TRUTH_LAYERS),
        }

    def rebuild_graph(self) -> dict[str, Any]:
        manager = self.manager()
        # Sample the fingerprint *before* reading, and keep the earlier of the
        # two samples.  Taking it afterwards records a change the graph does not
        # contain, so the next comparison sees no difference and the view stays
        # frozen for as long as the ledger is quiet -- while the stream keeps
        # reporting itself live.
        before = ledger_fingerprint(manager.paths)
        snapshot = manager.snapshot()
        sessions = [
            session.actor_context() | {"state": _session_state(session)}
            for session in manager.sessions.list_sessions()
        ]
        after = ledger_fingerprint(manager.paths)
        # Disagreement means the ledger moved while it was being read, so this
        # graph is already behind: record nothing as seen and let the next check
        # rebuild.
        fingerprint = before if before == after else ""
        graph = build_graph(
            snapshot,
            sessions=sessions,
            workspace_id=manager.workspace_id,
            generated_at=_iso_now(),
            ledger_fingerprint=fingerprint,
            server_instance_id=self.server_instance_id,
            seq=self._seq + 1,
            read_diagnostics={
                "source": "canonical",
                "reason": "read_only",
                "projection": dict(snapshot.replay_metrics),
            },
        )
        with self._guard:
            self._seq += 1
            graph["seq"] = self._seq
            self._fingerprint = fingerprint
            self._graph = graph
            return graph

    def graph(self) -> dict[str, Any]:
        with self._guard:
            graph = self._graph
        return graph if graph is not None else self.rebuild_graph()

    def snapshot_frame(self) -> tuple[int, dict[str, Any]]:
        """Sequence and graph as one consistent pair.

        Reading them separately lets a concurrent rebuild pair a sequence with a
        different graph, which is exactly what the resume contract relies on.
        """

        with self._guard:
            if self._graph is not None:
                return self._seq, self._graph
        graph = self.rebuild_graph()
        # Read the sequence off the graph, not off the context: a concurrent
        # rebuild may already have moved the counter past this graph.
        return int(graph["seq"]), graph

    def refresh_if_changed(self) -> bool:
        with self._guard:
            known = self._fingerprint
        if self.fingerprint() != known:
            self.rebuild_graph()
            return True
        return False

    # -- catalogue ------------------------------------------------------------

    def catalog(self) -> dict[str, Any]:
        """What the gear panel may offer, and who owns each half of it.

        The capability-granting half is operator configuration outside the
        workspace and is served read-only: a bearer token must not be able to
        widen what any child process can do.  The narrowing half -- presets and
        the profiles' own fixed tool sets -- is safe to choose from here.
        """

        from agent_commons.ui import CATALOG_SCHEMA

        snapshot = self.manager().snapshot()
        catalogue = load_role_catalog(self._catalog_path, workspace_root=self.repo)
        editable = ["presets"] + (list(CATALOG_SECTIONS) if self.catalog_editing_enabled else [])
        return {
            "schema": CATALOG_SCHEMA,
            "editable_here": editable,
            "operator_owned": ["profiles"] + (
                [] if self.catalog_editing_enabled else list(CATALOG_SECTIONS)
            ),
            "catalog_editing_enabled": self.catalog_editing_enabled,
            "catalog_path": str(self._catalog_path) if self._catalog_path else None,
            "profiles": sorted(PROFILE_NARROWING),
            "grant_levels": ["deny", "ask", "auto"],
            "context_modes": ["fresh", "accumulated"],
            **catalogue,
            "presets": [
                bounded_copy(record)
                for _, record in sorted(snapshot.agents.items())
                if record.get("template") and record.get("state") == "active"
            ],
        }

    # -- catalogue editing ----------------------------------------------------

    def _require_catalog_editing(self) -> Path:
        if not self.catalog_editing_enabled:
            raise ConfigurationError(
                "catalogue editing is off; restart with --role-catalog and "
                "--enable-catalog-editing"
            )
        assert self._catalog_path is not None
        return self._catalog_path

    def _catalog_users(self, section: str, entry_id: str) -> list[str]:
        """Active roles that would break if this entry disappeared."""

        field = "skills" if section == "skills" else "tool_allowlist"
        return sorted(
            str(record["id"])
            for record in self.manager().snapshot().agents.values()
            if record.get("state") == "active" and entry_id in (record.get(field) or ())
        )

    def save_catalog_entry(
        self,
        *,
        section: str,
        entry_id: str,
        title: str,
        description: str = "",
        instruction: str | None = None,
    ) -> dict[str, Any]:
        path = self._require_catalog_editing()
        if section not in CATALOG_SECTIONS:
            raise ValidationError(f"unknown catalogue section: {section}")
        catalogue = load_role_catalog(path, workspace_root=self.repo)
        entry: dict[str, str] = {"id": entry_id, "title": title, "description": description}
        if section == "skills":
            entry["instruction"] = instruction or ""
        remaining = [item for item in catalogue[section] if item["id"] != entry_id]
        catalogue[section] = sorted([*remaining, entry], key=lambda item: item["id"])
        write_role_catalog(path, catalogue, workspace_root=self.repo)
        return {"section": section, "entry": entry, "catalog_path": str(path)}

    def remove_catalog_entry(self, *, section: str, entry_id: str) -> dict[str, Any]:
        path = self._require_catalog_editing()
        if section not in CATALOG_SECTIONS:
            raise ValidationError(f"unknown catalogue section: {section}")
        # Removing something a role requires would make that role fail at its
        # next launch, far from the click that caused it.
        users = self._catalog_users(section, entry_id)
        if users:
            raise ValidationError(
                f"{entry_id} is required by active roles: " + ", ".join(users)
            )
        catalogue = load_role_catalog(path, workspace_root=self.repo)
        catalogue[section] = [item for item in catalogue[section] if item["id"] != entry_id]
        write_role_catalog(path, catalogue, workspace_root=self.repo)
        return {"section": section, "removed": entry_id, "catalog_path": str(path)}

    def search(
        self, *, query: str, limit: int = 25, subject_kind: str | None = None
    ) -> dict[str, Any]:
        """Search history. Read-only: synchronizing a projection records nothing."""

        return self.manager().search_history(query, limit=limit, subject_kind=subject_kind)

    def agent_proposals(self) -> list[dict[str, Any]]:
        """Open role proposals, readable whether or not this server writes."""

        return [bounded_copy(item) for item in self.manager().list_agent_proposals()]

    # -- writes ---------------------------------------------------------------

    def create_agent(self, *, from_preset_id: str | None = None, **fields: Any) -> dict[str, Any]:
        manager = self.writer()
        if from_preset_id:
            preset = manager.get_agent(from_preset_id)
            if not preset.get("template"):
                raise ValidationError("from_preset_id must name a role preset")
            for key in ("profile_id", "context_mode", "grants", "turnover_budget"):
                if not fields.get(key):
                    fields[key] = preset.get(key)
            for key in ("skills", "tool_allowlist"):
                if not fields.get(key):
                    fields[key] = tuple(preset.get(key) or ())
        return manager.create_agent(**fields)

    def approve_agent_proposal(self, *, thread_id: str, **fields: Any) -> dict[str, Any]:
        return self.writer().approve_agent_proposal(thread_id, **fields)

    def reconfigure_agent(self, *, agent_id: str, **fields: Any) -> dict[str, Any]:
        expected_revision = fields.pop("expected_revision")
        return self.writer().reconfigure_agent(agent_id, expected_revision, **fields)

    def retire_agent(self, *, agent_id: str, **fields: Any) -> dict[str, Any]:
        expected_revision = fields.pop("expected_revision", None)
        return self.writer().retire_agent(agent_id, expected_revision, **fields)

    def message_agent(
        self,
        *,
        agent_id: str,
        body_text: str,
        subject: str | None = None,
        thread_id: str | None = None,
        expected_revision: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """The human side of the panel: one durable message addressed to a role.

        Agents already talk to each other through delegation and the bounded
        communication channel, so this surface carries only human-in-the-loop
        traffic and lands in the canonical thread record either way.
        """

        manager = self.writer()
        if not body_text.strip():
            raise ValidationError("a message needs a body")
        if thread_id:
            if not expected_revision:
                raise ValidationError("replying to a thread requires its expected_revision")
            return manager.reply_thread(
                thread_id,
                expected_revision,
                body=body_text,
                idempotency_key=idempotency_key,
            )
        opened = manager.open_thread(
            thread_type="decision_request",
            subject=subject or truncate_utf8(body_text, 200),
            desired_outcome="the role acts on this direction",
            to=(agent_id,),
            related_refs=({"kind": "agent", "id": agent_id},),
            idempotency_key=idempotency_key,
        )
        # `thread.opened` carries no body, so the message itself is the first
        # reply.  Putting it in `desired_outcome` would make a directive look
        # like a resolution.
        return manager.reply_thread(
            opened["entity_ref"]["id"],
            opened["revision"],
            body=body_text,
            idempotency_key=f"{opened['idempotency_key']}:body",
        )

    def entity(self, kind: str, entity_id: str) -> Mapping[str, Any] | None:
        from agent_commons.views import bounded_copy

        collections = {
            "objective": "objectives",
            "task": "tasks",
            "thread": "threads",
            "review": "reviews",
            "verification": "verifications",
            "finding": "findings",
            "decision": "decisions",
            "artifact": "artifacts",
            "handoff": "handoffs",
            "delegation": "delegations",
            "agent": "agents",
            "agent_link": "agent_links",
        }
        if kind == "session":
            for session in self.manager().sessions.list_sessions():
                if session.session_id == entity_id:
                    return bounded_copy(
                        session.actor_context() | {"state": _session_state(session)}
                    )
            return None
        attribute = collections.get(kind)
        if attribute is None:
            return None
        record = getattr(self.manager().snapshot(), attribute).get(entity_id)
        return None if record is None else bounded_copy(record)
