from __future__ import annotations

from multiprocessing import get_context
from pathlib import Path

import pytest
import yaml

import agent_commons.integrations.installer as installer_module
from agent_commons.core.ids import is_typed_id
from agent_commons.errors import ConfigurationError
from agent_commons.integrations import (
    MANAGED_BLOCK_END,
    MANAGED_BLOCK_START,
    initialize_workspace,
)


def _concurrent_init_worker(root: str, start: object, results: object) -> None:
    start.wait(timeout=10)  # type: ignore[attr-defined]
    try:
        report = initialize_workspace(root)
        results.put(("ok", report.changed))  # type: ignore[attr-defined]
    except Exception as exc:
        results.put((type(exc).__name__, str(exc)))  # type: ignore[attr-defined]


def test_fresh_install_creates_shared_workspace_and_both_integrations(tmp_path: Path) -> None:
    report = initialize_workspace(tmp_path, workspace_name="demo")

    assert report.workspace == ".agent-commons"
    assert is_typed_id(report.workspace_id, "workspace")
    assert report.integrations == ("codex", "claude")
    assert report.changed
    assert all(not Path(change.path).is_absolute() for change in report.changes)
    assert (tmp_path / ".agent-commons" / "ONBOARDING.md").is_file()
    assert (tmp_path / ".agent-commons" / "workspace.yaml").is_file()
    workspace_ignore = (tmp_path / ".agent-commons" / ".gitignore").read_text()
    assert "cache/" in workspace_ignore
    assert ".state/" in workspace_ignore
    for directory in ("events", "manifests", "blobs", "cache"):
        assert (tmp_path / ".agent-commons" / directory).is_dir()
    assert not (tmp_path / ".agent-commons" / "messages").exists()

    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    claude = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert agents == claude
    assert agents.count(MANAGED_BLOCK_START) == 1
    assert agents.count(MANAGED_BLOCK_END) == 1
    assert ".agent-commons/ONBOARDING.md" in agents

    config = yaml.safe_load((tmp_path / ".agent-commons" / "workspace.yaml").read_text())
    assert config["schema"] == "agent-commons.workspace.v1"
    assert is_typed_id(config["workspace_id"], "workspace")
    assert config["workspace"]["name"] == "demo"
    assert set(config["workspace"]) == {"name", "guidance"}
    assert "policy" not in config
    assert "coordination" not in config
    assert "orientation" not in config

    skill_names = {
        "commons-start",
        "commons-coordinate",
        "commons-share",
        "commons-review",
        "commons-record",
        "commons-handoff",
    }
    for skill_name in skill_names:
        codex_skill = tmp_path / ".agents" / "skills" / skill_name / "SKILL.md"
        claude_skill = tmp_path / ".claude" / "skills" / skill_name / "SKILL.md"
        assert codex_skill.is_file()
        assert codex_skill.read_bytes() == claude_skill.read_bytes()
        assert (codex_skill.parent / "agents" / "openai.yaml").is_file()


