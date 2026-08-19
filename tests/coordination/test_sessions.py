from __future__ import annotations

import json
import stat
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agent_commons.coordination import (
    SessionRegistry,
    SourceProducer,
    discover_operational_state_root,
)
from agent_commons.errors import (
    ConfigurationError,
    IntegrityError,
    LifecycleConflictError,
    SecurityPolicyError,
    ValidationError,
)
from agent_commons.services import CommonsManager


class Clock:
    def __init__(self, value: float = 1_720_000_000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def init_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    return path


def open_session(registry: SessionRegistry, *, suffix: str = "a", **overrides):
    values = {
        "stable_instance_id": f"codex-thread-{suffix}-12345678",
        "principal": f"local-operator-{suffix}",
        "client": "codex",
        "software": "codex-cli",
        "model_family": "gpt",
        "model": "gpt-test-model",
        "role": "builder",
        "capabilities": ("task:write",),
    }
    values.update(overrides)
    return registry.open_session(**values)


def test_state_root_is_in_git_common_directory(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    expected_common = subprocess.check_output(
        ["git", "rev-parse", "--git-common-dir"], cwd=repo, text=True
    ).strip()
    expected = (repo / expected_common).resolve() / "agent-commons-state"
    assert discover_operational_state_root(repo) == expected

    registry = SessionRegistry(repo)
    assert stat.S_IMODE(registry.state_root.stat().st_mode) == 0o700
    assert stat.S_IMODE(registry.event_root.stat().st_mode) == 0o700


def test_session_registry_rejects_a_symlinked_private_directory(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    state_root = tmp_path / "state"
    state_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (state_root / "sessions").symlink_to(outside, target_is_directory=True)

    with pytest.raises(IntegrityError, match="session operational directory"):
        SessionRegistry(repo, state_root=state_root)


def test_worktrees_discover_the_same_operational_state(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    (repo / "README.md").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Agent Commons Tests",
            "-c",
            "user.email=tests@example.invalid",
            "commit",
            "-q",
            "-m",
            "fixture",
        ],
        cwd=repo,
        check=True,
    )
    worktree = tmp_path / "worktree"
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "other", str(worktree)],
        cwd=repo,
        check=True,
    )

    assert discover_operational_state_root(repo) == discover_operational_state_root(worktree)


def test_manager_namespaces_nested_workspaces_but_linked_worktrees_share_owner(
    tmp_path: Path,
) -> None:
    repo = init_repo(tmp_path / "repo")
    CommonsManager.initialize(repo, integrations=(), workspace_name="shared")
    subprocess.run(["git", "add", ".agent-commons"], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Agent Commons Tests",
            "-c",
            "user.email=tests@example.invalid",
            "commit",
            "-q",
            "-m",
            "workspace",
        ],
        cwd=repo,
        check=True,
    )
    linked = tmp_path / "linked"
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "linked", str(linked)],
        cwd=repo,
        check=True,
    )
    nested = init_repo(repo / "nested")
    CommonsManager.initialize(nested, integrations=(), workspace_name="nested")

    main_manager = CommonsManager(repo)
    linked_manager = CommonsManager(linked)
    nested_manager = CommonsManager(nested)

    assert main_manager.workspace_id == linked_manager.workspace_id
    assert main_manager.paths.state_root == linked_manager.paths.state_root
    assert nested_manager.workspace_id != main_manager.workspace_id
    assert nested_manager.paths.state_root != main_manager.paths.state_root


def test_two_workspaces_under_one_operator_base_do_not_share_sessions(tmp_path: Path) -> None:
    base = tmp_path / "operator-state"
    managers: list[CommonsManager] = []
    for name in ("alpha", "beta"):
        repo = tmp_path / name
        repo.mkdir()
        CommonsManager.initialize(repo, integrations=(), workspace_name=name)
        managers.append(CommonsManager(repo, state_base=base))

    first = managers[0].start_session(
        stable_instance_id="alpha-window-12345678",
        principal="operator",
        client="codex",
        software="codex-cli",
        role="builder",
    )

    assert managers[0].show_session(first["session_id"])["session_id"] == first["session_id"]
    with pytest.raises(ValidationError, match="does not exist"):
        managers[1].show_session(first["session_id"])


def test_ambiguous_session_only_exact_root_fails_before_registry_read(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="ambiguous")
    state = tmp_path / "legacy-state"
    event_root = state / "sessions" / "events"
    event_root.mkdir(parents=True)
    foreign_bytes = b"not a session audit event\n"
    (event_root / "00000000000000000001-deadbeefdeadbeefdeadbeefdeadbeef.json").write_bytes(
        foreign_bytes
    )

    with pytest.raises(ConfigurationError, match="ownership is missing") as captured:
        CommonsManager(repo, state_root=state)

    assert captured.value.code == "state_owner_unproven"
    assert not (state / "workspace-owner.json").exists()
    assert next(event_root.iterdir()).read_bytes() == foreign_bytes


