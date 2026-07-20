from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import get_context
from pathlib import Path

import pytest

from agent_commons.coordination import (
    ClaimService,
    SessionRegistry,
    normalize_resource,
    normalize_resources,
    resources_overlap,
)
from agent_commons.errors import (
    ClaimConflictError,
    IdempotencyConflictError,
    IntegrityError,
    LifecycleConflictError,
    SecurityPolicyError,
    ValidationError,
)


class Clock:
    def __init__(self, value: float = 1_720_000_000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def init_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    return path


def open_agent(
    registry: SessionRegistry,
    name: str,
    *,
    capabilities: tuple[str, ...] = ("task:write",),
):
    return registry.open_session(
        stable_instance_id=f"agent-window-{name}-12345678",
        principal=f"operator-{name}",
        client="codex" if name != "reviewer" else "claude-code",
        software="agent-cli",
        role=name,
        capabilities=capabilities,
    )


def harness(tmp_path: Path, *, clock: Clock | None = None):
    repo = init_repo(tmp_path / "repo")
    actual_clock = clock or Clock()
    sessions = SessionRegistry(repo, clock=actual_clock)
    service = ClaimService(repo, sessions=sessions, clock=actual_clock)
    return repo, sessions, service


def process_claim_worker(
    repo: str,
    state_root: str,
    session_id: str,
    start_event,
    results,
) -> None:
    sessions = SessionRegistry(repo, state_root=state_root)
    claims = ClaimService(repo, sessions=sessions, state_root=state_root)
    start_event.wait(timeout=10)
    try:
        claim = claims.acquire(["path:src/process-race.py"], owner_session_id=session_id)
        results.put(("acquired", claim.claim_id))
    except ClaimConflictError:
        results.put(("conflict", session_id))


def test_resource_normalization_and_hierarchical_overlap() -> None:
    assert normalize_resource("PATH:src\\api/./routes") == "path:src/api/routes"
    assert resources_overlap("path:src", "path:src/api/routes.py")
    assert resources_overlap("path:src/api", "path:src/api")
    assert not resources_overlap("path:src/api", "path:src/application")
    assert not resources_overlap("task:abc", "path:abc")
    assert resources_overlap("task:abc", "task:abc")
    assert normalize_resources(
        ["path:src/pkg", "resource:z", "path:src", "resource:a", "resource:a"]
    ) == ("path:src", "resource:a", "resource:z")

    with pytest.raises(ValidationError):
        normalize_resource("path:../secret")
    with pytest.raises(ValidationError):
        normalize_resource("path:/absolute")
    with pytest.raises(ValidationError):
        normalize_resource("path:.")


def test_path_ancestor_descendant_claims_conflict_but_siblings_do_not(
    tmp_path: Path,
) -> None:
    _, sessions, claims = harness(tmp_path)
    first = open_agent(sessions, "builder-a")
    second = open_agent(sessions, "builder-b")
    parent = claims.acquire(["path:src/api"], owner_session_id=first.session_id)

    with pytest.raises(ClaimConflictError, match="overlaps"):
        claims.acquire(["path:src/api/routes.py"], owner_session_id=second.session_id)
    with pytest.raises(ClaimConflictError, match="overlaps"):
        claims.acquire(["path:src"], owner_session_id=second.session_id)

    sibling = claims.acquire(["path:src/application"], owner_session_id=second.session_id)
    assert sibling.status == "active"
    claims.release(parent.claim_id, owner_session_id=first.session_id, nonce=parent.nonce)
    child = claims.acquire(["path:src/api/routes.py"], owner_session_id=second.session_id)
    assert child.resources == ("path:src/api/routes.py",)


def test_multi_resource_acquisition_is_all_or_none_and_deterministic(
    tmp_path: Path,
) -> None:
    _, sessions, claims = harness(tmp_path)
    first = open_agent(sessions, "builder-a")
    second = open_agent(sessions, "builder-b")
    claims.acquire(["task:occupied"], owner_session_id=first.session_id)

    before = len(claims.audit_events())
    with pytest.raises(ClaimConflictError):
        claims.acquire(["task:free", "task:occupied"], owner_session_id=second.session_id)
    assert len(claims.audit_events()) == before

    free = claims.acquire(["task:free"], owner_session_id=second.session_id)
    assert free.resources == ("task:free",)

    bundle = claims.acquire(
        ["resource:z", "path:docs/api", "path:docs", "resource:a"],
        owner_session_id=second.session_id,
    )
    assert bundle.resources == ("path:docs", "resource:a", "resource:z")


def test_acquire_requires_active_session_and_idempotency_is_semantic(
    tmp_path: Path,
) -> None:
    _, sessions, claims = harness(tmp_path)
    owner = open_agent(sessions, "builder")
    with pytest.raises(LifecycleConflictError, match="explicit active session"):
        claims.acquire(["task:x"], owner_session_id=None)

    first = claims.acquire(
        ["task:x"], owner_session_id=owner.session_id, idempotency_key="take-task-x"
    )
    repeated = claims.acquire(
        ["task:x"], owner_session_id=owner.session_id, idempotency_key="take-task-x"
    )
    assert repeated.claim_id == first.claim_id
    assert len([event for event in claims.audit_events() if event["action"] == "acquired"]) == 1

    with pytest.raises(IdempotencyConflictError):
        claims.acquire(["task:y"], owner_session_id=owner.session_id, idempotency_key="take-task-x")


def test_renew_release_and_break_are_single_audited_transitions(tmp_path: Path) -> None:
    clock = Clock()
    _, sessions, claims = harness(tmp_path, clock=clock)
    owner = open_agent(sessions, "builder")
    reviewer = open_agent(sessions, "reviewer", capabilities=("claim:break",))
    acquired = claims.acquire(
        ["path:src/module.py"], owner_session_id=owner.session_id, ttl_seconds=20
    )
    clock.value += 5
    renewed = claims.renew(
        acquired.claim_id,
        owner_session_id=owner.session_id,
        nonce=acquired.nonce,
        ttl_seconds=40,
    )
    assert renewed.nonce != acquired.nonce
    released = claims.release(
        renewed.claim_id, owner_session_id=owner.session_id, nonce=renewed.nonce
    )
    assert released.status == "released"
    assert [event["action"] for event in claims.audit_events()] == [
        "acquired",
        "renewed",
        "released",
    ]

    other = claims.acquire(["task:stalled"], owner_session_id=owner.session_id)
    broken = claims.break_claim(
        other.claim_id,
        actor_session_id=reviewer.session_id,
        reason="owner explicitly handed off the abandoned task",
    )
    assert broken.status == "broken"
    assert broken.ended_by_session_id == reviewer.session_id

    without_authority = open_agent(sessions, "observer")
    third = claims.acquire(["task:another"], owner_session_id=owner.session_id)
    with pytest.raises(LifecycleConflictError, match="required capability"):
        claims.break_claim(
            third.claim_id,
            actor_session_id=without_authority.session_id,
            reason="not authorized",
        )


def test_expired_claim_no_longer_conflicts_and_cannot_be_renewed(tmp_path: Path) -> None:
    clock = Clock()
    _, sessions, claims = harness(tmp_path, clock=clock)
    first = open_agent(sessions, "builder-a")
    second = open_agent(sessions, "builder-b")
    expired = claims.acquire(["path:src"], owner_session_id=first.session_id, ttl_seconds=1)
    clock.value += 2
    replacement = claims.acquire(["path:src/module.py"], owner_session_id=second.session_id)
    assert replacement.status == "active"
    with pytest.raises(ClaimConflictError, match="inactive or expired"):
        claims.renew(
            expired.claim_id,
            owner_session_id=first.session_id,
            nonce=expired.nonce,
        )


def test_sensitive_claim_metadata_is_rejected_before_audit_write(tmp_path: Path) -> None:
    _, sessions, claims = harness(tmp_path)
    owner = open_agent(sessions, "builder")
    secret = "sk-proj-" + "Q" * 24
    before = list(claims.event_root.glob("*.json"))

    with pytest.raises(SecurityPolicyError) as caught:
        claims.acquire(["task:secure"], owner_session_id=owner.session_id, description=secret)

    assert secret not in str(caught.value)
    assert list(claims.event_root.glob("*.json")) == before


def test_concurrent_conflicting_acquisition_has_exactly_one_winner(tmp_path: Path) -> None:
    repo, sessions, _ = harness(tmp_path)
    state_root = sessions.state_root
    owners = [open_agent(sessions, f"builder-{index}") for index in range(12)]

    def acquire(index: int):
        local_sessions = SessionRegistry(repo, state_root=state_root, clock=sessions.clock)
        local_claims = ClaimService(
            repo,
            sessions=local_sessions,
            state_root=state_root,
            clock=sessions.clock,
        )
        try:
            return local_claims.acquire(
                ["path:src/shared.py"], owner_session_id=owners[index].session_id
            )
        except ClaimConflictError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(acquire, range(12)))

    assert sum(not isinstance(item, ClaimConflictError) for item in results) == 1
    final = ClaimService(repo, sessions=sessions, clock=sessions.clock)
    assert len(final.list_claims(active_only=True)) == 1
    assert len(final.audit_events()) == 1


