"""First-run setup: discover providers and generate the operator runtime config.

This module is the security-bearing half of the panel's first-run screen.  It
answers three questions and does nothing else -- no routes, no context, no CLI:

- *What state is this directory in?*  ``setup_state`` distinguishes a directory
  that is not a git repository, a repository without an initialized workspace,
  an initialized workspace without an operator runtime config, a config the
  launch loader refuses, and a configured one, naming each with the refusal
  codes frozen by the wave contract.  ``setup_configured`` is earned through
  ``load_runtime_configuration`` on every call, never inferred from the file's
  existence.
- *Which executables exist here?*  ``discover_providers`` probes ``claude``,
  ``codex``, ``grok``, ``agent-commons-mcp``, and ``git``.  It performs **no checks of
  its own**: the single call per candidate is ``resolve_trusted_executable``,
  which already carries PATH resolution, the workspace ban, the regular-file
  and executability requirements, the group/world-writable ban, and the owner
  check.  This module only sequences candidates and keeps every refusal.
- *Can a working config be written?*  ``generate_runtime_config`` refuses a
  target inside the delegated workspace, inside the operational state base or
  state root (the exact trap that flips state resolution to ``legacy-exact``
  and makes ``ensure_layout`` refuse the whole workspace), and a target whose
  placement fails the ownership rules.  The launch modes of the generated
  profiles are fixed here in code and are never selectable from the UI.
  Immediately after the write the file is read back through
  ``load_runtime_configuration`` -- the same guarded loader the launch path
  uses -- so the generator and the consumer can never drift apart.  A file the
  loader refuses is renamed to ``runtime.yaml.rejected`` and the refusal is
  surfaced with its reason instead of being left in place as a working config.

The placement and write discipline (atomic replace, ``0600``, the state-storage
ban) comes from :mod:`agent_commons.operator_files`; nothing here reimplements
it.
"""

from __future__ import annotations

import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from agent_commons.catalog import empty_catalog, write_role_catalog
from agent_commons.config import CommonsPaths
from agent_commons.errors import CommonsError, ConfigurationError
from agent_commons.operator_files import (
    STATE_REFUSAL_CODE,
    assert_outside_state_storage,
    assert_outside_workspace,
    replace_operator_file,
)
from agent_commons.runtime.model import (
    ClaudePermissionMode,
    CodexApprovalPolicy,
    CodexSandbox,
    ExecutableResolutionError,
    ExecutableRole,
    GrokPermissionMode,
    GrokSandbox,
    resolve_trusted_executable,
)
from agent_commons.services.delegation_runtime import load_runtime_configuration

_LABEL = "runtime profile config"

#: Setup states and refusal codes, frozen by the wave contract.  The backend
#: emits them and the frontend renders by them; neither side may rename one.
SETUP_NOT_A_REPOSITORY = "setup_not_a_repository"
SETUP_UNINITIALIZED = "setup_uninitialized"
SETUP_UNCONFIGURED = "setup_unconfigured"
SETUP_CONFIGURED = "setup_configured"
SETUP_NO_PROVIDER_FOUND = "setup_no_provider_found"
PATH_REFUSED_WORKSPACE = "setup_path_refused_workspace"
PATH_REFUSED_STATE = STATE_REFUSAL_CODE
PATH_REFUSED_OWNERSHIP = "setup_path_refused_ownership"
CONFIG_REJECTED_BY_LOADER = "setup_config_rejected_by_loader"

#: Filename of a generated config the loader refused; kept next to the target
#: so the operator can inspect exactly what was rejected.
REJECTED_SUFFIX = ".rejected"


class SetupError(ConfigurationError):
    """A first-run refusal carrying one frozen code and its full detail."""

    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


def default_config_directory() -> Path:
    """``$XDG_CONFIG_HOME/agent-commons``, else ``~/.config/agent-commons``.

    A relative ``XDG_CONFIG_HOME`` is ignored, as the basedir spec requires.
    """

    override = os.environ.get("XDG_CONFIG_HOME", "")
    base = Path(override) if override and Path(override).is_absolute() else None
    if base is None:
        base = Path.home() / ".config"
    return base / "agent-commons"


def default_runtime_config_path() -> Path:
    return default_config_directory() / "runtime.yaml"