def test_install_preserves_existing_instruction_text_outside_markers(tmp_path: Path) -> None:
    agents_prefix = "# Existing Codex rules\n\nKeep this exactly."
    claude_prefix = "# Existing Claude rules\r\n\r\nKeep this too.\r\n"
    (tmp_path / "AGENTS.md").write_text(agents_prefix, encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text(claude_prefix, encoding="utf-8", newline="")

    initialize_workspace(tmp_path)

    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8").startswith(agents_prefix)
    assert (tmp_path / "CLAUDE.md").read_bytes().startswith(claude_prefix.encode("utf-8"))


def test_second_install_is_idempotent(tmp_path: Path) -> None:
    first = initialize_workspace(tmp_path)
    snapshots = {
        path: (tmp_path / path).read_bytes()
        for path in (
            "AGENTS.md",
            "CLAUDE.md",
            ".agent-commons/ONBOARDING.md",
            ".agent-commons/.gitignore",
            ".agent-commons/workspace.yaml",
        )
    }

    second = initialize_workspace(tmp_path)

    assert first.changed
    assert not second.changed
    assert all(change.status == "unchanged" for change in second.changes)
    assert snapshots == {path: (tmp_path / path).read_bytes() for path in snapshots}
    assert (tmp_path / "AGENTS.md").read_text().count(MANAGED_BLOCK_START) == 1


def test_existing_managed_block_is_replaced_without_touching_surrounding_text(
    tmp_path: Path,
) -> None:
    before = "# Project rules\n\n"
    after = "\n\n## Local appendix\nDo not alter.\n"
    original = f"{before}{MANAGED_BLOCK_START}\nold body\n{MANAGED_BLOCK_END}{after}"
    (tmp_path / "AGENTS.md").write_text(original, encoding="utf-8")

    initialize_workspace(tmp_path, integrations=("codex",))

    updated = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert updated[: updated.index(MANAGED_BLOCK_START)] == before
    marker_end = updated.index(MANAGED_BLOCK_END) + len(MANAGED_BLOCK_END)
    assert updated[marker_end:] == after
    assert "old body" not in updated


@pytest.mark.parametrize(
    "malformed",
    [
        f"text\n{MANAGED_BLOCK_START}\nmissing end\n",
        f"{MANAGED_BLOCK_END}\n{MANAGED_BLOCK_START}\n",
        f"{MANAGED_BLOCK_START}\na\n{MANAGED_BLOCK_END}\n{MANAGED_BLOCK_START}\nb\n{MANAGED_BLOCK_END}\n",
    ],
)
def test_malformed_markers_fail_before_any_install_write(tmp_path: Path, malformed: str) -> None:
    path = tmp_path / "CLAUDE.md"
    path.write_text(malformed, encoding="utf-8")

    with pytest.raises(ConfigurationError, match="marker"):
        initialize_workspace(tmp_path)

    assert path.read_text(encoding="utf-8") == malformed
    assert not (tmp_path / ".agent-commons").exists()
    assert not (tmp_path / "AGENTS.md").exists()


def test_unknown_integration_fails_without_writes(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="unsupported integration"):
        initialize_workspace(tmp_path, integrations=("unknown-client",))

    assert list(tmp_path.iterdir()) == []


def test_existing_workspace_configuration_is_operator_owned(tmp_path: Path) -> None:
    workspace = tmp_path / ".agent-commons"
    workspace.mkdir()
    workspace_id = "workspace.01K00000000000000000000000"
    custom = (
        "schema: agent-commons.workspace.v1\n"
        f"workspace_id: {workspace_id}\n"
        "workspace:\n"
        "  name: custom\n"
        "operator_setting: keep-me\n"
    )
    (workspace / "workspace.yaml").write_text(custom, encoding="utf-8")

    report = initialize_workspace(tmp_path, integrations=())

    assert (workspace / "workspace.yaml").read_text(encoding="utf-8") == custom
    status = {change.path: change.status for change in report.changes}
    assert status[".agent-commons/workspace.yaml"] == "unchanged"
    assert report.workspace_id == workspace_id


def test_invalid_existing_workspace_configuration_fails_without_partial_writes(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / ".agent-commons"
    workspace.mkdir()
    config = workspace / "workspace.yaml"
    config.write_text("schema: unrelated.v1\n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="unsupported schema"):
        initialize_workspace(tmp_path)

    assert config.read_text(encoding="utf-8") == "schema: unrelated.v1\n"
    assert not (workspace / "ONBOARDING.md").exists()
    assert not (tmp_path / "AGENTS.md").exists()


def test_locally_modified_onboarding_requires_explicit_replacement(tmp_path: Path) -> None:
    initialize_workspace(tmp_path, integrations=())
    onboarding = tmp_path / ".agent-commons" / "ONBOARDING.md"
    onboarding.write_text("local change\n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="replace_onboarding"):
        initialize_workspace(tmp_path, integrations=())

    assert onboarding.read_text(encoding="utf-8") == "local change\n"

    report = initialize_workspace(tmp_path, integrations=(), replace_onboarding=True)
    assert report.changed
    assert onboarding.read_text(encoding="utf-8").startswith("# Agent Commons onboarding")


def test_locally_modified_skill_requires_explicit_replacement(tmp_path: Path) -> None:
    initialize_workspace(tmp_path, integrations=("codex",))
    skill = tmp_path / ".agents" / "skills" / "commons-start" / "SKILL.md"
    skill.write_text("local skill\n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="replace_skills"):
        initialize_workspace(tmp_path, integrations=("codex",))

    assert skill.read_text(encoding="utf-8") == "local skill\n"

    report = initialize_workspace(
        tmp_path,
        integrations=("codex",),
        replace_skills=True,
    )
    assert report.changed
    assert skill.read_text(encoding="utf-8").startswith("---\nname: commons-start")


def test_workspace_name_is_rendered_as_data_not_yaml(tmp_path: Path) -> None:
    initialize_workspace(tmp_path, integrations=(), workspace_name='demo: "quoted" value')

    config = yaml.safe_load((tmp_path / ".agent-commons" / "workspace.yaml").read_text())
    assert config["workspace"]["name"] == 'demo: "quoted" value'


def test_workspace_name_rejects_control_characters_before_writing(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="control characters"):
        initialize_workspace(tmp_path, workspace_name="unsafe\nname")

    assert list(tmp_path.iterdir()) == []


def test_symlinked_managed_target_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("untouched\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").symlink_to(outside)

    with pytest.raises(ConfigurationError, match="symlinked"):
        initialize_workspace(tmp_path, integrations=("codex",))

    assert outside.read_text(encoding="utf-8") == "untouched\n"
    assert not (tmp_path / ".agent-commons").exists()


def test_symlinked_skill_root_is_rejected_without_writes(tmp_path: Path) -> None:
    outside = tmp_path / "outside-skills"
    outside.mkdir()
    (tmp_path / ".agents").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ConfigurationError, match="symlinked integration directory"):
        initialize_workspace(tmp_path, integrations=("codex",))

    assert list(outside.iterdir()) == []
    assert not (tmp_path / ".agent-commons").exists()
    assert not (tmp_path / "AGENTS.md").exists()


def test_initialization_does_not_modify_git_state(tmp_path: Path) -> None:
    git = tmp_path / ".git"
    git.mkdir()
    index = git / "index"
    sentinel = b"operator-owned-index\x00bytes"
    index.write_bytes(sentinel)
    before = {path.relative_to(git): path.read_bytes() for path in git.rglob("*") if path.is_file()}

    initialize_workspace(tmp_path)

    after = {path.relative_to(git): path.read_bytes() for path in git.rglob("*") if path.is_file()}
    assert after == before
    assert index.read_bytes() == sentinel


def test_concurrent_instruction_change_is_not_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agents = tmp_path / "AGENTS.md"
    agents.write_text("initial project rules\n", encoding="utf-8")
    original_verify = installer_module._verify_preconditions

    def mutate_then_verify(planned: object) -> None:
        agents.write_text("concurrent project rules\n", encoding="utf-8")
        original_verify(planned)  # type: ignore[arg-type]

    monkeypatch.setattr(installer_module, "_verify_preconditions", mutate_then_verify)

    with pytest.raises(ConfigurationError, match="changed during preflight"):
        initialize_workspace(tmp_path, integrations=("codex",))

    assert agents.read_text(encoding="utf-8") == "concurrent project rules\n"
    assert not (tmp_path / ".agent-commons" / "ONBOARDING.md").exists()


def test_change_after_preflight_but_before_publication_is_not_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agents = tmp_path / "AGENTS.md"
    agents.write_text("initial project rules\n", encoding="utf-8")
    original_atomic = installer_module._atomic_write
    injected = False

    def mutate_then_publish(item: object) -> None:
        nonlocal injected
        if getattr(item, "path", None) == agents and not injected:
            agents.write_text("late concurrent project rules\n", encoding="utf-8")
            injected = True
        original_atomic(item)  # type: ignore[arg-type]

    monkeypatch.setattr(installer_module, "_atomic_write", mutate_then_publish)

    with pytest.raises(ConfigurationError, match="changed during publication"):
        initialize_workspace(tmp_path, integrations=("codex",))

    assert injected
    assert agents.read_text(encoding="utf-8") == "late concurrent project rules\n"
    assert MANAGED_BLOCK_START not in agents.read_text(encoding="utf-8")


def test_parallel_initializers_serialize_and_converge_idempotently(tmp_path: Path) -> None:
    context = get_context("fork")
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_concurrent_init_worker,
            args=(str(tmp_path), start, results),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    start.set()
    outcomes = [results.get(timeout=20) for _ in processes]
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0

    assert sorted(outcomes) == [("ok", False), ("ok", True)]
    assert (tmp_path / "AGENTS.md").read_text().count(MANAGED_BLOCK_START) == 1
    assert (tmp_path / "CLAUDE.md").read_text().count(MANAGED_BLOCK_START) == 1


def test_templates_are_portable_and_use_one_client_guidance_contract() -> None:
    template_root = Path(__file__).parents[2] / "src" / "agent_commons" / "resources" / "templates"
    agents = (template_root / "AGENTS_BLOCK.md").read_text(encoding="utf-8")
    claude = (template_root / "CLAUDE_BLOCK.md").read_text(encoding="utf-8")
    assert agents == claude

    docs = Path(__file__).parents[2] / "docs"
    owned_docs = (docs / "PROTOCOL.md", docs / "THREAT_MODEL.md", docs / "USER_WORKFLOWS.md")
    for path in (*template_root.iterdir(), *owned_docs):
        if path.is_file():
            content = path.read_text(encoding="utf-8")
            for absolute_prefix in ("/" + "home" + "/", "/" + "Users" + "/"):
                assert absolute_prefix not in content
            for forbidden in ("live" + "ness", "ap" + "cer", "bp" + "cer"):
                assert forbidden not in content.lower()


def test_repository_and_packaged_skill_copies_are_identical() -> None:
    root = Path(__file__).parents[2]
    names = (
        "commons-start",
        "commons-coordinate",
        "commons-share",
        "commons-review",
        "commons-record",
        "commons-handoff",
    )
    for name in names:
        copies = [
            root / ".agents" / "skills" / name,
            root / ".claude" / "skills" / name,
            root / "src" / "agent_commons" / "resources" / "skills" / name,
        ]
        for relative in (Path("SKILL.md"), Path("agents") / "openai.yaml"):
            bodies = [(directory / relative).read_bytes() for directory in copies]
            assert bodies[0] == bodies[1] == bodies[2]