def test_separate_processes_share_the_same_claim_lock(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    sessions = SessionRegistry(repo)
    owners = [open_agent(sessions, f"process-{index}") for index in range(6)]
    context = get_context("fork")
    start_event = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=process_claim_worker,
            args=(
                str(repo),
                str(sessions.state_root),
                owner.session_id,
                start_event,
                results,
            ),
        )
        for owner in owners
    ]
    for process in processes:
        process.start()
    start_event.set()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0

    outcomes = [results.get(timeout=2) for _ in processes]
    assert [kind for kind, _ in outcomes].count("acquired") == 1
    assert [kind for kind, _ in outcomes].count("conflict") == len(processes) - 1
    claims = ClaimService(repo, sessions=sessions)
    assert len(claims.list_claims(active_only=True)) == 1
    assert len(claims.audit_events()) == 1


def test_claim_audit_rejects_semantically_valid_noncanonical_bytes(
    tmp_path: Path,
) -> None:
    _, sessions, claims = harness(tmp_path)
    owner = open_agent(sessions, "builder")
    claims.acquire(["task:canonical-audit"], owner_session_id=owner.session_id)
    event_path = next(claims.event_root.glob("*.json"))
    value = json.loads(event_path.read_bytes())
    event_path.write_text(json.dumps(value, indent=2), encoding="utf-8")

    with pytest.raises(IntegrityError, match="canonical JSON"):
        claims.audit_events()