def missing_workspace_state(repo: str | Path) -> str | None:
    """The first-run state that precedes any workspace here, or None past it.

    The two states in which the panel has no ledger to read and nothing to
    record into: a directory that is not a git repository, and a repository
    whose workspace was never initialized.  Split from :func:`setup_state`
    because this is the per-request precondition every API route -- reading
    or writing -- refuses by, and it must stay a pair of stats; the config
    and loader questions ``setup_state`` goes on to ask cost a YAML parse and
    change nothing about whether a workspace exists.
    """

    root = Path(repo).expanduser()
    if not (root / ".git").exists():
        return SETUP_NOT_A_REPOSITORY
    workspace_config = root / ".agent-commons" / "workspace.yaml"
    if workspace_config.is_symlink() or not workspace_config.is_file():
        return SETUP_UNINITIALIZED
    return None


def setup_state(repo: str | Path, *, profile_config: str | Path | None = None) -> str:
    """Name the first-run state of ``repo`` with one frozen code.

    The absence of a config is a *state*, never "defaults": the loader's
    default registry is deliberately unlaunchable, so the panel must route the
    operator to setup instead of pretending the environment is configured.

    ``setup_configured`` means the guarded launch loader accepted the file,
    not that a file happens to sit at the path.  Generation proves acceptance once, at
    write time; this is the only place that proves it on every later start, so
    a config broken after the fact (an edit, a permission change, a foreign
    owner) names itself ``setup_config_rejected_by_loader`` instead of posing
    as a configured environment that cannot launch anything.
    """

    return str(setup_state_report(repo, profile_config=profile_config)["state"])


def setup_state_report(
    repo: str | Path, *, profile_config: str | Path | None = None
) -> dict[str, Any]:
    """:func:`setup_state`, keeping the loader's refusal instead of eating it.

    ``setup_config_rejected_by_loader`` alone tells the operator "your file
    does not work" and not one word about why -- and the only way to learn
    more was the terminal, the exact regress this panel exists to remove.  So
    the rejected state carries ``rejected_reason``, the loader's own refusal
    text exactly as ``POST /api/setup/runtime-config`` already reports it, and
    ``rejected_path``, the file it refused.

    ``rejected_path`` names the operator's file *where it stands*: unlike a
    just-generated config, which the generator proves and renames to
    ``runtime.yaml.rejected`` when its own loader read-back refuses it, a
    pre-existing config is the operator's own -- this product did not write it
    and has no business moving it.  Naming a state never touches the disk.
    """

    root = Path(repo).expanduser()
    missing = missing_workspace_state(root)
    if missing is not None:
        return {"state": missing}
    config = Path(profile_config).expanduser() if profile_config else default_runtime_config_path()
    if config.is_symlink() or not config.is_file():
        return {"state": SETUP_UNCONFIGURED}
    try:
        load_runtime_configuration(config, workspace_root=root)
    except CommonsError as exc:
        return {
            "state": CONFIG_REJECTED_BY_LOADER,
            "rejected_reason": str(exc),
            "rejected_path": str(config),
        }
    return {"state": SETUP_CONFIGURED}


@dataclass(frozen=True, slots=True)
class ProbeRefusal:
    """One refused candidate, keeping the resolver's exact reason."""

    candidate: str
    reason: str


@dataclass(frozen=True, slots=True)
class ExecutableProbe:
    """The outcome of resolving one executable through the trusted resolver."""

    name: str
    role: str
    path: str | None
    refusals: tuple[ProbeRefusal, ...]

    @property
    def found(self) -> bool:
        return self.path is not None

    def describe(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "found": self.found,
            "path": self.path,
            "refusals": [
                {"candidate": refusal.candidate, "reason": refusal.reason}
                for refusal in self.refusals
            ],
        }


@dataclass(frozen=True, slots=True)
class ProviderDiscovery:
    """Everything the first-run screen may say about this machine's binaries."""

    claude: ExecutableProbe
    codex: ExecutableProbe
    grok: ExecutableProbe
    mcp: ExecutableProbe
    git: ExecutableProbe

    @property
    def providers_found(self) -> tuple[str, ...]:
        return tuple(probe.name for probe in (self.claude, self.codex, self.grok) if probe.found)

    @property
    def providers_missing(self) -> tuple[str, ...]:
        return tuple(
            probe.name for probe in (self.claude, self.codex, self.grok) if not probe.found
        )

    def describe(self) -> dict[str, Any]:
        return {
            probe.name: probe.describe()
            for probe in (self.claude, self.codex, self.grok, self.mcp, self.git)
        }


