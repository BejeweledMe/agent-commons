"""The panel's own operator session, owned for the life of one open project.

Until now the panel borrowed a session the shell had to open first
(`session start`, then `AGENT_COMMONS_SESSION_ID`).  Borrowing has two
structural faults: the panel holds no ownership nonce, so it cannot renew the
session it writes under, and a session opened yesterday is silently expired by
the time the first POST arrives.  This module makes the panel the owner: it
opens the session itself, keeps the nonce in process memory, renews it from a
daemon thread, and replaces an expired session with a fresh one under the same
identity.

One instance exists per *open project*, not per process: the future project
sidebar holds a ``dict[workspace_id, ProjectSessionOwner]`` and nothing here is
module-global.  The pattern -- a process opens a session, holds the nonce, and
closes it -- is the same one ``services/provider_canary.py`` and the delegation
runtime's child sessions already follow.

The session identity is a frozen contract (see ``panel_session_identity``).  It
deliberately contains no version, port, or pid: ``open_session`` deduplicates
only on a byte-identical identity, so any varying field would turn a panel
restart into a ``LifecycleConflictError`` instead of re-adopting the still
active session.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agent_commons.coordination.sessions import SessionRegistry
from agent_commons.errors import (
    CommonsError,
    ConfigurationError,
    LifecycleConflictError,
)
from agent_commons.platform_support import require_supported_platform
from agent_commons.services.manager import CommonsManager
from agent_commons.storage.opstate import (
    SESSION_STORAGE,
    ensure_private_directory,
    parse_timestamp,
)

try:  # pragma: no cover - import guarded exactly like platform_support
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - non-POSIX hosts are refused anyway
    _fcntl = None  # type: ignore[assignment]

_LOG = logging.getLogger("agent_commons.ui")

#: How often the daemon thread renews the session.  Fifteen minutes against an
#: eight-hour TTL means fifteen missed beats are survivable before expiry.
HEARTBEAT_INTERVAL_SECONDS = 15 * 60.0

#: Default TTL requested at open and at every routine renewal.
SESSION_TTL_SECONDS = 8 * 3600

#: The delegation runtime refuses to open a child unless the parent session
#: outlives ``wall_time + 60`` (`_open_child_session`).  The extra margin below
#: keeps the guarantee true through the launch window itself, so the check made
#: before ``create_delegation`` still holds when the child actually starts.
RUN_TTL_FINALIZATION_SECONDS = 60
RUN_TTL_MARGIN_SECONDS = 60

#: When the in-memory expiry is closer than this, ``ensure_active`` stops
#: trusting memory and renews through the registry.
_LIVENESS_MARGIN_SECONDS = 120.0

_PANEL_LOCK_SCHEMA = "agent_commons.ui.panel_lock.v1"


def panel_session_identity(workspace_id: str) -> dict[str, Any]:
    """The panel's session identity. Frozen; a change breaks every restart.

    ``open_session`` deduplicates only when every one of these fields matches
    byte for byte, so nothing volatile -- version, port, pid -- may ever appear
    here.  The test suite pins this shape as an eternal contract.
    """

    return {
        "stable_instance_id": f"agent-commons-ui-{workspace_id}",
        "principal": "local-operator",
        "client": "agent-commons",
        "software": "agent-commons-ui",
        "role": "operator",
        "capabilities": (),
        "model_family": None,
        "model": None,
        "source_producer": None,
    }


class PanelAlreadyOpenError(ConfigurationError):
    """A second panel refused to start because one already owns this project."""

    def __init__(self, port: int | None) -> None:
        where = f"127.0.0.1:{port}" if port is not None else "an unknown port"
        super().__init__(
            f"another panel already serves this project at {where}; use that window "
            "or stop it before starting a new one"
        )
        self.code = "panel_already_open"
        self.details = {"port": port}


class ProjectSessionOwner:
    """Opens, renews, repairs, and finally closes the panel's own session.

    All session state -- the id, the ownership nonce, the in-memory expiry --
    lives behind one lock.  The nonce never leaves this object: a heartbeat
    returns a replacement and it is swapped in under the same lock, so no
    caller can observe or race the rotation.
    """

    def __init__(
        self,
        repo: Path,
        *,
        state_root: Path | None = None,
        state_base: Path | None = None,
        state_source: str = "default",
        clock: Callable[[], float] = time.time,
        heartbeat_interval_seconds: float = HEARTBEAT_INTERVAL_SECONDS,
        session_ttl_seconds: int = SESSION_TTL_SECONDS,
    ) -> None:
        self.repo = repo
        self._configured_state_root = state_root
        self._configured_state_base = state_base
        self._configured_state_source = state_source
        self._clock = clock
        self._heartbeat_interval = float(heartbeat_interval_seconds)
        self._session_ttl = int(session_ttl_seconds)
        # One manager up front settles the workspace identity and the effective
        # state root, and fails at the terminal if the project is not usable.
        manager = self._manager()
        self.workspace_id = manager.workspace_id
        self._state_root = manager.paths.state_root
        # The registry is held directly rather than through the manager so the
        # clock is injectable: every expiry decision in this object and in the
        # store is then made against the same notion of now.
        self._registry = SessionRegistry(
            repo,
            state_root=self._state_root,
            policy=manager.policy,
            clock=clock,
        )
        self._guard = threading.RLock()
        self._session_id: str | None = None
        self._nonce: str | None = None
        self._expires_at = 0.0
        # Every id this owner has ever written under, oldest first, current
        # last.  Blocker answers are scoped to the session that asked, so a
        # recovered panel must still recognise questions addressed to its
        # previous selves; the panel filters by this whole set.
        self._session_ids: list[str] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._panel_lock_fd: int | None = None
        self._lock_refused = False

    # -- managers ----------------------------------------------------------

    def _manager(self, session_id: str | None = None) -> CommonsManager:
        return CommonsManager(
            self.repo,
            session_id=session_id,
            state_root=self._configured_state_root,
            state_base=self._configured_state_base,
            state_source=self._configured_state_source,
        )

    # -- session lifecycle -------------------------------------------------

    def start(self) -> str:
        """Open (or re-adopt) the session and start the renewal thread."""

        session_id = self.ensure_active()
        with self._guard:
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self._thread = threading.Thread(
                    target=self._heartbeat_loop,
                    name=f"agent-commons-ui-heartbeat-{self.workspace_id}",
                    daemon=True,
                )
                self._thread.start()
        return session_id

    def ensure_active(self) -> str:
        """The current session id, opening or repairing first when needed.

        Cheap by design: while the in-memory expiry says the session is
        comfortably alive this touches no file.  Once expiry is near -- or the
        laptop slept past it -- the renewal goes through the registry, and an
        expired session is replaced by a fresh one under the same identity
        (deduplication considers only active sessions).  This is what turns
        "the panel started with a stale session and dies on the first POST"
        into a repaired write.
        """

        with self._guard:
            if self._session_id is None:
                self._open_locked()
            elif self._clock() + _LIVENESS_MARGIN_SECONDS >= self._expires_at:
                self._renew_locked(self._session_ttl)
            assert self._session_id is not None
            return self._session_id

    def heartbeat_now(self) -> None:
        """Renew immediately: extend the TTL and rotate the nonce."""

        with self._guard:
            if self._session_id is None:
                self._open_locked()
                return
            self._renew_locked(self._session_ttl)

    def session_ids(self) -> tuple[str, ...]:
        """Every session id this owner has written under, current one last."""

        with self._guard:
            return tuple(self._session_ids)

    @property
    def session_id(self) -> str | None:
        with self._guard:
            return self._session_id

    def ensure_run_ttl(self, wall_time_seconds: int) -> str:
        """Guarantee the session outlives one run, renewing first if needed.

        The delegation runtime refuses a child session unless the parent's TTL
        covers ``wall_time + 60``; without this forced heartbeat *before*
        ``create_delegation`` the refusal arrives raw from the broker in a
        background thread, where only the log sees it.  When even a renewal
        cannot cover the run, the refusal raised here is a sentence a person
        can act on, not a broker internal.
        """

        needed = int(wall_time_seconds) + RUN_TTL_FINALIZATION_SECONDS + RUN_TTL_MARGIN_SECONDS
        with self._guard:
            if self._session_id is None:
                self._open_locked()
            remaining = self._expires_at - self._clock()
            if remaining < needed:
                try:
                    self._renew_locked(max(self._session_ttl, needed))
                except CommonsError as exc:
                    raise ConfigurationError(
                        "the panel could not extend its session to cover this run "
                        f"({int(wall_time_seconds)}s of wall time plus the finalization "
                        f"margin): {exc}. Close other windows using this session, or "
                        "restart the panel, then launch again."
                    ) from exc
                remaining = self._expires_at - self._clock()
            if remaining < needed:
                raise ConfigurationError(
                    f"the panel session expires in {int(remaining)}s but this run needs "
                    f"{needed}s ({int(wall_time_seconds)}s of wall time plus the "
                    "finalization margin); shorten the run's wall time and launch again"
                )
            assert self._session_id is not None
            return self._session_id

    def _open_locked(self) -> None:
        """Open the panel session, or re-adopt the active one, under the lock.

        Deduplication makes this idempotent: an active session under the same
        byte-identical identity comes back with its *current* nonce, which also
        heals a nonce this process lost track of.  An expired or closed session
        no longer participates in deduplication, so recovery is simply opening
        again.
        """

        previous = self._session_id
        session = self._registry.open_session(
            **panel_session_identity(self.workspace_id),
            ttl_seconds=self._session_ttl,
        )
        self._session_id = session.session_id
        self._nonce = session.nonce
        self._expires_at = parse_timestamp(session.expires_at)
        if session.session_id not in self._session_ids:
            self._session_ids.append(session.session_id)
        if previous is not None and previous != session.session_id:
            _LOG.warning(
                "panel session %s expired; recovered as %s under the same identity",
                previous,
                session.session_id,
            )

    def _renew_locked(self, ttl_seconds: int) -> None:
        """Heartbeat under the lock, repairing an expired session first."""

        assert self._session_id is not None
        try:
            session = self._registry.heartbeat(
                self._session_id,
                nonce=str(self._nonce),
                ttl_seconds=int(ttl_seconds),
            )
        except LifecycleConflictError:
            # Expired, closed, or the nonce diverged.  Reopening covers every
            # case: expiry and closure yield a fresh session, and an active
            # session with a diverged nonce is re-adopted with its current one.
            self._open_locked()
            session = self._registry.heartbeat(
                self._session_id,
                nonce=str(self._nonce),
                ttl_seconds=int(ttl_seconds),
            )
        self._session_id = session.session_id
        self._nonce = session.nonce
        self._expires_at = parse_timestamp(session.expires_at)

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self._heartbeat_interval):
            try:
                self.heartbeat_now()
            except CommonsError as exc:  # keep the thread alive; retry next beat
                _LOG.warning("panel session heartbeat failed: %s", exc)

    # -- single panel per project ------------------------------------------

    @property
    def panel_lock_path(self) -> Path:
        return self._state_root / "ui" / "panel.lock"

    def acquire_panel_lock(self, port: int) -> None:
        """Become the one panel for this project, or refuse and name the first.

        Deduplication would hand a second panel the same session *and the same
        nonce*; its first heartbeat would rotate that nonce out from under the
        first panel and its shutdown would close the shared session.  An
        exclusive lock file per project prevents the second panel from ever
        reaching that point, and the recorded port lets the refusal say where
        the existing panel already is.
        """

        require_supported_platform()
        assert _fcntl is not None
        with self._guard:
            path = self.panel_lock_path
            ensure_private_directory(path.parent, policy=SESSION_STORAGE)
            if path.is_symlink():
                raise ConfigurationError(f"panel lock must not be a symlink: {path}")
            if self._panel_lock_fd is not None:
                self._write_panel_lock_locked(port)
                return
            flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags, 0o600)
            try:
                _fcntl.flock(descriptor, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            except OSError:
                holder_port = self._read_panel_lock_port(descriptor)
                os.close(descriptor)
                self._lock_refused = True
                raise PanelAlreadyOpenError(holder_port) from None
            os.fchmod(descriptor, 0o600)
            self._panel_lock_fd = descriptor
            self._lock_refused = False
            self._write_panel_lock_locked(port)

    def _write_panel_lock_locked(self, port: int) -> None:
        assert self._panel_lock_fd is not None
        body = json.dumps(
            {"schema": _PANEL_LOCK_SCHEMA, "port": int(port), "pid": os.getpid()},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        os.ftruncate(self._panel_lock_fd, 0)
        os.lseek(self._panel_lock_fd, 0, os.SEEK_SET)
        os.write(self._panel_lock_fd, (body + "\n").encode("utf-8"))

    @staticmethod
    def _read_panel_lock_port(descriptor: int) -> int | None:
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            raw = os.read(descriptor, 4096)
            value = json.loads(raw.decode("utf-8"))
            port = value.get("port")
            return int(port) if isinstance(port, int) else None
        except (OSError, ValueError, AttributeError):
            return None

    def _release_panel_lock_locked(self) -> None:
        if self._panel_lock_fd is None:
            return
        try:
            assert _fcntl is not None
            _fcntl.flock(self._panel_lock_fd, _fcntl.LOCK_UN)
        finally:
            os.close(self._panel_lock_fd)
            self._panel_lock_fd = None

    # -- shutdown ----------------------------------------------------------

    def shutdown(self) -> dict[str, Any]:
        """Stop renewing and close the session unless live work still needs it.

        ``end_session`` itself refuses while the session owns non-terminal
        delegations, and that refusal is deliberate: closing the session under
        a live run would orphan it.  The outcome names the reason so the caller
        can print why the session was intentionally left open.
        """

        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5.0)
            self._thread = None
        outcome: dict[str, Any] = {"session_id": None, "closed": False, "reason": None}
        with self._guard:
            if self._session_id is not None:
                outcome["session_id"] = self._session_id
                if self._lock_refused:
                    # Another panel owns this project; the session (and nonce)
                    # are shared with it and must be left strictly alone.
                    outcome["reason"] = (
                        "another panel owns this project's session; leaving it untouched"
                    )
                else:
                    try:
                        self._manager(self._session_id).end_session(nonce=str(self._nonce))
                        outcome["closed"] = True
                        self._session_id = None
                        self._nonce = None
                        self._expires_at = 0.0
                    except LifecycleConflictError as exc:
                        # Live delegations keep the session open on purpose; a
                        # session already expired needs no closing either way.
                        outcome["reason"] = str(exc)
                        _LOG.warning("panel session left open: %s", exc)
                    except CommonsError as exc:  # pragma: no cover - defence
                        outcome["reason"] = str(exc)
                        _LOG.warning("panel session could not be closed: %s", exc)
            self._release_panel_lock_locked()
        return outcome
