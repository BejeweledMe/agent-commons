"""First-run setup: discovery through the one trusted resolver, safe placement.

Discovery must perform no checks of its own; the proof is a substituted
resolver whose answers are taken at face value in both directions -- paths
that do not exist are believed, refusals are kept with their exact reason.
Placement must refuse the delegated workspace and the operational state
storage (the exact trap that flips ``ensure_layout`` into ``legacy-exact``),
and the generated file must be accepted by the same guarded loader the launch
path uses.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

import agent_commons.ui.setup as setup
from agent_commons.config import CommonsPaths
from agent_commons.errors import ConfigurationError
from agent_commons.runtime.model import ExecutableResolutionError, ExecutableRole
from agent_commons.services.delegation_runtime import load_runtime_configuration

WORKSPACE_ID = "workspace.00000000000000000000000001"


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Ambient state overrides or a real operator config must not leak in."""

    monkeypatch.delenv("AGENT_COMMONS_STATE_ROOT", raising=False)
    monkeypatch.delenv("AGENT_COMMONS_STATE_BASE", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))


class RecordingResolver:
    """Stands in for ``resolve_trusted_executable`` and records every call."""

    def __init__(self, outcome: Any) -> None:
        self.calls: list[tuple[str, ExecutableRole]] = []
        self._outcome = outcome

    def __call__(self, value: str, *, workspace_root: Path, role: ExecutableRole) -> str:
        self.calls.append((value, role))
        result = self._outcome(value, role)
        if isinstance(result, Exception):
            raise result
        return result