def _probe(
    name: str,
    candidates: tuple[str, ...],
    *,
    workspace_root: Path,
    role: ExecutableRole,
) -> ExecutableProbe:
    """Try each candidate through the trusted resolver, keeping every refusal.

    The resolver's answer is final in both directions: a returned path is
    recorded without any further check of our own, and an
    ``ExecutableResolutionError`` is decomposed into the candidate that was
    tried and the resolver's exact reason, so no detail of the refusal is lost
    on the way to the screen.
    """

    refusals: list[ProbeRefusal] = []
    for candidate in candidates:
        try:
            resolved = resolve_trusted_executable(
                candidate, workspace_root=workspace_root, role=role
            )
        except ExecutableResolutionError as error:
            refusals.append(ProbeRefusal(candidate=candidate, reason=str(error)))
        else:
            return ExecutableProbe(name, role.value, resolved, tuple(refusals))
    return ExecutableProbe(name, role.value, None, tuple(refusals))


def discover_providers(workspace_root: str | Path) -> ProviderDiscovery:
    """Resolve every candidate executable through ``resolve_trusted_executable``.

    Candidate order encodes three traps this wave already met:

    - ``agent-commons-mcp`` is not an operator-chosen tool like the providers;
      it is this project's own support binary, and the only correct version is
      the one matching the code that writes the ledger -- the sibling of the
      running interpreter, by construction the same installed distribution as
      this very module.  The sibling is therefore tried *first*; PATH is only
      the fallback for installs where the console script does not sit next to
      the interpreter.  PATH-first was tried and picked another checkout's
      ``.venv/bin/agent-commons-mcp`` on a machine with two checkouts -- the
      exact skew AGENTS.md warns about, where an older install misreads a
      newer ledger as corrupted instead of failing to launch.
    - ``git`` tries the well-known ``/usr/bin/git`` first and falls back to
      PATH; either way the recorded value is the resolved absolute path.
    - The process PATH is the operator's own, which is why this wave's panel
      is started from a terminal.
    """

    root = Path(workspace_root).expanduser()
    interpreter_sibling = str(Path(sys.executable).parent / "agent-commons-mcp")
    return ProviderDiscovery(
        claude=_probe("claude", ("claude",), workspace_root=root, role=ExecutableRole.PROVIDER),
        codex=_probe("codex", ("codex",), workspace_root=root, role=ExecutableRole.PROVIDER),
        # The `agent` compatibility symlink is intentionally not treated as a
        # second provider.  Discovery is the canonical `grok` basename on the
        # operator's PATH, commonly added there from ~/.grok/bin by install.
        grok=_probe("grok", ("grok",), workspace_root=root, role=ExecutableRole.PROVIDER),
        mcp=_probe(
            "agent-commons-mcp",
            (interpreter_sibling, "agent-commons-mcp"),
            workspace_root=root,
            role=ExecutableRole.MCP,
        ),
        git=_probe("git", ("/usr/bin/git", "git"), workspace_root=root, role=ExecutableRole.GIT),
    )


def _refuse_ownership(message: str, *, target: Path) -> SetupError:
    return SetupError(PATH_REFUSED_OWNERSHIP, message, details={"target": str(target)})


def _assert_operator_owned(path: Path, *, expect_directory: bool) -> None:
    """Refuse a symlink, a foreign owner, or group/world-writable placement.

    Absent paths pass: creation is ours and lands with private modes.  The
    rules mirror what the guarded loader will enforce on read, so a placement
    refused here is exactly one the launch path would refuse later.
    """

    if path.is_symlink():
        raise _refuse_ownership(f"{_LABEL} path must not be a symlink: {path}", target=path)
    try:
        metadata = path.stat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise _refuse_ownership(
            f"{_LABEL} path cannot be inspected safely: {path}", target=path
        ) from exc
    if expect_directory and not stat.S_ISDIR(metadata.st_mode):
        raise _refuse_ownership(
            f"{_LABEL} directory location is not a directory: {path}", target=path
        )
    if not expect_directory and not stat.S_ISREG(metadata.st_mode):
        raise _refuse_ownership(f"{_LABEL} target is not a regular file: {path}", target=path)
    if hasattr(os, "getuid") and metadata.st_uid not in {0, os.getuid()}:
        raise _refuse_ownership(
            f"{_LABEL} path must be owned by the operator or root: {path}", target=path
        )
    if metadata.st_mode & 0o022:
        raise _refuse_ownership(
            f"{_LABEL} path must not be group/world writable: {path}", target=path
        )