def test_session_registration_is_explicit_and_source_producer_is_separate(
    tmp_path: Path,
) -> None:
    repo = init_repo(tmp_path / "repo")
    registry = SessionRegistry(repo)

    with pytest.raises(ValidationError, match="stable_instance_id"):
        registry.open_session(
            stable_instance_id="",
            principal="operator",
            client="codex",
            software="codex-cli",
            role="builder",
        )
    with pytest.raises(LifecycleConflictError, match="explicit active session"):
        registry.assert_can_write(None)

    session = open_session(
        registry,
        source_producer=SourceProducer(
            client="claude-code",
            software="claude-cli",
            model_family="claude",
            model="claude-test-model",
            principal="source-agent",
            external_session_id="claude-session-42",
        ),
    )
    actor = registry.assert_can_write(session.session_id, capability="task:write")
    assert actor["client"] == "codex"
    assert actor["software"] == "codex-cli"
    assert actor["principal_id"] == "local-operator-a"
    assert actor["role_id"] == "builder"
    assert actor["model_family"] == "gpt"
    assert actor["source_producer"]["client"] == "claude-code"
    assert actor["source_producer"]["external_session_id"] == "claude-session-42"
    assert "nonce" not in actor


def test_exact_open_retry_reuses_session_and_conflicting_identity_fails(
    tmp_path: Path,
) -> None:
    repo = init_repo(tmp_path / "repo")
    registry = SessionRegistry(repo)
    first = open_session(registry)
    repeated = open_session(registry)
    assert repeated.session_id == first.session_id
    assert repeated.nonce == first.nonce

    with pytest.raises(LifecycleConflictError, match="different identity metadata"):
        open_session(registry, role="reviewer")


def test_session_heartbeat_close_expiry_and_capability_checks(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    clock = Clock()
    registry = SessionRegistry(repo, clock=clock)
    opened = open_session(registry, capabilities=("task:write",), ttl_seconds=10)
    assert registry.is_available(opened.session_id) is True
    assert registry.is_available("session." + "f" * 32) is False

    with pytest.raises(LifecycleConflictError, match="required capability"):
        registry.require_active(opened.session_id, capability="claim:break")

    clock.value += 5
    renewed = registry.heartbeat(opened.session_id, nonce=opened.nonce, ttl_seconds=20)
    assert renewed.nonce != opened.nonce
    with pytest.raises(LifecycleConflictError, match="nonce"):
        registry.close(opened.session_id, nonce=opened.nonce)

    closed = registry.close(opened.session_id, nonce=renewed.nonce)
    assert closed.status == "closed"
    assert registry.is_available(opened.session_id) is False
    with pytest.raises(LifecycleConflictError, match="closed"):
        registry.require_active(opened.session_id)

    expiring = open_session(registry, suffix="b", ttl_seconds=1)
    clock.value += 2
    assert registry.is_available(expiring.session_id) is False
    with pytest.raises(LifecycleConflictError, match="expired"):
        registry.require_active(expiring.session_id)


def test_sensitive_registration_leaves_no_audit_event(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    registry = SessionRegistry(repo)
    secret = "sk-proj-" + "Z" * 24

    with pytest.raises(SecurityPolicyError) as caught:
        open_session(registry, role=secret)

    assert secret not in str(caught.value)
    assert list(registry.event_root.glob("*.json")) == []


def test_concurrent_registration_of_one_instance_has_one_identity(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    state_root = tmp_path / "shared-state"

    def register(_: int) -> str:
        registry = SessionRegistry(repo, state_root=state_root)
        return open_session(registry).session_id

    with ThreadPoolExecutor(max_workers=12) as pool:
        session_ids = list(pool.map(register, range(12)))

    assert len(set(session_ids)) == 1
    registry = SessionRegistry(repo, state_root=state_root)
    assert len(registry.list_sessions(active_only=True)) == 1
    assert len(list(registry.event_root.glob("*.json"))) == 1


def test_session_audit_rejects_noncanonical_bytes_and_symlinks(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    registry = SessionRegistry(repo)
    open_session(registry)
    event_path = next(registry.event_root.glob("*.json"))
    canonical = event_path.read_bytes()
    value = json.loads(canonical)
    event_path.write_text(json.dumps(value, indent=2), encoding="utf-8")

    with pytest.raises(IntegrityError, match="canonical JSON"):
        registry.list_sessions()

    event_path.write_bytes(canonical)
    outside = tmp_path / "outside-session-event.json"
    event_path.replace(outside)
    event_path.symlink_to(outside)
    with pytest.raises(IntegrityError, match="unsafe path"):
        registry.list_sessions()


def test_session_audit_rejects_an_opened_session_without_status(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    registry = SessionRegistry(repo)
    open_session(registry)
    event_path = next(registry.event_root.glob("*.json"))
    value = json.loads(event_path.read_bytes())
    value["session"].pop("status")
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    event_path.write_bytes((canonical + "\n").encode())

    with pytest.raises(IntegrityError, match="invalid session body"):
        registry.list_sessions()
