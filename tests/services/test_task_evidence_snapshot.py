from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from agent_commons.errors import IntegrityError, LifecycleConflictError
from agent_commons.services import CommonsManager


@pytest.fixture
def manager(tmp_path: Path) -> CommonsManager:
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="evidence-snapshot")
    manager = CommonsManager(repo, state_root=tmp_path / "state")
    session = manager.start_session(
        stable_instance_id="evidence-snapshot-test-12345678",
        principal="operator",
        client="pytest",
        software="pytest",
        role="builder",
    )
    manager.session_id = session["session_id"]
    return manager


def active_task(manager: CommonsManager) -> dict:
    created = manager.create_task(
        title="Result",
        description="Prepare a result",
        acceptance_criteria=["done"],
        idempotency_key="task",
    )
    return manager.start_task(
        created["entity_ref"]["id"],
        created["revision"],
        idempotency_key="start",
    )


@pytest.mark.parametrize("action", ["complete_task", "submit_task"])
def test_explicit_empty_evidence_has_one_full_ledger_read_and_retries_exactly(
    manager: CommonsManager, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    previous = active_task(manager)
    if action == "submit_task":
        previous = manager.complete_task(
            previous["entity_ref"]["id"],
            previous["revision"],
            summary="done",
            artifact_refs=(),
            idempotency_key="complete",
        )
    before = list(manager.events.iter_events())
    reads: list[Path] = []
    original = manager.events.read_path

    def counted(path: Path):
        reads.append(path)
        return original(path)

    monkeypatch.setattr(manager.events, "read_path", counted)
    kwargs = dict(summary="ready", artifact_refs=(), idempotency_key="measured-" + action)
    result = getattr(manager, action)(previous["entity_ref"]["id"], previous["revision"], **kwargs)
    assert all(reads.count(record.path) == 1 for record in before)
    assert len(reads) == len(before) + 1
    retried = getattr(manager, action)(previous["entity_ref"]["id"], previous["revision"], **kwargs)
    assert retried["event_id"] == result["event_id"]
    assert retried["created"] is False
    assert manager.doctor()["ok"]


@pytest.mark.parametrize("action", ["complete_task", "submit_task"])
def test_empty_evidence_still_rejects_a_corrupt_prior_event(
    manager: CommonsManager, action: str
) -> None:
    previous = active_task(manager)
    if action == "submit_task":
        previous = manager.complete_task(
            previous["entity_ref"]["id"],
            previous["revision"],
            summary="done",
            idempotency_key="complete",
        )
    records = list(manager.events.iter_events())
    records[0].path.write_bytes(b"{}\n")
    with pytest.raises(IntegrityError):
        getattr(manager, action)(
            previous["entity_ref"]["id"],
            previous["revision"],
            summary="ready",
            artifact_refs=(),
            idempotency_key="corrupt-" + action,
        )
    assert len(list(manager.paths.events.glob("*/*/*/evt.*.json"))) == len(records)


@pytest.mark.parametrize("retain", [True, False])
def test_none_retains_exact_evidence_and_explicit_empty_replaces_it(
    manager: CommonsManager, retain: bool
) -> None:
    source = manager.repo_root / "result.txt"
    source.write_text("result", encoding="utf-8")
    artifact = manager.register_artifact(
        source, media_type="text/plain", idempotency_key="artifact"
    )
    active = active_task(manager)
    completed = manager.complete_task(
        active["entity_ref"]["id"],
        active["revision"],
        summary="done",
        artifact_refs=(artifact["entity_ref"],),
        idempotency_key="complete",
    )
    bound = {"ref": artifact["entity_ref"], "revision": artifact["revision"]}
    assert manager.show_event(completed["event_id"])["event"]["payload"]["artifact_bindings"] == [
        bound
    ]
    submitted = manager.submit_task(
        active["entity_ref"]["id"],
        completed["revision"],
        summary="review",
        artifact_refs=None if retain else (),
        idempotency_key="submit",
    )
    payload = manager.show_event(submitted["event_id"])["event"]["payload"]
    assert payload["artifact_bindings"] == ([bound] if retain else [])
    assert payload["artifact_refs"] == ([artifact["entity_ref"]] if retain else [])


def test_none_does_not_silently_discard_stale_evidence(manager: CommonsManager) -> None:
    source = manager.repo_root / "result.txt"
    source.write_text("result", encoding="utf-8")
    artifact = manager.register_artifact(
        source, media_type="text/plain", idempotency_key="artifact"
    )
    active = active_task(manager)
    completed = manager.complete_task(
        active["entity_ref"]["id"],
        active["revision"],
        summary="done",
        artifact_refs=(artifact["entity_ref"],),
        idempotency_key="complete",
    )
    source.write_text("changed", encoding="utf-8")
    manager.revise_artifact(
        artifact["entity_ref"]["id"],
        artifact["revision"],
        source,
        media_type="text/plain",
        idempotency_key="revise-artifact",
    )
    with pytest.raises(LifecycleConflictError, match="stale or unbound"):
        manager.submit_task(
            active["entity_ref"]["id"],
            completed["revision"],
            summary="review",
            artifact_refs=None,
            idempotency_key="submit",
        )


def test_concurrent_empty_evidence_completion_has_one_winner(manager: CommonsManager) -> None:
    active = active_task(manager)
    barrier = Barrier(2)

    def complete(key: str) -> str:
        barrier.wait(timeout=5)
        try:
            manager.complete_task(
                active["entity_ref"]["id"],
                active["revision"],
                summary="done",
                artifact_refs=(),
                idempotency_key=key,
            )
            return "saved"
        except LifecycleConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(complete, key) for key in ("first", "second")]
        assert sorted(f.result(timeout=10) for f in futures) == ["conflict", "saved"]
    assert manager.doctor()["ok"]