def _tighten_operator_directory(directory: Path) -> None:
    """Re-verify the just-created config directory by descriptor, then 0700 it.

    ``_assert_safe_placement`` checked this path *before* ``mkdir``, which
    leaves the classic gap: a symlink planted between the check and the
    creation would let ``mkdir(exist_ok=True)`` succeed through it and a
    path-based ``chmod`` follow it into someone else's directory.  Opening
    with ``O_NOFOLLOW | O_DIRECTORY`` binds the ownership check and the
    ``fchmod`` to the one inode that actually exists after ``mkdir``, so the
    directory that gets tightened is provably a non-symlink directory owned by
    the operator.  (Exploiting the original gap already required write access
    to the operator's own config parent, but the descriptor costs nothing.)
    """

    try:
        descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | os.O_DIRECTORY)
    except OSError as exc:
        raise _refuse_ownership(
            f"{_LABEL} directory cannot be opened safely: {directory}", target=directory
        ) from exc
    try:
        metadata = os.fstat(descriptor)
        if hasattr(os, "getuid") and metadata.st_uid not in {0, os.getuid()}:
            raise _refuse_ownership(
                f"{_LABEL} path must be owned by the operator or root: {directory}",
                target=directory,
            )
        os.fchmod(descriptor, 0o700)
    finally:
        os.close(descriptor)


def _profile_bodies(
    discovery: ProviderDiscovery, *, providers: frozenset[str] | None = None
) -> dict[str, dict[str, Any]]:
    """Fixed launch modes per profile; only found providers are written at all.

    The modes are the security-bearing half of the config and are not
    UI-selectable: writable Codex and Grok profiles always require the
    trusted-workspace opt-in, both OS-sandboxed reviewers are pinned read-only,
    and the independent Claude reviewer runs ``dontAsk`` without workspace trust.
    """

    assert discovery.mcp.path is not None and discovery.git.path is not None
    shared = {
        "mcp_executable": discovery.mcp.path,
        "git_executable": discovery.git.path,
    }
    profiles: dict[str, dict[str, Any]] = {}
    if discovery.codex.found and (providers is None or "codex" in providers):
        profiles["codex-builder"] = {
            "executable": discovery.codex.path,
            **shared,
            "sandbox": CodexSandbox.WORKSPACE_WRITE.value,
            "approval_policy": CodexApprovalPolicy.NEVER.value,
            "trusted_workspace": True,
        }
        profiles["codex-independent-reviewer"] = {
            "executable": discovery.codex.path,
            **shared,
            "sandbox": CodexSandbox.READ_ONLY.value,
            "approval_policy": CodexApprovalPolicy.NEVER.value,
            "trusted_workspace": True,
        }
    if discovery.claude.found and (providers is None or "claude" in providers):
        profiles["claude-builder"] = {
            "executable": discovery.claude.path,
            **shared,
            "permission_mode": ClaudePermissionMode.ACCEPT_EDITS.value,
            "trusted_workspace": True,
        }
        profiles["claude-independent-reviewer"] = {
            "executable": discovery.claude.path,
            **shared,
            "permission_mode": ClaudePermissionMode.DONT_ASK.value,
        }
    if discovery.grok.found and (providers is None or "grok" in providers):
        profiles["grok-builder"] = {
            "executable": discovery.grok.path,
            **shared,
            "sandbox": GrokSandbox.WORKSPACE.value,
            "permission_mode": GrokPermissionMode.ALWAYS_APPROVE.value,
            "trusted_workspace": True,
        }
        profiles["grok-independent-reviewer"] = {
            "executable": discovery.grok.path,
            **shared,
            "sandbox": GrokSandbox.READ_ONLY.value,
            "permission_mode": GrokPermissionMode.ALWAYS_APPROVE.value,
        }
    return profiles


