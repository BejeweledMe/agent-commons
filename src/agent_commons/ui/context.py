"""Read-only workspace access for the local UI.

The manager is always constructed read-only.  That is the mechanism behind the
"this server records no canonical event" guarantee, not a convention layered on
top of a writable manager.
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

from agent_commons.catalog import CATALOG_SECTIONS, load_role_catalog, write_role_catalog
from agent_commons.config import CommonsPaths
from agent_commons.domain.agents import PROFILE_NARROWING
from agent_commons.domain.attention import awaits_human
from agent_commons.domain.collections import collection_for
from agent_commons.errors import (
    CommonsError,
    ConfigurationError,
    LifecycleConflictError,
    ValidationError,
)
from agent_commons.runtime.model import (
    BuiltinProfileId,
    profile_tool_summary,
    validate_model_name,
)
from agent_commons.services.manager import CommonsManager
from agent_commons.services.roles import role_model
from agent_commons.ui.graph import build_graph
from agent_commons.views import bounded_copy, truncate_utf8

_LOG = logging.getLogger("agent_commons.ui")

#: The one wording for "this panel cannot launch yet", shared by the direct-call
#: refusal here and the typed ``launch_not_configured`` HTTP refusal in
#: `ui.server`, so the two can never drift into two explanations of one state.
LAUNCH_NOT_CONFIGURED = (
    "no runtime environment is configured for this panel: launching a provider "
    "needs an operator session and an operator profile config"
)

#: A provider was resolved but one of the executables every profile needs beside
#: it was not.  The wave contract froze this code after `ui.setup` found the
#: state reachable and refused to invent a code for it: that module may not
#: extend the frozen table, so the binding lives here, on the wiring that turns
#: its answer into an HTTP refusal the first-run screen can draw.
SETUP_SUPPORT_BINARY_UNRESOLVED = "setup_support_binary_unresolved"

#: What the operator can do about a panel that lost the singleness race, said
#: once.  The refusal reaches the frontend from two directions -- a non-GET
#: route refused before its body is read, and `/api/meta` on the very first
#: load -- and the two must not grow into two pieces of advice for one state.
PANEL_ALREADY_OPEN_ACTIONS = ("use the panel that already serves this project",)

#: The frozen code the wave contract gives that state.  Named here rather than
#: imported from `ui.session_owner` so this module keeps no import edge to it;
#: the class that raises it owns the string and this is checked against it.
PANEL_ALREADY_OPEN = "panel_already_open"

#: What a preflight result means, said once.  `preflight_profile` checks fixed
#: argv and MCP startup and carries no credential at all, so a signed-out
#: provider and a working one are indistinguishable to it
#: (decision.5QPR0HQYNAG3XKBMMKBJCAG1RB).  A green preflight is therefore a
#: statement about structure, never about authorization.
PREFLIGHT_CREDENTIAL_FREE = (
    "this check is structural: it verifies the fixed launch flags and the MCP "
    "handshake without any credential, so it cannot tell a signed-out provider "
    "from an authorized one -- a green result does not mean you are logged in"
)


def _iso_now() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


def _elapsed_seconds(started_at: Any, ended_at: Any) -> float | None:
    """Seconds between two attempt timestamps, or None when that is not honest.

    The attempt store writes ISO-8601 strings, but this surface reads a file it
    does not own: a record written by an older build, a foreign clock, or a
    half-flushed write can carry something else.  A timestamp that will not
    parse, or an interval that runs backwards, is not a duration — it is an
    unknown, and the panel is better off showing nothing than a number nobody
    can explain.  Never raises: a display field must not take the run list down.
    """

    try:
        start = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(ended_at).replace("Z", "+00:00"))
        elapsed = (end - start).total_seconds()
    except (TypeError, ValueError, OverflowError):
        return None
    return elapsed if elapsed >= 0 else None


def _session_refusal_view(refusal: ConfigurationError | None) -> dict[str, Any] | None:
    """How `/api/meta` reports a refusal it survived, or None when there is one.

    Only a refusal carrying one of the wave's frozen codes is reported: the
    frontend draws by code, and a refusal with no code is something it could
    only render as raw text.  The uncoded ones all mean "this directory is not
    set up yet", and `GET /api/setup` answers that question properly, so
    silence here sends the tab to the surface that can actually explain it.

    ``address`` is the one field the operator needs and cannot derive: which
    window is the live one.  It is host and port and deliberately not a URL --
    each panel's bearer token lives only in its own tab's fragment, so a link
    built here would open a page that cannot authorize itself and would look
    broken rather than occupied.
    """

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
    # Sessions and runtime attempts are graph/operational state, not ledger
    # events, so a ledger-only fingerprint would leave a closed session on screen
    # forever and a launched run's live phase (launching -> running -> terminal)
    # invisible between canonical events.  Fold both into the change detector so
    # the panel refreshes as a run progresses (MUST-5).
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
        session_provider: Callable[[], str] | None = None,
        session_owner: Any | None = None,
        catalog_path: Path | None = None,
        profile_config: Path | None = None,
        runtime_factory: Any | None = None,
    ) -> None:
        self.repo = repo
        self._state_root = state_root
        self._state_base = state_base
        self._state_source = state_source
        # The operator catalogue is read in any mode -- a read-only panel still
        # shows what the roles were built from -- and edited only by a panel
        # that has a session, which is what `catalog_editing_enabled` answers.
        self._catalog_path = catalog_path
        # Launching a provider spawns a billable subscription process, and it
        # needs the operator profile config, exactly like the CLI broker.
        self._profile_config = profile_config
        # Tests inject a runtime service built over a fake runner here; in
        # production it is None and the service is built from the profile config.
        self._runtime_factory = runtime_factory
        # A writable context is opt-in and needs a real operator session, the
        # same identity the CLI writes under.  Absent one, this stays the
        # read-only server it has always been.
        #
        # Three ways in, and exactly one may be used.  A caller that already
        # holds a resolved session id passes it and it never changes; a caller
        # that owns the session's whole lifetime passes a provider or the owner
        # object itself, because the id is then not a constant -- a session that
        # outlives its TTL is replaced by a fresh one under the same identity,
        # and every reader has to see the replacement without anybody
        # remembering to re-assign a field.  The owner additionally carries the
        # whole session lineage (for blocker answers scoped to a previous
        # session) and the pre-launch TTL guarantee.  Passing more than one is a
        # programmer error, not an operator one: there would be no honest rule
        # for which wins.
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
        # The typed reason `writes_enabled` last answered False, when there was
        # one.  Kept so the request path can tell "no workspace yet" apart from
        # "another panel owns this project" instead of collapsing both into the
        # first-run refusal.
        self._session_refusal: ConfigurationError | None = None
        self.server_instance_id = uuid.uuid4().hex
        # One poller runs per SSE connection, so sequence and graph are shared
        # mutable state across worker threads.
        self._guard = threading.RLock()
        self._seq = 0
        self._fingerprint = ""
        self._graph: dict[str, Any] | None = None
        # Operator profile config is read once and kept here, next to the graph
        # it is served beside.  It is a file outside the workspace that only the
        # operator can change, and unlike the ledger nothing in a session is
        # expected to move it -- so re-reading it per request would buy a
        # syscall on the hot catalogue path and almost never a new answer.  The
        # cost is that an edit made while the server runs is not picked up until
        # it restarts, and so is a read that failed; `profile_info` says why
        # that trade is the right way round for this field.
        self._profile_info: dict[str, dict[str, Any]] | None = None
        # Background launch threads, kept so a test can await them; daemon so
        # they never hold the server open.
        self._launch_threads: list[threading.Thread] = []

    def await_launches(self, timeout: float = 30.0) -> None:
        """Join any background launch threads. For tests and clean shutdown."""

        for thread in list(self._launch_threads):
            thread.join(timeout=timeout)

    @property
    def writer_session_id(self) -> str | None:
        """The session this panel records under, or None when it is read-only.

        Computed rather than stored, so that an owner able to replace the
        session behind it is not obliged to reach into this object.  Every read
        goes through here; nothing caches the answer.  A caller that needs two
        consistent looks at it -- a check and then a use -- must read it once
        into a local, because a provider is free to hand back a different id
        between two calls.
        """

        if self._session_owner is not None:
            # `ensure_active` is a cheap in-memory liveness check that only
            # touches the registry -- and repairs, replacing an expired session
            # with a fresh one under the same identity -- when expiry is near.
            # Routing every read through it is what moved "is the session
            # alive" from once at startup to before every write.
            return str(self._session_owner.ensure_active())
        if self._session_provider is not None:
            return self._session_provider()
        return self._writer_session_id

    @property
    def writer_session_ids(self) -> tuple[str, ...]:
        """Every session id this panel has written under, the current one last.

        Blocker answers are scoped to the session that asked the question, and
        those scopes are immutable.  A panel whose session expired and was
        recovered must still recognise requests addressed to its previous
        sessions, so filters compare against this whole set rather than the
        single current id.
        """

        if self._session_owner is not None:
            self._session_owner.ensure_active()
            return tuple(str(item) for item in self._session_owner.session_ids())
        session_id = self.writer_session_id
        return (session_id,) if session_id is not None else ()

    def session_lineage(self) -> tuple[str, ...]:
        """The lineage the stream watches for a replaced session, kept fresh.

        The already-open tab fetched ``/api/meta`` exactly once, at boot, and
        compares nodes against that cached ``writer_session_id`` forever; the
        stream is the only channel it keeps reading.  This is what the stream
        polls: for an owner the liveness is refreshed first, so an expiry the
        laptop slept through is noticed within one poll interval instead of at
        the next write or the next quarter-hourly heartbeat.  A context bound
        to a fixed id has a lineage of one that can never grow, and a
        read-only panel has none: neither ever reports a recovery.
        """

        if self._session_owner is not None:
            self._session_owner.refresh_liveness()
            return tuple(str(item) for item in self._session_owner.session_ids())
        if self._writer_session_id is not None:
            return (str(self._writer_session_id),)
        return ()

    @property
    def operator_panel(self) -> bool:
        """Whether this panel acts at all, rather than only shows.

        Structural and settled at construction: a panel either was handed the
        means to hold an operator session -- an owner, a provider, or an
        already-resolved id -- or it was not, and nothing that happens while it
        serves can change the answer.  This is the *only* thing the route table
        is built from, because FastAPI builds that table once while every other
        piece of state the panel has (a workspace, an operator config, a
        catalogue) can now appear while it is already serving.

        Deliberately not "a session can be obtained": obtaining one needs a
        workspace, and creating the workspace is itself one of the routes.
        """

        return (
            self._session_owner is not None
            or self._session_provider is not None
            or self._writer_session_id is not None
        )

    def session_or_refusal(self) -> tuple[str | None, ConfigurationError | None]:
        """The session this panel can write under *right now*, and why not.

        One answer, not two reads.  Whether writes are possible and the typed
        reason they are not are the same fact seen from two sides, and every
        caller needs both: asking `writes_enabled` and then `session_refusal`
        is two looks at a value a session owner is free to change between them,
        so a concurrent caller could pair "no session" with somebody else's
        reason -- or with none at all.  Callers that need the pair consistent
        take it from here; the two properties below remain for callers that
        genuinely want only one half.

        Never raises.  A panel opened on a directory that is not a workspace
        yet is an operator panel that cannot obtain a session -- the owner
        cannot resolve a workspace that does not exist -- and it becomes one
        that can the moment `POST /api/setup/initialize` returns, in the same
        process.  A refusal from the session machinery is therefore an answer
        here rather than something to propagate: raising out of something
        handlers consult as a precondition would turn "not set up yet", or
        "another panel owns this project", into a 500.
        """

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
        """Whether a session can be obtained *right now*. Never raises.

        This is the executable half of the pair whose structural half is
        `operator_panel`: handlers read it, the route table does not.
        """

        return self.session_or_refusal()[0] is not None

    @property
    def session_refusal(self) -> ConfigurationError | None:
        """The typed refusal behind the last False from ``writes_enabled``.

        Most of the time the reason is the obvious one -- no workspace yet --
        and the caller's own state check already names it.  The exception is a
        panel that lost the singleness race while its lock was deferred: its
        refusal is ``PanelAlreadyOpenError``, and reporting that panel as
        "not set up yet" sends the operator to a first-run screen that cannot
        help.  Handlers read this to keep that refusal under its own name.

        A caller that also needs the session id must use `session_or_refusal`
        instead: these two properties are separate reads and nothing keeps
        them describing the same instant.
        """

        return self._session_refusal

    @property
    def catalog_editing_enabled(self) -> bool:
        """Whether this panel may write the operator catalogue back to disk.

        Two conditions, and both are states rather than switches: there has to
        be a catalogue file to edit, and the panel has to be one that acts at
        all.  A panel without a session shows the catalogue and changes nothing
        -- that is what read-only means.

        Like `launch_enabled`, this decides whether an edit is *permitted*, not
        whether the route exists.  The generated runtime config names a
        catalogue beside itself and the panel adopts both while it is already
        serving, so a route table built from this would answer 404 to the very
        editing the first-run screen had just switched on.  `POST` is refused by
        `_require_catalog_editing`, which says which half is missing.
        """

        return self._catalog_path is not None and self.writes_enabled

    @property
    def launch_enabled(self) -> bool:
        # Launch needs writes (a real operator session records the delegation)
        # and either a profile config to build the runtime from or an injected
        # runtime factory (tests).  Like every other capability here this one
        # does not decide whether a route exists: the operator config can appear
        # while the panel is already serving, so the route is always there for
        # an operator panel and refuses by `launch_not_configured` until this is
        # true.
        return self.writes_enabled and (
            self._profile_config is not None or self._runtime_factory is not None
        )

    # -- first run ------------------------------------------------------------
    #
    # Everything below is the wiring between `ui.setup` and the panel's HTTP
    # surface.  It sequences that module and names its refusals; it repeats none
    # of its checks, because a second copy of "is this executable safe to run"
    # is exactly how the two answers start to disagree.

    @staticmethod
    def _unresolved_support_binaries(discovery: Any) -> tuple[str, ...]:
        """Support executables every generated profile needs, that did not resolve.

        Named off the discovery the module already produced rather than probed
        again: this is a classification of its answer, not a second opinion.
        """

        return tuple(probe.name for probe in (discovery.mcp, discovery.git) if not probe.found)

    def setup_status(self) -> dict[str, Any]:
        """What the first-run screen needs, and the one place paths leave here.

        The panel hands out no filesystem path anywhere else, and this is the
        narrow, deliberate exception: while the runtime is still unconfigured
        the operator has to see which binaries were found and where the config
        is about to be written, because that is the decision being asked of
        them.  The moment the state is `configured` the exception closes and the
        same route answers with the state and nothing else -- the paths are not
        redacted, they are not gathered at all.
        """

        from agent_commons.ui import setup

        state = setup.setup_state(self.repo, profile_config=self._profile_config)
        status: dict[str, Any] = {
            "state": state,
            "launch_enabled": self.launch_enabled,
            "catalog_editing_enabled": self.catalog_editing_enabled,
        }
        if state == setup.SETUP_CONFIGURED:
            return status
        discovery = setup.discover_providers(self.repo)
        missing = self._unresolved_support_binaries(discovery)
        status["providers"] = discovery.describe()
        status["providers_found"] = list(discovery.providers_found)
        status["providers_missing"] = list(discovery.providers_missing)
        status["support_missing"] = list(missing)
        status["config_path"] = str(self._profile_config or setup.default_runtime_config_path())
        # What would refuse a write attempted right now, said before the
        # operator clicks rather than only after: no provider at all is the
        # frozen `setup_no_provider_found`, and a provider without the
        # executables every profile needs beside it is the code this wave added.
        status["blocking_refusal"] = (
            setup.SETUP_NO_PROVIDER_FOUND
            if not discovery.providers_found
            else (SETUP_SUPPORT_BINARY_UNRESOLVED if missing else None)
        )
        # Demo is one of the exits from every unconfigured state, and the only
        # one when no provider resolves at all -- without naming it here, the
        # person `setup_no_provider_found` stops would be left with "write the
        # YAML by hand", the exact terminal step this panel exists to remove.
        # Said explicitly so the screen offers it rather than deriving it.
        status["demo_available"] = True
        return status

    def initialize_workspace(self) -> dict[str, Any]:
        """Create the workspace in this directory, through the code `init` runs.

        `CommonsManager.initialize` is the same entry point the CLI command
        calls; there is no second initializer.  The report is trimmed to what
        the screen can use, because the full one lists every file written and
        this surface does not publish paths.
        """

        from agent_commons.ui import setup

        if setup.setup_state(self.repo) == setup.SETUP_NOT_A_REPOSITORY:
            raise setup.SetupError(
                setup.SETUP_NOT_A_REPOSITORY,
                "this directory is not a git repository, so there is nothing for "
                "a workspace to attach to",
            )
        report = CommonsManager.initialize(self.repo, integrations=("codex", "claude"))
        self.invalidate()
        return {
            "workspace_id": report["workspace_id"],
            "integrations": list(report["integrations"]),
            "changed": bool(report["changed"]),
            "state": setup.setup_state(self.repo, profile_config=self._profile_config),
        }

    def configure_runtime(self) -> dict[str, Any]:
        """Generate the operator runtime config and adopt it without a restart.

        Discovery runs once and is handed to the generator, so the profiles that
        get written are the ones the operator was just shown rather than the
        result of a second probe that could answer differently.
        """

        from agent_commons.ui import setup

        discovery = setup.discover_providers(self.repo)
        missing = self._unresolved_support_binaries(discovery)
        if discovery.providers_found and missing:
            # `ui.setup` raises an uncoded ConfigurationError here on purpose --
            # it may not extend the frozen refusal table by itself -- and keeps
            # every resolver reason in the message.  This is where the frozen
            # code gets attached, with those reasons carried through.
            reasons = "; ".join(
                f"{probe.name} [{refusal.candidate}]: {refusal.reason}"
                for probe in (discovery.mcp, discovery.git)
                if not probe.found
                for refusal in probe.refusals
            )
            raise setup.SetupError(
                SETUP_SUPPORT_BINARY_UNRESOLVED,
                "a provider was found but "
                + " and ".join(missing)
                + " could not be resolved, and every generated profile names both"
                + (f": {reasons}" if reasons else ""),
                details={"missing": list(missing), "discovery": discovery.describe()},
            )
        written = setup.generate_runtime_config(
            self.repo,
            state_root=self._state_root,
            state_base=self._state_base,
            discovery=discovery,
        )
        adopted = self.adopt_runtime_config(written["path"])
        # The written path is deliberately not echoed: this panel is configured
        # from here on, and `setup_status` stops naming paths at the same moment.
        return {
            "state": setup.SETUP_CONFIGURED,
            "providers_found": written["providers_found"],
            "providers_missing": written["providers_missing"],
            **adopted,
        }

    def configure_demo_runtime(self) -> dict[str, Any]:
        """Write the demo runtime config and adopt it; no provider is needed.

        The separate named operation the wave decision froze: demo is its own
        parameterless route, never a flag on the real write, so "no setup
        route accepts a parameter" stays true and a bearer of the panel token
        still cannot choose a mode.  No discovery runs here at all --
        requiring nothing resolvable is this operation's single purpose, and
        the machine it exists for is exactly the one `setup_no_provider_found`
        stops everywhere else.
        """

        from agent_commons.ui import setup

        written = setup.generate_demo_runtime_config(
            self.repo,
            state_root=self._state_root,
            state_base=self._state_base,
        )
        adopted = self.adopt_runtime_config(written["path"])
        return {"state": setup.SETUP_CONFIGURED, **adopted}

    def adopt_runtime_config(self, path: str | Path) -> dict[str, Any]:
        """Take a runtime config into a panel that is already serving.

        This is the whole point of writing the config from the panel: a first
        run that ends in "now restart me" is not a first run that worked.  Three
        pieces of state have to move together, and each was a trap on its own:

        - `_profile_config`, which is what `launch_enabled` and the runtime
          service read;
        - `_profile_info`, which is cached for the life of the process by
          design.  A panel that adopted a config without dropping it would keep
          answering "the model is fixed in the profile" forever, because the
          cache was filled from the *unconfigured* read;
        - `_catalog_path`, which the generated config names beside itself.

        Loaded through the same guarded loader the launch path uses, so a config
        this panel adopts is exactly one a launch would accept, and a refusal
        arrives here instead of at the operator's first Run.
        """

        from agent_commons.services.delegation_runtime import load_runtime_configuration

        target = Path(path).expanduser()
        configuration = load_runtime_configuration(target, workspace_root=self.repo)
        with self._guard:
            self._profile_config = target
            self._profile_info = None
            if configuration.catalog_path is not None:
                self._catalog_path = configuration.catalog_path
        # The graph is unchanged, but everything derived beside it -- the
        # catalogue, the launch options -- is not, and the panel polls for that
        # through the same fingerprint.
        self.invalidate()
        return {
            "profiles": [str(profile_id) for profile_id in configuration.profiles.profile_ids],
            "demo": configuration.demo,
            "launch_enabled": self.launch_enabled,
            "catalog_editing_enabled": self.catalog_editing_enabled,
        }

    def setup_preflight(self) -> dict[str, Any]:
        """Run the credential-free compatibility check over configured profiles.

        `preflight_profile` debuts in the panel here.  It starts provider `--help`
        and MCP handshake processes and consumes no delegation attempt; what it
        cannot do is tell an unauthorized provider from an authorized one, and
        the answer says so in every response rather than only in a tooltip.
        """

        from agent_commons.runtime.preflight import preflight_profile
        from agent_commons.services.delegation_runtime import load_runtime_configuration
        from agent_commons.ui import setup

        if self._profile_config is None:
            raise setup.SetupError(
                setup.SETUP_UNCONFIGURED,
                "there is no operator runtime config to check yet; the check runs "
                "against the profiles the first-run screen writes",
            )
        configuration = load_runtime_configuration(self._profile_config, workspace_root=self.repo)
        state_root = self.paths().state_root
        results = [
            preflight_profile(
                configuration.profiles,
                profile_id,
                workspace_root=self.repo,
                state_root=state_root,
            )
            for profile_id in configuration.profiles.profile_ids
        ]
        return {
            "credential_free": True,
            "note": PREFLIGHT_CREDENTIAL_FREE,
            "ok": all(bool(result["ok"]) for result in results),
            "profiles": results,
        }

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

        session_id = self.writer_session_id
        if session_id is None:
            raise ConfigurationError(
                "this panel holds no operator session, so it records no canonical event"
            )
        return self._writer_bound(session_id)

    def _writer_bound(self, session_id: str) -> CommonsManager:
        """A writer manager over one already-resolved session id.

        Split from :meth:`writer` so a caller that needs the id *and* the
        manager reads the (possibly rotating) session exactly once and binds
        both to the same value.
        """

        return CommonsManager(
            self.repo,
            session_id=session_id,
            state_root=self._state_root,
            state_base=self._state_base,
            state_source=self._state_source,
        )

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

    def _workspace_id(self) -> str | None:
        """This project's workspace id, or None when there is no workspace yet.

        Never raises.  A panel is now expected to serve a directory that is not
        a workspace at all -- that is the whole first-run screen -- and the tab
        fetches `/api/meta` before anything else, so an exception here is a tab
        that never loads and an operator with no way in but the terminal.
        Which state this actually is remains `GET /api/setup`'s question; the
        honest answer here is that there is no id to report.
        """

        try:
            return str(self.manager().workspace_id)
        except (CommonsError, OSError) as exc:
            _LOG.debug("no workspace to report on this panel yet: %s", exc)
            return None

    def meta(self) -> dict[str, Any]:
        """Boot facts for a tab, answerable in every state the panel can be in.

        This is the first request a tab makes and the tab renders nothing until
        it returns, so it must not fail for anything the panel is designed to
        survive.  Two such states used to raise straight out of here into a
        500: a directory with no workspace yet, and a panel that lost the
        deferred singleness race, whose session read raises
        ``PanelAlreadyOpenError``.  The second one is the worse of the two --
        the operator's second window would go blank with no way to learn that
        the first window is the live one, or where it is.

        Both are now reported rather than raised.  A refusal that carries a
        frozen code travels in ``session_refusal``; one that does not is left
        as an absence, because inventing a verdict here would put a second
        first-run state machine beside `GET /api/setup`, which owns that
        question.
        """

        from agent_commons import __version__
        from agent_commons.ui import META_SCHEMA, TRUST_NOTE, TRUTH_LAYERS

        # One look at the session, four fields derived from it: read twice and
        # a panel could be told it is writable and given no id to write as.
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

    def profile_info(self) -> dict[str, dict[str, Any]]:
        """Which provider and which model each profile would run -- nothing else.

        A role selects a profile and never names a model, so the only honest way
        to answer "what will actually run?" is to read the operator's own
        profile config.  Two fields leave here and no more: a profile body also
        carries the executable path, the sandbox or permission mode, and the
        argv a launch is built from, and every one of those tells a bearer of
        this token something about the operator's machine and about the
        narrowing that protects it.  Provider and model answer the question the
        panel asks; the rest stays where it is configured.

        Read through the same guarded loader the launch path uses, so a config
        this surface would accept is exactly one a launch would accept.
        `launch_enabled` is deliberately not consulted: permission to spawn a
        billable process and permission to say which model a profile names are
        different privileges, and the config is operator-owned either way.

        Never raises.  A missing, unreadable, or malformed config makes this
        empty, and the panel then says the model is fixed in the profile rather
        than inventing a name -- an unreadable operator file is a reason to know
        less, not a reason to fail a read-only catalogue request.

        A refusal is cached exactly like a success, and that is a real cost
        stated rather than hidden: a transient failure -- a read racing the
        operator's own save -- leaves the panel saying "fixed in the profile"
        until the server restarts.  It is the trade this surface wants.  The
        alternative is retrying on every catalogue request, which turns a
        misconfigured or hostile path into a syscall per poll, and the degraded
        answer is the truthful one either way: the panel knows no model, and
        says so, instead of naming one it could not read.
        """

        with self._guard:
            cached = self._profile_info
        if cached is not None:
            return cached

        from agent_commons.services.delegation_runtime import load_runtime_configuration

        summary: dict[str, dict[str, Any]] = {}
        try:
            config = load_runtime_configuration(self._profile_config, workspace_root=self.repo)
            for profile_id in config.profiles.profile_ids:
                profile = config.profiles.get(profile_id)
                model = getattr(profile, "model", None)
                summary[str(profile_id)] = {
                    "provider": str(profile.provider),
                    "model": str(model) if model is not None else None,
                }
        except (CommonsError, OSError, ValueError) as exc:
            # The detail names an operator path and the reason it was refused;
            # that belongs in the operator's own log, not in a response.
            _LOG.warning("profile config not readable, serving no profile detail: %s", exc)
            summary = {}
        with self._guard:
            self._profile_info = summary
        return summary

    def model_options(self, snapshot: Any) -> dict[str, list[str]]:
        """Models the hire form may offer, per provider, from honest sources only.

        The panel may not invent a model name -- a name it made up would be a
        name that is not the one that runs, and the frontend asset is pinned
        against carrying any.  So every string here comes from somewhere this
        project can actually point at:

        - the model each configured profile names, which is what a launch would
          use anyway;
        - the model each active role was already hired on, so the second role
          on a project is a pick from a list rather than retyping.

        Neither source is a claim that a name is valid *now*: a model can be
        retired by its provider between the config being written and this
        request, and this surface has no way to ask.  It is a list of what this
        machine and this project have already chosen, which is why the field
        stays free text with this offered beside it rather than a closed
        select.  Every candidate is re-validated on the way out: these arrive
        from an operator file and from replayed events, and a name that could
        not survive a launch has no business being offered for one.

        A key per provider, always present and possibly empty, so the form can
        look one up by the selected profile's provider without knowing which
        providers exist.
        """

        options: dict[str, set[str]] = {
            profile_id.provider.value: set() for profile_id in BuiltinProfileId
        }

        def offer(provider: str | None, model: Any) -> None:
            if provider not in options or not isinstance(model, str):
                return
            try:
                validated = validate_model_name(model)
            except ValidationError:
                # An operator file or a replayed event named something no
                # launch would accept.  Dropping it silently is right: the
                # catalogue is a list of offers, and this is not one.
                _LOG.debug("a recorded model name is not offerable: %r", model[:64])
                return
            if validated is not None:
                options[provider].add(validated)

        for info in self.profile_info().values():
            offer(str(info.get("provider")), info.get("model"))
        for record in snapshot.agents.values():
            if record.get("state") != "active":
                continue
            try:
                provider = BuiltinProfileId(str(record.get("profile_id"))).provider.value
            except ValueError:
                # A role recorded against a profile this build does not know.
                continue
            offer(provider, role_model(record))
        return {provider: sorted(models) for provider, models in sorted(options.items())}

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
        # Annotate each entry with the active roles that hold it: the card can
        # then say "used by N" and removal can be explained before the click.
        for section in CATALOG_SECTIONS:
            field = "skills" if section == "skills" else "tool_allowlist"
            for entry in catalogue.get(section) or []:
                entry["users"] = sorted(
                    str(record["id"])
                    for record in snapshot.agents.values()
                    if record.get("state") == "active"
                    and entry.get("id") in (record.get(field) or ())
                )
        editable = ["presets"] + (list(CATALOG_SECTIONS) if self.catalog_editing_enabled else [])
        return {
            "schema": CATALOG_SCHEMA,
            "editable_here": editable,
            "operator_owned": ["profiles"]
            + ([] if self.catalog_editing_enabled else list(CATALOG_SECTIONS)),
            "catalog_editing_enabled": self.catalog_editing_enabled,
            "catalog_path": str(self._catalog_path) if self._catalog_path else None,
            "profiles": sorted(PROFILE_NARROWING),
            # Provider and model per profile, so the hire form can say what a
            # choice actually starts.  Empty when the operator config could not
            # be read: the panel falls back to "fixed in the profile".
            "profile_info": self.profile_info(),
            # Models to offer beside the hire form's free-text field, keyed by
            # provider.  The panel names no model of its own, so this is the
            # only place the form's suggestions can come from; empty lists are
            # normal and mean the field is simply typed into.
            "model_options": self.model_options(snapshot),
            # Read-only reference: the same composition a launch receives, so
            # the Tools view can never drift from what actually runs.
            "profile_tools": profile_tool_summary(),
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
        # Named states, not missing switches: each branch says which half of
        # `catalog_editing_enabled` is false, so the refusal is actionable.  The
        # path check comes first and is not an assert -- `python -O` strips
        # assertions, and a stripped guard would let None reach
        # write_role_catalog and raise an opaque TypeError instead of a refusal
        # (O2, 2026-08-10 review).
        if self._catalog_path is None:
            raise ConfigurationError(
                "this panel has no operator catalogue configured, so there is nothing to edit"
            )
        if not self.writes_enabled:
            raise ConfigurationError(
                "this panel holds no operator session; it shows the catalogue and changes nothing"
            )
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
            raise ValidationError(f"{entry_id} is required by active roles: " + ", ".join(users))
        catalogue = load_role_catalog(path, workspace_root=self.repo)
        catalogue[section] = [item for item in catalogue[section] if item["id"] != entry_id]
        write_role_catalog(path, catalogue, workspace_root=self.repo)
        return {"section": section, "removed": entry_id, "catalog_path": str(path)}

    def search(
        self, *, query: str, limit: int = 25, subject_kind: str | None = None
    ) -> dict[str, Any]:
        """Search history. Read-only: synchronizing a projection records nothing."""

        return self.manager().search_history(query, limit=limit, subject_kind=subject_kind)

    def pending_operations(self) -> list[dict[str, Any]]:
        """Live requests waiting on a person, and who can answer each one.

        The communication channel authorizes by participant, so this server can
        answer only the requests whose delegation it owns.  Rather than hide the
        rest, it lists them and names the session that must answer -- a blocker
        you cannot see is worse than one you cannot yet act on.
        """

        from agent_commons.services.communication import (
            CommunicationRuntimeService,
            _participant_id,
        )

        # One read of the (possibly rotating) session lineage binds both the
        # manager and the answerability filter to the same value; a second read
        # through the provider could observe a different session between them.
        session_ids = self.writer_session_ids
        manager = self._writer_bound(session_ids[-1]) if session_ids else self.manager()
        try:
            operations = CommunicationRuntimeService(manager).inbox()
        except (CommonsError, OSError) as exc:
            # A corrupt or unreadable communication store is not "no blockers":
            # swallowing it silently made a real failure indistinguishable from
            # nothing needing you, and it compounded the two-sources split this
            # queue exists to close (O1/H4, 2026-08-10 review).  The canonical
            # attention list does not depend on this read, so the operator still
            # sees the blocker; only the live answer box is unavailable, and the
            # reason is logged rather than hidden.
            _LOG.warning("communication inbox unavailable, live answers disabled: %s", exc)
            operations = ()
        # Scopes store a deterministic pseudonym, not the registry session id,
        # so the comparison has to be made in the same space.  The whole owned
        # lineage participates: a scope recorded against a session this panel
        # has since replaced still belongs to this panel, and hiding its answer
        # form would orphan the blocker forever.
        answerable = {_participant_id(str(item)) for item in session_ids}
        found = []
        for record in operations:
            if record.get("state") not in {"open", "replied"}:
                continue
            scope = record.get("scope") or {}
            recipients = {str(item) for item in scope.get("allowed_recipient_session_ids") or ()}
            found.append(
                {
                    "operation_id": record.get("operation_id"),
                    "kind": record.get("kind"),
                    "state": record.get("state"),
                    "delegation_id": scope.get("delegation_id"),
                    "task_id": scope.get("task_id"),
                    "metadata": record.get("metadata"),
                    "deadline": record.get("deadline"),
                    "answerable_here": bool(answerable & recipients),
                    "answer_from_session": sorted(recipients),
                }
            )
        return found

    def attention(self) -> dict[str, Any]:
        """One canonical queue of everything waiting on a person.

        The amber ring, the footer count, and this list are now the same
        source.  The Blocked tab used to read the operational communication
        store, which is empty for a CLI writer, a crashed worker, replay, and
        always in read-only, so the graph glowed 'waiting on you: N' while the
        list was empty and the tab hid itself -- a blocker you cannot see is
        worse than one you cannot yet act on (H4, 2026-08-10 review).

        Presence is canonical: an input-needed run, an open decision-request,
        question, or help thread, and a role proposal.  The operational store
        only *enriches* an item with a live answer box where this session may
        reply; if that store is unreadable the item still appears, and the
        design reviewers' merged attention queue replaces the two hidden tabs.
        """

        snapshot = self.manager().snapshot()
        canonical_attention = awaits_human(snapshot)
        answerable_by_delegation: dict[str, dict[str, Any]] = {}
        for operation in self.pending_operations():
            delegation_id = str(operation.get("delegation_id") or "")
            if delegation_id:
                answerable_by_delegation[delegation_id] = operation

        items: list[dict[str, Any]] = []
        for attention_item in canonical_attention.items:
            if attention_item.kind != "run_blocked":
                continue
            delegation_id = attention_item.identifier
            record = attention_item.record
            operation = answerable_by_delegation.get(delegation_id)
            items.append(
                {
                    "kind": "run_blocked",
                    "id": delegation_id,
                    "agent_id": record.get("agent_id"),
                    "target_ref": record.get("target_ref"),
                    "run_state": record.get("state"),
                    "reason_code": record.get("reason_code"),
                    "summary": record.get("summary"),
                    "operation_id": (operation or {}).get("operation_id"),
                    "metadata": (operation or {}).get("metadata"),
                    "answerable_here": bool((operation or {}).get("answerable_here")),
                    "answer_from_session": (operation or {}).get("answer_from_session") or [],
                    "deadline": (operation or {}).get("deadline"),
                }
            )
        # A run that reached `succeeded` left nothing anywhere on screen, so the
        # loop looked closed while the work was still waiting on a person to
        # accept it or send it back — the blocker both round-3 testers hit
        # (finding 3).  It belongs in the same list as every other thing waiting
        # on you, so the footer count and the amber ring stay one source.  One
        # item per task, not per run: ids sort chronologically, so the last one
        # seen is the run whose result is actually being asked about.
        returned: dict[str, dict[str, Any]] = {}
        for attention_item in canonical_attention.items:
            if attention_item.kind != "work_returned":
                continue
            delegation_id = attention_item.identifier
            record = attention_item.record
            target = record.get("target_ref") or {}
            task_id = str(target.get("id") or "")
            task = snapshot.tasks.get(task_id)
            assert task is not None
            agent_id = record.get("agent_id")
            agent = snapshot.agents.get(str(agent_id)) if agent_id else None
            returned[task_id] = {
                "kind": "work_returned",
                "id": task_id,
                "task_id": task_id,
                "title": task.get("title"),
                "task_state": task.get("state"),
                "task_revision": str(task.get("effective_revision") or task.get("revision")),
                "delegation_id": delegation_id,
                "agent_id": agent_id,
                "agent_name": agent.get("name") if agent else None,
            }
        items.extend(returned.values())
        for attention_item in canonical_attention.items:
            if attention_item.kind != "thread":
                continue
            thread_id = attention_item.identifier
            record = attention_item.record
            thread_type = str(record.get("thread_type", ""))
            proposal = (record.get("extensions") or {}).get("staff_proposal")
            is_proposal = isinstance(proposal, Mapping) and proposal.get("action") == "create_role"
            items.append(
                {
                    "kind": "proposal" if is_proposal else "thread",
                    "id": thread_id,
                    "thread_type": thread_type,
                    "subject": record.get("subject"),
                    "revision": record.get("revision"),
                    "proposal": dict(proposal) if is_proposal else None,
                }
            )
        # Wave 1 item 8: a role holding a skill the catalogue no longer defines
        # will fail its NEXT launch fail-closed.  Surface that here, before any
        # run — the catalogue can be edited by hand, outside the panel.
        if self._catalog_path is not None:
            from agent_commons.catalog import catalog_ids

            try:
                known = catalog_ids(
                    load_role_catalog(self._catalog_path, workspace_root=self.repo),
                    "skills",
                )
            except CommonsError:
                known = None
            if known is not None:
                for agent_id, record in sorted(snapshot.agents.items()):
                    if record.get("state") != "active":
                        continue
                    missing = sorted(set(record.get("skills") or ()) - known)
                    if missing:
                        items.append(
                            {
                                "kind": "config_broken",
                                "id": agent_id,
                                "agent_id": agent_id,
                                "name": record.get("name"),
                                "missing_skills": missing,
                            }
                        )
        return {
            "items": [bounded_copy(item) for item in items],
            "count": len(items),
            "writes_enabled": self.writes_enabled,
        }

    def engagements(self) -> list[dict[str, Any]]:
        """The main chats, readable whether or not this server writes."""

        return [bounded_copy(item) for item in self.manager().list_engagements()]

    def agent_proposals(self) -> list[dict[str, Any]]:
        """Open role proposals, readable whether or not this server writes."""

        return [bounded_copy(item) for item in self.manager().list_agent_proposals()]

    # -- writes ---------------------------------------------------------------

    def _check_role_selection(
        self,
        profile_id: str | None,
        skills: Any,
        tool_allowlist: Any,
    ) -> None:
        """Refuse a selection the next launch would refuse, at click time.

        The launch path stays the last line (fail-closed), but the operator is
        here now: a skill the catalogue does not define or a tool outside the
        role's profile should be named while they are still looking at the
        form, not when the run dies later (round 2, product; wave 1 item 5).
        """

        selected_skills = tuple(str(name) for name in skills or ())
        if selected_skills and self._catalog_path is not None:
            from agent_commons.catalog import catalog_ids, load_role_catalog

            known = catalog_ids(
                load_role_catalog(self._catalog_path, workspace_root=self.repo), "skills"
            )
            missing = sorted(set(selected_skills) - known)
            if missing:
                raise ValidationError(
                    "these skills are not in the operator catalogue: " + ", ".join(missing)
                )
        selected_tools = tuple(str(name) for name in tool_allowlist or ())
        if selected_tools and profile_id:
            summary = profile_tool_summary().get(str(profile_id))
            if summary is not None:
                available = (
                    set(summary["fixed"])
                    | set(summary["narrowable"])
                    | set(summary["grant_tools"].values())
                )
                outside = sorted(set(selected_tools) - available)
                if outside:
                    raise ValidationError(
                        "role tool selection is not part of this profile: " + ", ".join(outside)
                    )

    def create_agent(self, *, from_preset_id: str | None = None, **fields: Any) -> dict[str, Any]:
        """Hire a role, once, with the model it will run on for its whole life.

        The model is chosen here and nowhere else.  A role's accumulated
        context is built for the model that built it, so moving a hired role to
        another one would need that context recomputed -- the gear panel
        therefore has no model field and is not going to grow one; it edits
        skills, the system prompt, the reachable MCP servers, and how much the
        role decides on its own.

        Empty means the profile's model stands, which is why blank is
        normalized to absent rather than refused: an untouched free-text box
        must mean "you choose", not "run a role named ''".
        """

        manager = self.writer()
        fields["model"] = self._chosen_model(fields.get("model"))
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
            if fields["model"] is None:
                # A preset that was saved with a model is a preset whose whole
                # point includes it; hiring from it and silently getting the
                # profile's model instead would be the template not applying.
                fields["model"] = role_model(preset)
        self._check_role_selection(
            fields.get("profile_id"), fields.get("skills"), fields.get("tool_allowlist")
        )
        return manager.create_agent(**fields)

    @staticmethod
    def _chosen_model(value: Any) -> str | None:
        """The model this hire names, refused here rather than at launch.

        The runtime validates it again when the profile is replaced, and
        `create_agent` validates it once more before it becomes an immutable
        event -- but neither of those refusals reaches the person who typed it.
        This one lands on the form, while the field is still on screen.
        """

        if value is None:
            return None
        text = str(value).strip()
        return validate_model_name(text) if text else None

    # -- launch (MUST-4) ------------------------------------------------------

    #: A UI-launched run is a single bounded leaf: no children, one attempt, and
    #: the subscription-friendly `provider_units` budget the broker docs prefer.
    _DEFAULT_RUN_LIMITS: dict[str, Any] = {
        "max_depth": 0,
        "wall_time_seconds": 600,
        "max_attempts": 1,
        "max_concurrency": 1,
        "budget": {"unit": "provider_units", "limit": 1},
    }

    def _runtime_service(self, manager: CommonsManager) -> Any:
        """The one runtime service, over the writer session, that launches a run.

        It is the same `DelegationRuntimeService` the CLI broker uses, built from
        the operator profile config — not a second launch path.  Tests inject a
        factory that wraps a fake runner so no real provider is spawned.
        """

        if self._runtime_factory is not None:
            return self._runtime_factory(manager)
        from agent_commons.services.delegation_runtime import (
            DelegationRuntimeService,
            load_runtime_configuration,
        )

        config = load_runtime_configuration(self._profile_config, workspace_root=self.repo)
        runner = None
        if config.demo:
            # Demo mode: complete the run without launching a provider, so the
            # panel's Run closes the loop in a scratch workspace without billing.
            from agent_commons.runtime.demo import DemoRunner

            runner = DemoRunner(manager.paths.state_root)
        return DelegationRuntimeService(
            manager,
            profiles=config.profiles,
            operator_limits=config.limits,
            catalog=config.catalog,
            runner=runner,
        )

    def runs(self) -> list[dict[str, Any]]:
        """Live and recent provider runs with bounded local failure diagnostics.

        Reads the operational attempt store (metadata: phase, pid liveness,
        target) and joins each to its canonical delegation state.  Unsuccessful
        runs may include the attempt store's sanitized 4 KiB stderr diagnostic
        tail and bounded terminal-tool rejection messages.  It never exposes
        stdout, prompts, transcripts, reasoning, or tool arguments.
        """

        from agent_commons.runtime import AttemptStore, TerminalToolAuditStore

        manager = self.manager()
        store = AttemptStore(manager.paths.state_root, read_only=True)
        try:
            attempts = store.list_attempts()
        except (CommonsError, OSError):
            return []
        delegations = manager.snapshot().delegations
        audit_store = TerminalToolAuditStore(
            manager.paths.state_root,
            security_policy=manager.policy,
            read_only=True,
        )
        found: list[dict[str, Any]] = []
        for attempt in attempts:
            record = attempt.as_dict()
            delegation_id = str(record["correlation"]["delegation_id"])
            delegation = delegations.get(delegation_id) or {}
            live = store.process_is_live(record.get("pid"))
            try:
                audit = audit_store.get(delegation_id)
                rejection_details = [
                    {
                        "ordinal": detail.ordinal,
                        "tool": detail.tool,
                        "error_type": detail.error_type,
                        "message": detail.message,
                        "recorded_at": detail.recorded_at,
                    }
                    for detail in audit.rejection_details
                ]
                rejection_count = audit.terminal_tool_rejections
                rejection_details_truncated = audit.rejection_details_truncated
            except (CommonsError, OSError):
                rejection_details = []
                rejection_count = 0
                rejection_details_truncated = False
            found.append(
                {
                    "delegation_id": delegation_id,
                    "attempt_id": record["attempt_id"],
                    "phase": record["state"],
                    "live": live,
                    # When the attempt started and when it was last touched, read
                    # off the operational attempt store.  That store is not the
                    # ledger: canonical events carry no wall clock and replay
                    # never sees these values, which is exactly why the run
                    # panel — an operational view — is where they belong
                    # (finding 28).
                    "started_at": record.get("created_at"),
                    "updated_at": record.get("updated_at"),
                    # How long a finished run took.  A live run gets None rather
                    # than a number: any duration computed here is stale the
                    # moment it is rendered, and a caller that wants a ticking
                    # figure has `started_at` to count up from.
                    "duration_seconds": (
                        None
                        if live
                        else _elapsed_seconds(record.get("created_at"), record.get("updated_at"))
                    ),
                    "profile_id": record.get("profile_id"),
                    "target_kind": record["correlation"].get("target_kind"),
                    "target_id": record["correlation"].get("target_id"),
                    "agent_id": delegation.get("agent_id"),
                    "delegation_state": delegation.get("state"),
                    # What the run was for, and the bounds it was launched
                    # under, exactly as the canonical delegation records them.
                    # `limits` is a launch-time CAP and nothing else — its
                    # `budget` says what the run was permitted to use, never
                    # what it used.  Nothing in this codebase records consumed
                    # provider units or money, so no spend is reported here;
                    # an estimate would read like a measurement, and there is
                    # no measurement to report.
                    "purpose": delegation.get("purpose"),
                    "limits": delegation.get("limits") or None,
                    # The one human-readable line a terminal run leaves behind
                    # (canonical delegation summary, already public through the
                    # entity route) — the PM run could not find any result
                    # without opening raw JSON (finding 4).
                    "summary": (
                        str(delegation.get("summary"))[:500] if delegation.get("summary") else None
                    ),
                    # This is deliberately not raw provider output. AttemptStore
                    # only persists this field for unsuccessful processes after
                    # bounding, path removal, secret/PII line redaction, and a
                    # fail-closed security scan.
                    "stderr_diagnostic_tail": record.get("stderr_diagnostic_tail"),
                    "stderr_diagnostic_tail_truncated": bool(
                        record.get("stderr_diagnostic_tail_truncated", False)
                    ),
                    "stderr_diagnostic_tail_redacted": bool(
                        record.get("stderr_diagnostic_tail_redacted", False)
                    ),
                    "terminal_tool_rejections": rejection_count,
                    "terminal_tool_rejection_details": rejection_details,
                    "terminal_tool_rejection_details_truncated": (rejection_details_truncated),
                }
            )
        # Live runs first, then most-recently-seen; a person watches the moving
        # ones.
        found.sort(key=lambda item: (not item["live"], item["attempt_id"]), reverse=False)
        return [bounded_copy(item) for item in found]

    def launch_options(self) -> dict[str, Any]:
        """What the panel needs to offer a run: active roles and pickable tasks."""

        snapshot = self.manager().snapshot()
        roles = [
            {"id": rid, "name": rec.get("name"), "profile_id": rec.get("profile_id")}
            for rid, rec in sorted(snapshot.agents.items())
            if rec.get("state") == "active" and not rec.get("template")
        ]
        # A run needs an open target; a finished task is not something to staff.
        # `review` belongs here even though the work is done: the acceptance
        # chain tells the operator in as many words to "run a role whose profile
        # is an independent reviewer against this task", and a task that has
        # just been sent for review is exactly when that must be possible.
        # Leaving it out made the panel point at an action its own picker
        # refused to offer (vibecoder round, blocker 1).
        open_states = {"ready", "assigned", "active", "blocked", "review"}
        tasks = [
            {"id": tid, "title": rec.get("title"), "state": rec.get("state")}
            for tid, rec in sorted(snapshot.tasks.items())
            if rec.get("state") in open_states
        ]
        return {"launch_enabled": self.launch_enabled, "roles": roles, "tasks": tasks}

    def run_role_on_task(
        self,
        *,
        agent_id: str,
        task_id: str,
        wall_time_seconds: int | None = None,
        idempotency_key: str | None = None,
        background: bool = True,
    ) -> dict[str, Any]:
        """Put a standing role to work on a task, and launch the provider.

        Records a `delegation.requested` on behalf of the role — which fixes the
        provider and model to the role's profile — then runs it through the same
        broker the CLI uses.  The run proceeds off-request so the panel returns
        at once; its canonical state changes reach the panel over the stream.
        """

        if not self.launch_enabled:
            raise ConfigurationError(LAUNCH_NOT_CONFIGURED)
        limits = dict(self._DEFAULT_RUN_LIMITS)
        if wall_time_seconds:
            limits["wall_time_seconds"] = int(wall_time_seconds)
        if self._session_owner is not None:
            # A forced heartbeat *before* `create_delegation`, not merely before
            # the child starts: the runtime refuses a child unless the parent
            # session TTL covers the wall time plus a finalization margin, and
            # by launch time that refusal would arrive raw from the broker in a
            # background thread.  Renewing here also pins the session for the
            # whole launch window, so the requester recorded on the delegation
            # is the session the background thread launches under.
            self._session_owner.ensure_run_ttl(int(limits["wall_time_seconds"]))
        writer = self.writer()
        role = writer.get_agent(agent_id)
        if role.get("state") != "active":
            raise ValidationError("only an active role can be given work")
        if role.get("template"):
            raise ValidationError("a role preset is a template and is never employed")
        task = writer.snapshot().tasks.get(task_id)
        if task is None:
            raise ValidationError(f"no such task: {task_id}")
        profile_id = str(role["profile_id"])
        # The purpose follows the role's profile.  A reviewer profile needs an
        # open independent review to exist; the domain refuses with a legible
        # message if one does not, so the panel surfaces that rather than
        # inventing a review here.
        purpose = (
            "independent_review"
            if profile_id.endswith("independent-reviewer")
            else "implementation"
        )
        delegation = writer.create_delegation(
            target_ref={"kind": "task", "id": task_id},
            target_revision=str(task.get("effective_revision") or task["revision"]),
            target_profile=profile_id,
            purpose=purpose,
            limits=limits,
            on_behalf_of_agent_id=agent_id,
            idempotency_key=idempotency_key,
        )
        delegation_id = str(delegation["entity_ref"]["id"])
        launch_key = f"ui-launch-{delegation_id}"

        def _launch() -> None:
            try:
                # A fresh writer manager for the run thread: the runtime binds
                # the requester session, and a manager is cheap and not shared.
                self._runtime_service(self.writer()).run(
                    delegation_id, delegation["revision"], idempotency_key=launch_key
                )
            except Exception as exc:  # a launch failure is reported, never silent
                _LOG.warning("UI launch of %s failed: %s", delegation_id, exc)
                # The log is the operator's last resort, not their first: this
                # thread is the only witness, and a delegation left `requested`
                # looks pending forever.  A live tester waited twenty-five
                # minutes on a run that had failed in its first second, because
                # the panel had nothing else to show.  Record the refusal where
                # a person will see it -- `needs_operator` is legal from
                # `requested` and is exactly what this is: work only a human can
                # unblock.  Best effort: if even this write fails, the log stays
                # the last resort it always was.
                try:
                    self.writer().mark_delegation_needs_operator(
                        delegation_id,
                        str(delegation["revision"]),
                        reason_code="launch_failed",
                        summary=f"the panel could not start this run: {exc}",
                        idempotency_key=f"{launch_key}:launch-failed",
                    )
                    self.invalidate()
                except CommonsError as write_failure:  # pragma: no cover - defence
                    _LOG.warning(
                        "UI launch failure of %s could not be recorded: %s",
                        delegation_id,
                        write_failure,
                    )
            finally:
                self.invalidate()

        if background:
            thread = threading.Thread(target=_launch, name=launch_key, daemon=True)
            self._launch_threads.append(thread)
            thread.start()
        else:
            _launch()
        self.invalidate()
        return {
            "delegation_id": delegation_id,
            "target_profile": profile_id,
            "purpose": purpose,
            "launched": True,
        }

    def answer_operation(
        self, *, operation_id: str, answer: Mapping[str, Any], idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Answer a live request. Bounded metadata only; never a secret."""

        from agent_commons.services.communication import CommunicationRuntimeService

        if not isinstance(answer, Mapping) or not answer:
            raise ValidationError("an answer needs at least one field")
        service = CommunicationRuntimeService(self.writer())
        return service.reply_to_input(
            operation_id,
            idempotency_key=idempotency_key or f"ui-reply-{operation_id}",
            answer=dict(answer),
        )

    def open_engagement(self, **fields: Any) -> dict[str, Any]:
        return self.writer().open_engagement(**fields)

    def say_in_engagement(
        self,
        *,
        thread_id: str,
        expected_revision: str,
        body: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if not body.strip():
            raise ValidationError("a message needs a body")
        return self.writer().reply_thread(
            thread_id, expected_revision, body=body, idempotency_key=idempotency_key
        )

    def approve_agent_proposal(self, *, thread_id: str, **fields: Any) -> dict[str, Any]:
        return self.writer().approve_agent_proposal(thread_id, **fields)

    def decline_agent_proposal(self, *, thread_id: str, **fields: Any) -> dict[str, Any]:
        return self.writer().decline_agent_proposal(thread_id, **fields)

    def reconfigure_agent(self, *, agent_id: str, **fields: Any) -> dict[str, Any]:
        expected_revision = fields.pop("expected_revision")
        manager = self.writer()
        # Mirror of the hire-time check: reconfigure was a pure passthrough, so
        # the panel could grant a skill the catalogue lost or a foreign tool and
        # only the NEXT launch would fail (council contract, wave 1 item 5).
        changes = fields.get("changes") or {}
        if "skills" in changes or "tool_allowlist" in changes:
            record = manager.get_agent(agent_id)
            self._check_role_selection(
                record.get("profile_id"),
                changes.get("skills"),
                changes.get("tool_allowlist"),
            )
        return manager.reconfigure_agent(agent_id, expected_revision, **fields)

    def create_task(self, **fields: Any) -> dict[str, Any]:
        """One task, recorded through the same manager the CLI uses.

        The PM cold run's blocker: the chat form looked like task creation but
        records a thread — a manager could not put work on the board at all.
        """

        return self.writer().create_task(**fields)

    def revise_task(self, *, task_id: str, **fields: Any) -> dict[str, Any]:
        """Revise task content through the canonical immutable event path."""

        expected_revision = fields.pop("expected_revision")
        return self.writer().revise_task(task_id, expected_revision, **fields)

    #: Which manager transitions carry a task from where it is to `review`.
    #: Acceptance is only legal from `review`, so the panel's one button has to
    #: walk the whole way rather than pretend the task is already there — and it
    #: walks by calling the same transitions the CLI calls, never by writing a
    #: state.  `blocked`, `accepted` and `cancelled` are absent on purpose: each
    #: gets its own refusal below, because "nothing happened" is the worst
    #: possible answer to a click.
    _REVIEW_WALK: dict[str, tuple[str, ...]] = {
        "ready": ("start_task", "complete_task", "submit_task"),
        "assigned": ("start_task", "complete_task", "submit_task"),
        "active": ("complete_task", "submit_task"),
        "completed": ("submit_task",),
        "review": (),
    }

    #: What the ledger will say this operator did.  Canonical text, so it stays
    #: English and honest: the panel sent the work onward, it did not do it.
    #: Two sentences, because `complete_task` is a claim that the work is done
    #: and the two ways of arriving at it are not the same claim.  A run that
    #: finished is evidence; without one the operator is asserting completion
    #: themselves -- legitimate (they may have done the work by hand) but it
    #: must not be recorded as though a run produced it.
    _WALK_SUMMARY = "the operator sent finished work for review from the panel"
    _WALK_SUMMARY_UNRUN = (
        "the operator judged this work done and sent it for review from the panel; "
        "no run had finished on it"
    )

    @staticmethod
    def _step_key(idempotency_key: str | None, step: str) -> str | None:
        """One caller key, one distinct key per recorded event.

        Reusing the caller's key across the walk would make the second step
        collide with the first's receipt; deriving keeps a retried click
        idempotent end to end.
        """

        return None if not idempotency_key else f"{idempotency_key}:{step}"

    @staticmethod
    def _task_has_finished_run(manager: CommonsManager, task_id: str) -> bool:
        """Did any delegation on this task actually reach a successful end?

        The evidence the panel needs before it will claim a task is completed.
        Only `succeeded` counts: a run that ended `needs_operator` or `failed`
        produced no result to review.
        """

        target = {"kind": "task", "id": task_id}
        return any(
            record.get("state") == "succeeded" and record.get("target_ref") == target
            for record in manager.snapshot().delegations.values()
        )

    @staticmethod
    def _task_or_refuse(manager: CommonsManager, task_id: str) -> Mapping[str, Any]:
        record = manager.snapshot().tasks.get(task_id)
        if record is None:
            raise ValidationError(f"no such task: {task_id}")
        return record

    def request_task_review(
        self,
        *,
        task_id: str,
        expected_revision: str,
        criteria: Any = (),
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Send finished work for an independent review, from wherever it sits.

        Both round-3 testers stopped at the same wall: a run reaches `succeeded`
        and nothing in the panel can accept the work, because acceptance needs a
        task already in `review` and an approved independent review bound to its
        current revision.  This is the first half of that chain.  It records
        nothing itself — every step is a `CommonsManager` transition, and the
        review it opens is the one an independent reviewer then answers.  The
        caller's revision is spent on the *first* step so a drawer that has gone
        stale is refused by the domain rather than quietly overwritten; each
        later step re-reads the revision the previous one produced.
        """

        manager = self.writer()
        record = self._task_or_refuse(manager, task_id)
        state = str(record.get("state", ""))
        if state == "blocked":
            raise ValidationError(
                "this task is blocked; unblock it before sending the work for review"
            )
        walk = self._REVIEW_WALK.get(state)
        if walk is None:
            raise ValidationError(
                f"this task is {state or 'in an unknown state'}; "
                "there is nothing to send for review"
            )
        if not walk:
            # Already in `review`, so no transition will test the caller's
            # revision for us.  Refuse a stale drawer here in the same words the
            # domain would have used.
            current = {str(record.get("revision")), str(record.get("effective_revision") or "")}
            if str(expected_revision) not in current:
                raise LifecycleConflictError(
                    f"stale expected revision {expected_revision}; "
                    f"current revision is {record.get('revision')}"
                )
        steps: list[str] = []
        revision = str(expected_revision)
        # Decided once, before the walk moves anything: afterwards the task's own
        # state has changed and the question "was there evidence?" is muddier.
        summary = (
            self._WALK_SUMMARY
            if self._task_has_finished_run(manager, task_id)
            else self._WALK_SUMMARY_UNRUN
        )
        for step in walk:
            arguments: dict[str, Any] = {"idempotency_key": self._step_key(idempotency_key, step)}
            if step in {"complete_task", "submit_task"}:
                arguments["summary"] = summary
            getattr(manager, step)(task_id, revision, **arguments)
            steps.append(step)
            # A transition names the record's `revision`; the review that follows
            # binds its `effective_revision`.  The two differ only once a
            # correction lands, which is exactly when reusing one for the other
            # would bind the review to work nobody did.
            record = self._task_or_refuse(manager, task_id)
            revision = str(record.get("revision"))
        target_revision = str(record.get("effective_revision") or record.get("revision"))
        chosen = tuple(str(item) for item in criteria or () if str(item).strip())
        if not chosen:
            chosen = tuple(str(item) for item in record.get("acceptance_criteria") or ())
        if not chosen:
            # `request_review` requires something to judge against, and a task
            # with no criteria still has a description a reviewer can read.
            chosen = ("the work meets the task description",)
        review = manager.request_review(
            target_ref={"kind": "task", "id": task_id},
            target_revision=target_revision,
            criteria=chosen,
            independent=True,
            idempotency_key=self._step_key(idempotency_key, "request_review"),
        )
        steps.append("request_review")
        return {
            "task_id": task_id,
            "task_state": str(record.get("state", "")),
            "task_revision": target_revision,
            "review_id": str(review["entity_ref"]["id"]),
            "review_revision": str(review["revision"]),
            "steps": steps,
        }

    def accept_task(
        self,
        *,
        task_id: str,
        expected_revision: str,
        summary: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Accept the work. The manager picks the review; the panel never does.

        Deliberately thin: which review qualifies — approved, independent, not
        stale, bound to this exact revision, completed outside the principals
        that authored the work — is the domain's judgement, and the refusal when
        none qualifies is the property the whole design exists to protect.  It
        reaches the operator as the guard that fired.
        """

        if not summary.strip():
            raise ValidationError("an acceptance needs a summary of what you accepted")
        return self.writer().accept_task(
            task_id,
            expected_revision,
            summary=summary,
            idempotency_key=idempotency_key,
        )

    def reopen_task(
        self,
        *,
        task_id: str,
        expected_revision: str,
        reason: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Send the work back. The other half of the decision, recorded the same way."""

        if not reason.strip():
            raise ValidationError("sending work back needs a reason the role can act on")
        return self.writer().reopen_task(
            task_id,
            expected_revision,
            reason=reason,
            idempotency_key=idempotency_key,
        )

    def open_agent_link(self, **fields: Any) -> dict[str, Any]:
        """Open one directed link between two roles — a recorded permission.

        A thin adapter over the same ``open_agent_link`` the CLI uses: the
        domain is the judge (enum, self-link, deadline bounds, both roles
        active); the panel maps its refusal, never re-implements it.
        """

        return self.writer().open_agent_link(**fields)

    def close_agent_link(self, *, link_id: str, **fields: Any) -> dict[str, Any]:
        expected_revision = fields.pop("expected_revision")
        return self.writer().close_agent_link(link_id, expected_revision, **fields)

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

        if kind == "session":
            for session in self.manager().sessions.list_sessions():
                if session.session_id == entity_id:
                    return bounded_copy(
                        session.actor_context() | {"state": _session_state(session)}
                    )
            return None
        attribute = collection_for(kind)
        if attribute is None:
            return None
        manager = self.manager()
        if kind == "agent":
            # Through the agent view, so effective_grants, retirement_blockers,
            # and live_delegations reach the panel.  The raw projection record
            # carries only stored grants, which made guarantee 7 invisible in the
            # one screen where a human edits grants and the "cannot be retired
            # yet" warning unable to fire (M3, 2026-08-10 review).
            if entity_id not in manager.snapshot().agents:
                return None
            return bounded_copy(manager.get_agent(entity_id))
        record = getattr(manager.snapshot(), attribute).get(entity_id)
        return None if record is None else bounded_copy(record)
