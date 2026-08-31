"""Shared session, manager, and graph cache for the local UI.

Read models live in :mod:`agent_commons.ui.reads`; workflows live in
:mod:`agent_commons.ui.actions`; launch coordination lives in
:mod:`agent_commons.ui.launch`.  This compatibility facade keeps their existing
``UIContext`` call surface while owning only shared state.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_commons.config import CommonsPaths
from agent_commons.errors import CommonsError, ConfigurationError
from agent_commons.runtime import ContextBindingRequest
from agent_commons.services.manager import CommonsManager
from agent_commons.ui.actions import (
    MODEL_NAME_REFUSED,
    PREFLIGHT_CREDENTIAL_FREE,
    SETUP_SUPPORT_BINARY_UNRESOLVED,
    UIActions,
)
from agent_commons.ui.graph import build_graph
from agent_commons.ui.launch import (
    LAUNCH_NOT_CONFIGURED,
    LaunchRequest,
    LaunchResult,
    UILaunchCoordinator,
)
from agent_commons.ui.reads import UIReads, session_state

_LOG = logging.getLogger("agent_commons.ui")

__all__ = [
    "LAUNCH_NOT_CONFIGURED",
    "MODEL_NAME_REFUSED",
    "PANEL_ALREADY_OPEN",
    "PANEL_ALREADY_OPEN_ACTIONS",
    "PREFLIGHT_CREDENTIAL_FREE",
    "SETUP_SUPPORT_BINARY_UNRESOLVED",
    "UIContext",
    "ledger_fingerprint",
]

#: What the operator can do about a panel that lost the singleness race, said
#: once.  The refusal reaches the frontend from a non-GET route and `/api/meta`.
PANEL_ALREADY_OPEN_ACTIONS = ("use the panel that already serves this project",)

#: The frozen code the wave contract gives a panel that lost the race.
PANEL_ALREADY_OPEN = "panel_already_open"


def _iso_now() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


def _session_refusal_view(refusal: ConfigurationError | None) -> dict[str, Any] | None:
    """Render only frozen, actionable session refusals for `/api/meta`."""

    code = getattr(refusal, "code", None)
    if refusal is None or not code:
        return None
    details = getattr(refusal, "details", None)
    port = details.get("port") if isinstance(details, Mapping) else None
    return {
        "code": str(code),
        "message": str(refusal),
        "address": (
            f"127.0.0.1:{port}" if isinstance(port, int) and not isinstance(port, bool) else None
        ),
        "safe_next_actions": (
            list(PANEL_ALREADY_OPEN_ACTIONS) if str(code) == PANEL_ALREADY_OPEN else []
        ),
    }


def ledger_fingerprint(paths: CommonsPaths) -> str:
    """Cheap change detector over immutable ledger and operational run files."""

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
    # Sessions and runtime attempts are graph/operational state, not canonical
    # events.  Include both so an open panel sees an expired session and live
    # run-phase changes even when the immutable ledger remains quiet.
    for label, root in (
        ("sessions", paths.state_root / "sessions"),
        ("runtime", paths.state_root / "runtime"),
    ):
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            try:
                info = path.stat()
            except OSError:  # pragma: no cover - file vanished mid-scan
                continue
            digest.update(
                f"{label}/{path.relative_to(root)}\0{info.st_size}\0{info.st_mtime_ns}\n".encode()
            )
    return "sha256:" + digest.hexdigest()


class UIContext(UIReads, UIActions):
    """Compatibility facade for UI read/workflow adapters and the cached graph."""

    def __init__(
        self,
        repo: Path,
        *,
        state_root: Path | None = None,
        state_base: Path | None = None,
        state_source: str = "default",
        writer_session_id: str | None = None,
        session_provider: Callable[[], str] | None = None,
        session_owner: Any | None = None,
        catalog_path: Path | None = None,
        profile_config: Path | None = None,
        runtime_factory: Any | None = None,
        design_package_writes_enabled: bool = True,
    ) -> None:
        self.repo = repo
        self._state_root = state_root
        self._state_base = state_base
        self._state_source = state_source
        self._catalog_path = catalog_path
        self._profile_config = profile_config
        self._runtime_factory = runtime_factory
        self._design_package_writes_enabled = design_package_writes_enabled
        given = [
            item
            for item in (writer_session_id, session_provider, session_owner)
            if item is not None
        ]
        if len(given) > 1:
            raise TypeError("pass writer_session_id, session_provider, or session_owner, not two")
        self._writer_session_id = writer_session_id
        self._session_provider = session_provider
        self._session_owner = session_owner
        self._session_refusal: ConfigurationError | None = None
        self.server_instance_id = uuid.uuid4().hex
        self._guard = threading.RLock()
        self._seq = 0
        self._fingerprint = ""
        self._graph: dict[str, Any] | None = None
        # This operator-owned configuration is intentionally cached for the
        # server lifetime; a failed read is an honest empty answer, not a retry
        # on every catalogue poll.
        self._profile_info: dict[str, dict[str, Any]] | None = None
        self._launch_coordinator = UILaunchCoordinator(self)

    def await_launches(self, timeout: float = 30.0) -> None:
        """Join background launches through the dedicated coordinator."""

        self._launch_coordinator.await_launches(timeout=timeout)

    def run_role_on_task(
        self,
        *,
        agent_id: str,
        task_id: str,
        wall_time_seconds: int | None = None,
        idempotency_key: str | None = None,
        background: bool = True,
        context_pack_id: str | None = None,
        context_pack_revision: str | None = None,
    ) -> LaunchResult:
        """Delegate the existing launch call to the dedicated coordinator."""

        if (context_pack_id is None) != (context_pack_revision is None):
            raise ConfigurationError(
                "a Context Pack selection requires both its id and exact revision"
            )
        try:
            context = (
                ContextBindingRequest.fresh()
                if context_pack_id is None
                else ContextBindingRequest.accumulated(
                    context_pack_id=context_pack_id,
                    context_pack_revision=context_pack_revision,
                )
            )
        except ValueError as exc:
            raise ConfigurationError(
                "the Context Pack selection must use typed exact identifiers"
            ) from exc

        return self._launch_coordinator.run(
            LaunchRequest(
                agent_id=agent_id,
                task_id=task_id,
                wall_time_seconds=wall_time_seconds,
                idempotency_key=idempotency_key,
                background=background,
                context=context,
            )
        )

    @property
    def writer_session_id(self) -> str | None:
        """The current writer session, dynamically refreshed when an owner has one."""

        if self._session_owner is not None:
            return str(self._session_owner.ensure_active())
        if self._session_provider is not None:
            return self._session_provider()
        return self._writer_session_id

    @property
    def writer_session_ids(self) -> tuple[str, ...]:
        """All writer sessions in the owner lineage, current session last."""

        if self._session_owner is not None:
            self._session_owner.ensure_active()
            return tuple(str(item) for item in self._session_owner.session_ids())
        session_id = self.writer_session_id
        return (session_id,) if session_id is not None else ()

    def session_lineage(self) -> tuple[str, ...]:
        """The lineage the stream watches for an owner-recovered session."""

        if self._session_owner is not None:
            self._session_owner.refresh_liveness()
            return tuple(str(item) for item in self._session_owner.session_ids())
        if self._writer_session_id is not None:
            return (str(self._writer_session_id),)
        return ()

    @property
    def operator_panel(self) -> bool:
        """Whether this context may ever act, rather than merely render."""

        return (
            self._session_owner is not None
            or self._session_provider is not None
            or self._writer_session_id is not None
        )

    def session_or_refusal(self) -> tuple[str | None, ConfigurationError | None]:
        """Resolve a session and its typed refusal in one consistent read."""

        try:
            session_id = self.writer_session_id
        except ConfigurationError as exc:
            _LOG.debug("this panel cannot hold a session yet: %s", exc)
            self._session_refusal = exc
            return None, exc
        self._session_refusal = None
        return session_id, None

    @property
    def writes_enabled(self) -> bool:
        """Whether the panel can obtain an operator session right now."""

        return self.session_or_refusal()[0] is not None

    @property
    def session_refusal(self) -> ConfigurationError | None:
        """The typed reason from the most recent unsuccessful session check."""

        return self._session_refusal

    @property
    def catalog_editing_enabled(self) -> bool:
        """Whether a configured catalogue can be edited by this panel."""

        return self._catalog_path is not None and self.writes_enabled

    @property
    def launch_enabled(self) -> bool:
        """Whether this context has a writer and runtime configuration."""

        return self.writes_enabled and (
            self._profile_config is not None or self._runtime_factory is not None
        )

    def manager(self) -> CommonsManager:
        """Create the manager that backs every panel read."""

        return CommonsManager(
            self.repo,
            state_root=self._state_root,
            state_base=self._state_base,
            state_source=self._state_source,
            read_only=True,
            design_package_writes_enabled=self._design_package_writes_enabled,
        )

    def writer(self) -> CommonsManager:
        """Create the only manager every mutating panel workflow goes through."""

        session_id = self.writer_session_id
        if session_id is None:
            raise ConfigurationError(
                "this panel holds no operator session, so it records no canonical event"
            )
        return self._writer_bound(session_id)

    def _writer_bound(self, session_id: str) -> CommonsManager:
        """Create a writer over one already-resolved session id."""

        return CommonsManager(
            self.repo,
            session_id=session_id,
            state_root=self._state_root,
            state_base=self._state_base,
            state_source=self._state_source,
            design_package_writes_enabled=self._design_package_writes_enabled,
        )

    def invalidate(self) -> None:
        """Cause the next graph poll to rebuild its cache."""

        with self._guard:
            self._fingerprint = ""

    @property
    def seq(self) -> int:
        return self._seq

    def paths(self) -> CommonsPaths:
        return self.manager().paths

    def fingerprint(self) -> str:
        return ledger_fingerprint(self.paths())

    def _workspace_id(self) -> str | None:
        """Return the workspace id without making a first-run panel fail to boot."""

        try:
            return str(self.manager().workspace_id)
        except (CommonsError, OSError) as exc:
            _LOG.debug("no workspace to report on this panel yet: %s", exc)
            return None

    def meta(self) -> dict[str, Any]:
        """Boot facts that remain readable on a bare or occupied workspace."""

        from agent_commons import __version__
        from agent_commons.ui import META_SCHEMA, TRUST_NOTE, TRUTH_LAYERS

        session_id, refusal = self.session_or_refusal()
        return {
            "schema": META_SCHEMA,
            "agent_commons_version": __version__,
            "workspace_id": self._workspace_id(),
            "repo": str(self.repo),
            "read_only": session_id is None,
            "writes_enabled": session_id is not None,
            "writer_session_id": session_id,
            "session_refusal": _session_refusal_view(refusal),
            "server_instance_id": self.server_instance_id,
            "trust_note": TRUST_NOTE,
            "truth_layers": list(TRUTH_LAYERS),
        }

    def rebuild_graph(self) -> dict[str, Any]:
        """Rebuild and cache a graph against one stable fingerprint sample."""

        manager = self.manager()
        before = ledger_fingerprint(manager.paths)
        snapshot = manager.snapshot()
        sessions = [
            session.actor_context() | {"state": session_state(session)}
            for session in manager.sessions.list_sessions()
        ]
        after = ledger_fingerprint(manager.paths)
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
        """Return the stream sequence and graph from one consistent cache frame."""

        with self._guard:
            if self._graph is not None:
                return self._seq, self._graph
        graph = self.rebuild_graph()
        return int(graph["seq"]), graph

    def refresh_if_changed(self) -> bool:
        """Rebuild only if the graph cache's fingerprint has changed."""

        with self._guard:
            known = self._fingerprint
        if self.fingerprint() != known:
            self.rebuild_graph()
            return True
        return False