def _assert_safe_placement(
    target: Path,
    directory: Path,
    *,
    repo: Path,
    state_root: Path | None,
    state_base: Path | None,
) -> None:
    """Run the three placement refusals in a fixed order, each with its code."""

    try:
        assert_outside_workspace(target, repo, label=_LABEL)
    except ConfigurationError as exc:
        if isinstance(exc, SetupError):
            raise
        raise SetupError(PATH_REFUSED_WORKSPACE, str(exc), details={"target": str(target)}) from exc
    # The effective state locations include the defaults and the environment
    # overrides, exactly as the panel itself resolves them: a config written
    # into tomorrow's state base poisons it the moment it is created
    # (state resolution flips to legacy-exact and ensure_layout refuses the
    # workspace), so the check must see the same roots the runtime will.
    paths = CommonsPaths.for_workspace(repo, state_root=state_root, state_base=state_base)
    for candidate in (directory, target):
        assert_outside_state_storage(
            candidate,
            state_root=paths.state_root,
            state_base=paths.state_base,
            label=_LABEL,
        )
    _assert_operator_owned(directory, expect_directory=True)
    _assert_operator_owned(target, expect_directory=False)


def generate_runtime_config(
    repo: str | Path,
    *,
    state_root: str | Path | None = None,
    state_base: str | Path | None = None,
    config_directory: str | Path | None = None,
    discovery: ProviderDiscovery | None = None,
) -> dict[str, Any]:
    """Write ``runtime.yaml`` and prove the launch loader accepts it.

    Returns a summary naming the written path, the catalogue path next to it,
    the profiles written, and the providers found and missing.  Raises
    :class:`SetupError` with one of the frozen codes for every refusal.
    """

    root = Path(repo).expanduser().resolve()
    found = discovery if discovery is not None else discover_providers(root)
    if not found.providers_found:
        raise SetupError(
            SETUP_NO_PROVIDER_FOUND,
            "none of claude, codex, or grok could be resolved on this machine",
            details={"discovery": found.describe()},
        )
    for required in (found.mcp, found.git):
        if not required.found:
            # The frozen refusal table has no code for a found provider with a
            # missing support executable, and this module may not extend that
            # table on its own.  The refusal therefore travels as a plain
            # ConfigurationError whose message keeps the resolver's exact
            # reasons, decomposed per candidate, so nothing is lost even
            # without a code to render by.
            reasons = "; ".join(
                f"{refusal.candidate}: {refusal.reason}" for refusal in required.refusals
            )
            raise ConfigurationError(
                f"required {required.role} executable is unavailable: {required.name}"
                + (f" ({reasons})" if reasons else "")
            )

    written = _write_and_prove(
        root,
        config_directory=config_directory,
        profiles=_profile_bodies(found),
        demo=False,
        state_root=state_root,
        state_base=state_base,
    )
    return {
        **written,
        "providers_found": list(found.providers_found),
        "providers_missing": list(found.providers_missing),
    }


def add_discovered_provider_profiles(
    repo: str | Path,
    *,
    state_root: str | Path | None = None,
    state_base: str | Path | None = None,
    config_directory: str | Path | None = None,
    discovery: ProviderDiscovery | None = None,
) -> dict[str, Any]:
    """Add newly discovered provider profiles to an unchanged generated config.

    This is deliberately not a YAML merge.  Before it writes, it regenerates
    the exact bytes for the provider families the current file already names.
    A mismatch proves the operator has edited the file, so this operation
    refuses and leaves that file alone.  A matching file may be regenerated
    with the union of those families and today's discovery through the same
    write-and-read-back discipline as the initial configuration.
    """

    root = Path(repo).expanduser().resolve()
    directory = (
        Path(config_directory).expanduser() if config_directory else default_config_directory()
    )
    target = directory / "runtime.yaml"
    configuration = load_runtime_configuration(target, workspace_root=root)
    existing = frozenset(
        profile_id.value.split("-", 1)[0]
        for profile_id in configuration.profiles.profile_ids
        if profile_id.value.split("-", 1)[0] in {"claude", "codex", "grok"}
    )
    if not existing:
        raise SetupError(
            SETUP_CONFIGURED,
            "the working runtime config does not name generated claude, codex, or grok profiles; "
            "add profiles by editing the file manually",
        )

    found = discovery if discovery is not None else discover_providers(root)
    if not found.providers_found:
        raise SetupError(
            SETUP_CONFIGURED,
            "the working runtime config cannot be proved generated because its provider "
            "is no longer discoverable; add profiles by editing the file manually",
        )
    for required in (found.mcp, found.git):
        if not required.found:
            reasons = "; ".join(
                f"{refusal.candidate}: {refusal.reason}" for refusal in required.refusals
            )
            raise ConfigurationError(
                f"required {required.role} executable is unavailable: {required.name}"
                + (f" ({reasons})" if reasons else "")
            )
    discovered = frozenset(found.providers_found)
    if not existing <= discovered:
        unavailable = ", ".join(sorted(existing - discovered))
        raise SetupError(
            SETUP_CONFIGURED,
            "the working runtime config cannot be proved generated because its provider "
            f"is no longer discoverable ({unavailable}); add profiles by editing the file manually",
        )

    catalog_path = directory / "catalog.yaml"
    expected = _runtime_config_bytes(
        catalog_path=catalog_path,
        profiles=_profile_bodies(found, providers=existing),
        demo=False,
    )
    if target.read_bytes() != expected:
        raise SetupError(
            SETUP_CONFIGURED,
            "the runtime config was edited manually; add provider profiles by editing the "
            "file manually",
        )

    added = tuple(name for name in found.providers_found if name not in existing)
    if not added:
        return {
            "path": str(target),
            "added_providers": [],
            "changed": False,
        }
    written = _write_and_prove(
        root,
        config_directory=directory,
        profiles=_profile_bodies(found, providers=existing | discovered),
        demo=False,
        state_root=state_root,
        state_base=state_base,
    )
    return {**written, "added_providers": list(added), "changed": True}


