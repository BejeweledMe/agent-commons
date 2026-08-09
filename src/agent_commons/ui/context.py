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

from agent_commons.config import CommonsPaths
from agent_commons.services.manager import CommonsManager
from agent_commons.ui.graph import build_graph


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
    ) -> None:
        self.repo = repo
        self._state_root = state_root
        self._state_base = state_base
        self._state_source = state_source
        self.server_instance_id = uuid.uuid4().hex
        # One poller runs per SSE connection, so sequence and graph are shared
        # mutable state across worker threads.
        self._guard = threading.RLock()
        self._seq = 0
        self._fingerprint = ""
        self._graph: dict[str, Any] | None = None

    def manager(self) -> CommonsManager:
        return CommonsManager(
            self.repo,
            state_root=self._state_root,
            state_base=self._state_base,
            state_source=self._state_source,
            read_only=True,
        )

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
            "read_only": True,
            "writes_enabled": False,
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
        fingerprint = before if before == after else ""
        graph = build_graph(
            snapshot,
            sessions=sessions,
            workspace_id=manager.workspace_id,
            generated_at=_iso_now(),
            ledger_fingerprint=before,
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
        with self._guard:
            return self._seq, graph

    def refresh_if_changed(self) -> bool:
        with self._guard:
            known = self._fingerprint
        if self.fingerprint() != known:
            self.rebuild_graph()
            return True
        return False

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