def _install(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# -- discovery goes through the shared resolver -------------------------------


def test_discovery_calls_only_the_trusted_resolver_and_believes_its_answers(
    workspace: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A substituted resolver catches every probe; its fabricated paths, which
    exist nowhere on disk, are recorded unchallenged -- so the module owns no
    checks of its own."""

    resolver = RecordingResolver(lambda value, role: f"/nowhere/resolved/{Path(value).name}")
    monkeypatch.setattr(setup, "resolve_trusted_executable", resolver)

    discovery = setup.discover_providers(workspace["repo"])

    assert resolver.calls == [
        ("claude", ExecutableRole.PROVIDER),
        ("codex", ExecutableRole.PROVIDER),
        (str(Path(sys.executable).parent / "agent-commons-mcp"), ExecutableRole.MCP),
        ("/usr/bin/git", ExecutableRole.GIT),
    ]
    assert discovery.claude.path == "/nowhere/resolved/claude"
    assert discovery.codex.path == "/nowhere/resolved/codex"
    assert discovery.mcp.path == "/nowhere/resolved/agent-commons-mcp"
    assert discovery.git.path == "/nowhere/resolved/git"
    assert not Path("/nowhere/resolved/claude").exists()
    assert discovery.providers_found == ("claude", "codex")


def test_mcp_prefers_the_interpreter_sibling_and_falls_back_to_path(
    workspace: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``agent-commons-mcp`` is this project's own support binary: the only
    version guaranteed to match the code writing the ledger is the running
    interpreter's sibling, so that absolute path is the *first* candidate.
    PATH is the fallback for installs without a sibling console script --
    never the way a foreign checkout's older install gets picked over the
    matching one."""

    sibling = str(Path(sys.executable).parent / "agent-commons-mcp")

    def outcome(value: str, role: ExecutableRole) -> Any:
        if value == sibling:
            return ExecutableResolutionError(role, "profile executable is unavailable: mcp")
        return f"/resolved/{Path(value).name}"

    resolver = RecordingResolver(outcome)
    monkeypatch.setattr(setup, "resolve_trusted_executable", resolver)

    discovery = setup.discover_providers(workspace["repo"])

    mcp_calls = [call for call in resolver.calls if call[1] is ExecutableRole.MCP]
    assert mcp_calls == [
        (sibling, ExecutableRole.MCP),
        ("agent-commons-mcp", ExecutableRole.MCP),
    ]
    assert discovery.mcp.path == "/resolved/agent-commons-mcp"
    assert Path(sibling).is_absolute()
    # The sibling refusal is decomposed but its exact reason survives.
    assert [refusal.reason for refusal in discovery.mcp.refusals] == [
        "profile executable is unavailable: mcp"
    ]


def test_a_path_shadowing_foreign_mcp_never_wins_over_the_sibling(
    workspace: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The live incident: a second checkout's ``.venv/bin`` on PATH offered an
    ``agent-commons-mcp`` that resolution would happily accept, and PATH-first
    ordering wrote it into every generated profile.  With the sibling first,
    the foreign install is never even asked for while the sibling resolves."""

    sibling = str(Path(sys.executable).parent / "agent-commons-mcp")

    def outcome(value: str, role: ExecutableRole) -> Any:
        return f"/resolved/{Path(value).name}" if Path(value).is_absolute() else f"/other/{value}"

    resolver = RecordingResolver(outcome)
    monkeypatch.setattr(setup, "resolve_trusted_executable", resolver)

    discovery = setup.discover_providers(workspace["repo"])

    mcp_calls = [call for call in resolver.calls if call[1] is ExecutableRole.MCP]
    assert mcp_calls == [(sibling, ExecutableRole.MCP)]
    assert discovery.mcp.path == "/resolved/agent-commons-mcp"
    assert discovery.mcp.refusals == ()


def test_git_tries_the_well_known_path_then_falls_back_to_path_lookup(
    workspace: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def outcome(value: str, role: ExecutableRole) -> Any:
        if value == "/usr/bin/git":
            return ExecutableResolutionError(role, "profile executable is unavailable: git")
        return f"/opt/tools/{Path(value).name}"

    resolver = RecordingResolver(outcome)
    monkeypatch.setattr(setup, "resolve_trusted_executable", resolver)

    discovery = setup.discover_providers(workspace["repo"])

    git_calls = [call for call in resolver.calls if call[1] is ExecutableRole.GIT]
    assert git_calls == [("/usr/bin/git", ExecutableRole.GIT), ("git", ExecutableRole.GIT)]
    assert discovery.git.path == "/opt/tools/git"
    assert discovery.git.refusals[0].candidate == "/usr/bin/git"


def test_total_absence_is_named_setup_no_provider_found_with_full_detail(
    workspace: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    resolver = RecordingResolver(
        lambda value, role: ExecutableResolutionError(
            role, f"profile executable is unavailable: {value}"
        )
    )
    monkeypatch.setattr(setup, "resolve_trusted_executable", resolver)

    discovery = setup.discover_providers(workspace["repo"])
    assert discovery.providers_found == ()
    assert discovery.providers_missing == ("claude", "codex")

    with pytest.raises(setup.SetupError) as captured:
        setup.generate_runtime_config(
            workspace["repo"], config_directory=tmp_path / "config" / "agent-commons"
        )
    assert captured.value.code == "setup_no_provider_found"
    described = captured.value.details["discovery"]
    assert described["claude"]["found"] is False
    assert described["claude"]["refusals"][0]["reason"] == (
        "profile executable is unavailable: claude"
    )
    assert not (tmp_path / "config").exists()


# -- generation and the loader round-trip -------------------------------------


@pytest.fixture
def provider_bin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bindir = tmp_path / "bin"
    for name in ("claude", "codex", "agent-commons-mcp", "git"):
        _install(bindir, name)
    monkeypatch.setenv("PATH", str(bindir))
    return bindir


def test_generated_config_is_accepted_by_the_same_loader_with_private_modes(
    workspace: dict[str, Any], provider_bin: Path, tmp_path: Path
) -> None:
    config_dir = tmp_path / "operator" / "agent-commons"

    result = setup.generate_runtime_config(
        workspace["repo"],
        state_root=workspace["state_root"],
        config_directory=config_dir,
    )

    target = config_dir / "runtime.yaml"
    assert result["path"] == str(target)
    assert result["profiles"] == [
        "claude-builder",
        "claude-independent-reviewer",
        "codex-builder",
        "codex-independent-reviewer",
    ]
    assert result["providers_found"] == ["claude", "codex"]
    assert result["providers_missing"] == []
    assert _mode(config_dir) == 0o700
    assert _mode(target) == 0o600
    assert _mode(config_dir / "catalog.yaml") == 0o600

    configuration = load_runtime_configuration(target, workspace_root=workspace["repo"])
    assert [item.value for item in configuration.profiles.profile_ids] == result["profiles"]

    document = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert document["demo"] is False
    assert document["catalog"] == str(config_dir / "catalog.yaml")
    for body in document["profiles"].values():
        assert Path(body["executable"]).is_absolute()
        assert Path(body["mcp_executable"]).is_absolute()
        assert Path(body["git_executable"]).is_absolute()
    assert document["profiles"]["codex-builder"]["sandbox"] == "workspace-write"
    assert document["profiles"]["codex-independent-reviewer"]["sandbox"] == "read-only"
    assert document["profiles"]["claude-builder"]["permission_mode"] == "acceptEdits"
    assert document["profiles"]["claude-independent-reviewer"]["permission_mode"] == "dontAsk"
    assert "trusted_workspace" not in document["profiles"]["claude-independent-reviewer"]


def test_partial_discovery_writes_exactly_the_found_providers_profiles(
    workspace: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bindir = tmp_path / "bin"
    for name in ("claude", "agent-commons-mcp", "git"):
        _install(bindir, name)
    monkeypatch.setenv("PATH", str(bindir))
    config_dir = tmp_path / "operator" / "agent-commons"

    result = setup.generate_runtime_config(workspace["repo"], config_directory=config_dir)

    assert result["profiles"] == ["claude-builder", "claude-independent-reviewer"]
    assert result["providers_missing"] == ["codex"]
    document = yaml.safe_load((config_dir / "runtime.yaml").read_text(encoding="utf-8"))
    assert sorted(document["profiles"]) == ["claude-builder", "claude-independent-reviewer"]


def test_regeneration_leaves_no_litter_and_preserves_an_edited_catalogue(
    workspace: dict[str, Any], provider_bin: Path, tmp_path: Path
) -> None:
    config_dir = tmp_path / "operator" / "agent-commons"
    setup.generate_runtime_config(workspace["repo"], config_directory=config_dir)
    edited = (
        "skills:\n- id: reviewer\n  title: Reviewer\n  description: ''\n"
        "  instruction: Review carefully.\ntools: []\n"
    )
    (config_dir / "catalog.yaml").write_text(edited, encoding="utf-8")

    setup.generate_runtime_config(workspace["repo"], config_directory=config_dir)

    assert sorted(item.name for item in config_dir.iterdir()) == ["catalog.yaml", "runtime.yaml"]
    assert (config_dir / "catalog.yaml").read_text(encoding="utf-8") == edited


def test_the_demo_config_needs_no_resolvable_binary_and_reads_back_demo_true(
    workspace: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The way out of `setup_no_provider_found`: written with nothing on PATH.

    The demo generator consults no discovery -- requiring nothing resolvable
    is its entire point -- and the file it writes is a constant modulo the
    catalogue path: all four fixed profiles over the inert demo placeholder,
    accepted by the same guarded loader with ``demo: true``, under the same
    private modes and with the same catalogue seeded beside it.
    """

    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    config_dir = tmp_path / "operator" / "agent-commons"

    result = setup.generate_demo_runtime_config(
        workspace["repo"],
        state_root=workspace["state_root"],
        config_directory=config_dir,
    )

    target = config_dir / "runtime.yaml"
    assert result["path"] == str(target)
    assert result["demo"] is True
    assert result["profiles"] == [
        "claude-builder",
        "claude-independent-reviewer",
        "codex-builder",
        "codex-independent-reviewer",
    ]
    assert _mode(config_dir) == 0o700
    assert _mode(target) == 0o600
    assert _mode(config_dir / "catalog.yaml") == 0o600

    configuration = load_runtime_configuration(target, workspace_root=workspace["repo"])
    assert configuration.demo is True

    document = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert document["demo"] is True
    from agent_commons.runtime.model import DEMO_UNRESOLVED_EXECUTABLE

    for body in document["profiles"].values():
        # No real path leaves the machine's PATH for this file; the DemoRunner
        # never launches what these fields name.
        assert body["executable"] == DEMO_UNRESOLVED_EXECUTABLE
        assert body["mcp_executable"] == DEMO_UNRESOLVED_EXECUTABLE
        assert body["git_executable"] == DEMO_UNRESOLVED_EXECUTABLE
    # The launch modes stay the fixed, never-UI-selectable ones.
    assert document["profiles"]["codex-builder"]["sandbox"] == "workspace-write"
    assert document["profiles"]["codex-independent-reviewer"]["sandbox"] == "read-only"
    assert document["profiles"]["claude-builder"]["permission_mode"] == "acceptEdits"
    assert document["profiles"]["claude-independent-reviewer"]["permission_mode"] == "dontAsk"


def test_the_demo_generator_shares_the_placement_refusals(
    workspace: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same discipline, not a weaker copy: one refusal from each family."""

    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    with pytest.raises(setup.SetupError) as inside_workspace:
        setup.generate_demo_runtime_config(
            workspace["repo"], config_directory=workspace["repo"] / "config"
        )
    assert inside_workspace.value.code == "setup_path_refused_workspace"
    assert not (workspace["repo"] / "config").exists()

    linked = tmp_path / "linked-config"
    linked.symlink_to(tmp_path)
    with pytest.raises(setup.SetupError) as ownership:
        setup.generate_demo_runtime_config(workspace["repo"], config_directory=linked)
    assert ownership.value.code == "setup_path_refused_ownership"


# -- placement refusals --------------------------------------------------------


def test_a_target_inside_the_workspace_is_refused(
    workspace: dict[str, Any], provider_bin: Path
) -> None:
    with pytest.raises(setup.SetupError) as captured:
        setup.generate_runtime_config(
            workspace["repo"], config_directory=workspace["repo"] / "config"
        )
    assert captured.value.code == "setup_path_refused_workspace"
    assert not (workspace["repo"] / "config").exists()


def test_a_target_inside_the_state_base_is_refused_and_the_base_stays_clean(
    workspace: dict[str, Any], provider_bin: Path, tmp_path: Path
) -> None:
    """The exact trap of tests/core/test_config.py: one foreign file inside the
    state base flips resolution to legacy-exact and ``ensure_layout`` then
    refuses the workspace.  The generator must refuse first."""

    base = tmp_path / "operator-state-base"

    for target in (base, base / "agent-commons"):
        with pytest.raises(ConfigurationError) as captured:
            setup.generate_runtime_config(
                workspace["repo"], state_base=base, config_directory=target
            )
        assert captured.value.code == "setup_path_refused_state_base"
    assert not base.exists()

    # A correctly placed generation afterwards leaves the base usable: state
    # resolution stays namespaced instead of falling into legacy-exact.
    setup.generate_runtime_config(
        workspace["repo"],
        state_base=base,
        config_directory=tmp_path / "operator" / "agent-commons",
    )
    paths = CommonsPaths.for_workspace(
        workspace["repo"], state_base=base, workspace_id=WORKSPACE_ID
    )
    assert paths.state_mode == "base"
    paths.ensure_layout()
    assert paths.state_mode == "base"


def test_a_target_inside_an_exact_state_root_is_refused(
    workspace: dict[str, Any], provider_bin: Path, tmp_path: Path
) -> None:
    state_root = tmp_path / "exact-state"
    with pytest.raises(ConfigurationError) as captured:
        setup.generate_runtime_config(
            workspace["repo"],
            state_root=state_root,
            config_directory=state_root / "agent-commons",
        )
    assert captured.value.code == "setup_path_refused_state_base"
    assert not state_root.exists()


def test_a_symlink_target_is_refused_as_an_ownership_violation(
    workspace: dict[str, Any], provider_bin: Path, tmp_path: Path
) -> None:
    config_dir = tmp_path / "operator" / "agent-commons"
    config_dir.mkdir(parents=True, mode=0o700)
    elsewhere = tmp_path / "elsewhere.yaml"
    elsewhere.write_text("profiles: {}\n", encoding="utf-8")
    (config_dir / "runtime.yaml").symlink_to(elsewhere)

    with pytest.raises(setup.SetupError) as captured:
        setup.generate_runtime_config(workspace["repo"], config_directory=config_dir)
    assert captured.value.code == "setup_path_refused_ownership"


def test_a_world_writable_directory_is_refused_as_an_ownership_violation(
    workspace: dict[str, Any], provider_bin: Path, tmp_path: Path
) -> None:
    config_dir = tmp_path / "operator" / "agent-commons"
    config_dir.mkdir(parents=True)
    config_dir.chmod(0o777)

    with pytest.raises(setup.SetupError) as captured:
        setup.generate_runtime_config(workspace["repo"], config_directory=config_dir)
    assert captured.value.code == "setup_path_refused_ownership"
    assert not (config_dir / "runtime.yaml").exists()


def test_the_post_mkdir_tightening_refuses_a_symlinked_directory(tmp_path: Path) -> None:
    """Placement is checked before ``mkdir``, so the tightening that follows
    creation must bind to the real inode: opened ``O_NOFOLLOW``, a symlink
    planted where the config directory should be is refused as an ownership
    violation instead of being chmodded through."""

    real = tmp_path / "elsewhere"
    real.mkdir(mode=0o755)
    link = tmp_path / "agent-commons"
    link.symlink_to(real)

    with pytest.raises(setup.SetupError) as captured:
        setup._tighten_operator_directory(link)
    assert captured.value.code == "setup_path_refused_ownership"
    assert _mode(real) == 0o755  # the target was never touched

    real.chmod(0o750)
    setup._tighten_operator_directory(real)
    assert _mode(real) == 0o700


# -- loader rejection ----------------------------------------------------------


def test_a_loader_refusal_renames_the_file_and_names_the_reason(
    workspace: dict[str, Any], provider_bin: Path, tmp_path: Path
) -> None:
    config_dir = tmp_path / "operator" / "agent-commons"
    target = config_dir / "runtime.yaml"
    rejected = config_dir / "runtime.yaml.rejected"

    with pytest.MonkeyPatch.context() as patch:

        def refuse(path: Any, *, workspace_root: Any = None) -> Any:
            raise ConfigurationError("loader said no")

        patch.setattr(setup, "load_runtime_configuration", refuse)
        with pytest.raises(setup.SetupError) as captured:
            setup.generate_runtime_config(workspace["repo"], config_directory=config_dir)

    assert captured.value.code == "setup_config_rejected_by_loader"
    assert captured.value.details["reason"] == "loader said no"
    assert not target.exists()
    assert rejected.is_file()
    assert "profiles" in rejected.read_text(encoding="utf-8")

    # A later successful generation proves the rejected file was litter and
    # removes it.
    setup.generate_runtime_config(workspace["repo"], config_directory=config_dir)
    assert target.is_file()
    assert not rejected.exists()


# -- states --------------------------------------------------------------------


def test_setup_states_are_named_with_the_frozen_codes(
    workspace: dict[str, Any], provider_bin: Path, tmp_path: Path
) -> None:
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    assert setup.setup_state(plain) == "setup_not_a_repository"
    assert setup.setup_state(tmp_path / "absent") == "setup_not_a_repository"

    bare = tmp_path / "bare-repo"
    (bare / ".git").mkdir(parents=True)
    assert setup.setup_state(bare) == "setup_uninitialized"

    # XDG_CONFIG_HOME points at an empty directory, so the default runtime
    # config path does not exist yet.
    assert setup.setup_state(workspace["repo"]) == "setup_unconfigured"

    setup.generate_runtime_config(
        workspace["repo"], config_directory=setup.default_config_directory()
    )
    assert setup.setup_state(workspace["repo"]) == "setup_configured"

    # A named config earns `configured` the same way the default path does:
    # by being accepted by the loader, not by existing.
    named = tmp_path / "named.yaml"
    assert setup.setup_state(workspace["repo"], profile_config=named) == "setup_unconfigured"
    generated = setup.default_runtime_config_path().read_text(encoding="utf-8")
    named.write_text(generated, encoding="utf-8")
    named.chmod(0o600)
    assert setup.setup_state(workspace["repo"], profile_config=named) == "setup_configured"


def test_configured_means_the_loader_accepted_the_file_not_that_it_exists(
    workspace: dict[str, Any], provider_bin: Path, tmp_path: Path
) -> None:
    """A present-but-refused config must never pose as a configured
    environment: every later panel start re-earns `configured` through the
    same guarded loader the launch path uses.  A file the loader refuses --
    an empty registry, an unknown field, loose permissions -- names itself
    `setup_config_rejected_by_loader` instead."""

    named = tmp_path / "named.yaml"
    named.write_text("profiles: {}\n", encoding="utf-8")
    named.chmod(0o600)
    assert (
        setup.setup_state(workspace["repo"], profile_config=named)
        == "setup_config_rejected_by_loader"
    )

    # The same dishonesty on the default path: a generated config that later
    # grows a field the loader does not know stops being `configured` on the
    # very next look, without anyone regenerating anything.
    setup.generate_runtime_config(
        workspace["repo"], config_directory=setup.default_config_directory()
    )
    target = setup.default_runtime_config_path()
    assert setup.setup_state(workspace["repo"]) == "setup_configured"
    target.write_text(target.read_text(encoding="utf-8") + "bogus_field: true\n", encoding="utf-8")
    assert setup.setup_state(workspace["repo"]) == "setup_config_rejected_by_loader"

    # A permissions regression is the same state: the loader would refuse this
    # file at launch, so setup must not call the environment configured.
    target.write_text(
        target.read_text(encoding="utf-8").replace("bogus_field: true\n", ""),
        encoding="utf-8",
    )
    assert setup.setup_state(workspace["repo"]) == "setup_configured"
    target.chmod(0o666)
    assert setup.setup_state(workspace["repo"]) == "setup_config_rejected_by_loader"


def test_default_config_directory_honours_absolute_xdg_and_falls_back_to_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert setup.default_config_directory() == tmp_path / "xdg" / "agent-commons"
    assert setup.default_runtime_config_path().name == "runtime.yaml"

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative/config")
    assert setup.default_config_directory() == tmp_path / "home" / ".config" / "agent-commons"
    monkeypatch.delenv("XDG_CONFIG_HOME")
    assert setup.default_config_directory() == tmp_path / "home" / ".config" / "agent-commons"


def test_the_refusal_codes_are_the_frozen_contract_strings() -> None:
    assert setup.SETUP_NOT_A_REPOSITORY == "setup_not_a_repository"
    assert setup.SETUP_UNINITIALIZED == "setup_uninitialized"
    assert setup.SETUP_UNCONFIGURED == "setup_unconfigured"
    # The one state this table once left unpinned, and the one that drifted:
    # the contract froze `setup_configured` while the code said "configured",
    # and the frontend had to accept both spellings blind.  Prefixed like its
    # three sibling states, and pinned so it cannot drift again.
    assert setup.SETUP_CONFIGURED == "setup_configured"
    assert setup.SETUP_NO_PROVIDER_FOUND == "setup_no_provider_found"
    assert setup.PATH_REFUSED_WORKSPACE == "setup_path_refused_workspace"
    assert setup.PATH_REFUSED_STATE == "setup_path_refused_state_base"
    assert setup.PATH_REFUSED_OWNERSHIP == "setup_path_refused_ownership"
    assert setup.CONFIG_REJECTED_BY_LOADER == "setup_config_rejected_by_loader"