def _runtime_config_bytes(
    *, catalog_path: Path, profiles: dict[str, dict[str, Any]], demo: bool
) -> bytes:
    """Render the one deterministic runtime config representation.

    The additive operation compares these bytes with the current file before
    it writes anything, so generation and the ownership proof cannot drift.
    """

    body = {
        "profiles": profiles,
        "catalog": str(catalog_path),
        "demo": demo,
    }
    return yaml.safe_dump(body, allow_unicode=True, sort_keys=True, width=88).encode("utf-8")


def _write_and_prove(
    root: Path,
    *,
    config_directory: str | Path | None,
    profiles: dict[str, dict[str, Any]],
    demo: bool,
    state_root: str | Path | None,
    state_base: str | Path | None,
) -> dict[str, Any]:
    """The shared write discipline used by product-generated configs.

    Placement refusals in their fixed order, the post-mkdir descriptor
    tightening, the atomic 0600 replace, the seeded catalogue, and the
    read-back through the guarded launch loader -- with a refused file renamed
    to ``runtime.yaml.rejected`` rather than left posing as a working config.
    """

    directory = (
        Path(config_directory).expanduser() if config_directory else default_config_directory()
    )
    target = directory / "runtime.yaml"
    catalog_path = directory / "catalog.yaml"
    rejected_path = target.with_name(target.name + REJECTED_SUFFIX)

    _assert_safe_placement(
        target,
        directory,
        repo=root,
        state_root=Path(state_root).expanduser() if state_root else None,
        state_base=Path(state_base).expanduser() if state_base else None,
    )
    _assert_operator_owned(catalog_path, expect_directory=False)

    directory.mkdir(parents=True, exist_ok=True)
    _tighten_operator_directory(directory)

    encoded = _runtime_config_bytes(catalog_path=catalog_path, profiles=profiles, demo=demo)
    replace_operator_file(target, encoded, label=_LABEL)
    if not catalog_path.exists():
        # An operator-edited catalogue survives regeneration untouched; only
        # the very first run seeds an empty one so catalogue editing has a
        # real 0600 file to work on.
        write_role_catalog(catalog_path, empty_catalog(), workspace_root=root)

    try:
        configuration = load_runtime_configuration(target, workspace_root=root)
    except CommonsError as exc:
        os.replace(target, rejected_path)
        raise SetupError(
            CONFIG_REJECTED_BY_LOADER,
            f"generated {_LABEL} was refused by the launch loader: {exc}",
            details={"reason": str(exc), "rejected_path": str(rejected_path)},
        ) from exc
    # A stale rejected file from an earlier failed attempt is now proven
    # obsolete; regeneration leaves no litter behind.
    rejected_path.unlink(missing_ok=True)

    return {
        "path": str(target),
        "catalog_path": str(catalog_path),
        "profiles": [profile_id.value for profile_id in configuration.profiles.profile_ids],
    }
