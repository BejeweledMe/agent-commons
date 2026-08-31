from __future__ import annotations

from pathlib import Path
from threading import Event, Thread

import pytest

from agent_commons.domain.design_packages import (
    DesignPackageRefusal,
    DesignPackageRefusalCode,
)
from agent_commons.errors import ValidationError
from agent_commons.services import CommonsManager
from agent_commons.services.design_feedback import open_design_feedback
from tests.services.test_design_packages import SCREEN_ID, _draft, _manager, _screen_work


def _published(tmp_path: Path):  # type: ignore[no-untyped-def]
    manager = _manager(tmp_path)
    artifact, task, _source = _screen_work(manager)
    package = manager.design_packages.publish(
        _draft(manager, artifact, task), idempotency_key="feedback-package"
    )
    screen = package.draft.screens[0]
    return manager, package, screen


def test_feedback_opens_exact_revision_bound_canonical_thread(tmp_path: Path) -> None:
    manager, package, screen = _published(tmp_path)

    replied = open_design_feedback(
        manager,
        design_package_id=package.design_package_id,
        design_package_revision=package.revision,
        screen_id=SCREEN_ID,
        artifact_revision=screen.artifact_binding.revision,
        producer_task_revision=screen.producer_task_binding.revision,
        body="Please increase the primary-action contrast.",
        idempotency_key="gallery-feedback",
    )

    thread_id = str(replied["entity_ref"]["id"])
    thread = manager.snapshot().threads[thread_id]
    assert thread["thread_type"] == "review_discussion"
    assert thread["messages"][0]["body"] == "Please increase the primary-action contrast."
    assert thread["extensions"]["design_feedback"] == {
        "design_package_id": package.design_package_id,
        "design_package_revision": package.revision,
        "screen_id": SCREEN_ID,
        "artifact_id": screen.artifact_binding.identifier,
        "artifact_revision": screen.artifact_binding.revision,
        "artifact_content_revision": screen.artifact_content_revision,
        "producer_task_id": screen.producer_task_binding.identifier,
        "producer_task_revision": screen.producer_task_binding.revision,
    }
    assert {item["kind"] for item in thread["related_refs"]} == {
        "artifact",
        "design_package",
        "task",
    }


def test_feedback_refuses_stale_package_or_screen_before_opening_thread(tmp_path: Path) -> None:
    manager, package, screen = _published(tmp_path)
    manager.design_packages.revise(
        package.design_package_id,
        package.revision,
        package.draft.to_payload(),
        idempotency_key="feedback-package-revision",
    )
    before = len(manager.snapshot().threads)

    with pytest.raises(DesignPackageRefusal) as caught:
        open_design_feedback(
            manager,
            design_package_id=package.design_package_id,
            design_package_revision=package.revision,
            screen_id=SCREEN_ID,
            artifact_revision=screen.artifact_binding.revision,
            producer_task_revision=screen.producer_task_binding.revision,
            body="This must not land on a newer revision.",
            idempotency_key="stale-gallery-feedback",
        )

    assert caught.value.code is DesignPackageRefusalCode.STALE
    assert len(manager.snapshot().threads) == before


def test_feedback_body_is_bounded_before_any_canonical_write(tmp_path: Path) -> None:
    manager, package, screen = _published(tmp_path)
    before = len(manager.snapshot().threads)

    with pytest.raises(ValidationError, match="8192-byte"):
        open_design_feedback(
            manager,
            design_package_id=package.design_package_id,
            design_package_revision=package.revision,
            screen_id=SCREEN_ID,
            artifact_revision=screen.artifact_binding.revision,
            producer_task_revision=screen.producer_task_binding.revision,
            body="x" * 8193,
            idempotency_key="oversized-gallery-feedback",
        )

    assert len(manager.snapshot().threads) == before


def test_feedback_refuses_a_task_revised_after_package_publish(tmp_path: Path) -> None:
    manager, package, screen = _published(tmp_path)
    manager.submit_task(
        screen.producer_task_binding.identifier,
        screen.producer_task_binding.revision,
        summary="A later task revision must stale the published screen.",
        artifact_refs=({"kind": "artifact", "id": screen.artifact_binding.identifier},),
        idempotency_key="revise-feedback-producer-task",
    )
    before = len(manager.snapshot().threads)

    with pytest.raises(DesignPackageRefusal) as caught:
        open_design_feedback(
            manager,
            design_package_id=package.design_package_id,
            design_package_revision=package.revision,
            screen_id=SCREEN_ID,
            artifact_revision=screen.artifact_binding.revision,
            producer_task_revision=screen.producer_task_binding.revision,
            body="This must not bind to the superseded task revision.",
            idempotency_key="stale-task-gallery-feedback",
        )

    assert caught.value.code is DesignPackageRefusalCode.STALE
    assert len(manager.snapshot().threads) == before


def test_feedback_refuses_an_artifact_revised_after_package_publish(tmp_path: Path) -> None:
    manager, package, screen = _published(tmp_path)
    source = manager.repo_root / "screens" / "checkout.png"
    source.write_bytes(source.read_bytes() + b"new-revision")
    manager.revise_artifact(
        screen.artifact_binding.identifier,
        screen.artifact_binding.revision,
        source,
        media_type="image/png",
        classification="internal",
        idempotency_key="revise-feedback-artifact",
    )
    before = len(manager.snapshot().threads)

    with pytest.raises(DesignPackageRefusal) as caught:
        open_design_feedback(
            manager,
            design_package_id=package.design_package_id,
            design_package_revision=package.revision,
            screen_id=SCREEN_ID,
            artifact_revision=screen.artifact_binding.revision,
            producer_task_revision=screen.producer_task_binding.revision,
            body="This must not bind to the superseded artifact revision.",
            idempotency_key="stale-artifact-gallery-feedback",
        )

    assert caught.value.code is DesignPackageRefusalCode.STALE
    assert len(manager.snapshot().threads) == before


def test_feedback_refuses_missing_exact_producer_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, package, screen = _published(tmp_path)
    original_snapshot = manager.snapshot

    def snapshot_without_producer():  # type: ignore[no-untyped-def]
        snapshot = original_snapshot()
        snapshot.entity_revision_actor_session_ids.pop(
            (
                "task",
                screen.producer_task_binding.identifier,
                screen.producer_task_binding.revision,
            ),
            None,
        )
        return snapshot

    monkeypatch.setattr(manager, "snapshot", snapshot_without_producer)

    with pytest.raises(DesignPackageRefusal) as caught:
        open_design_feedback(
            manager,
            design_package_id=package.design_package_id,
            design_package_revision=package.revision,
            screen_id=SCREEN_ID,
            artifact_revision=screen.artifact_binding.revision,
            producer_task_revision=screen.producer_task_binding.revision,
            body="This must not bind without immutable producer provenance.",
            idempotency_key="missing-producer-gallery-feedback",
        )

    assert caught.value.code is DesignPackageRefusalCode.STALE
    assert not original_snapshot().threads


@pytest.mark.parametrize("artifact_actor", [None, "session.different-producer-12345678"])
def test_feedback_refuses_missing_or_mismatched_exact_artifact_producer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    artifact_actor: str | None,
) -> None:
    manager, package, screen = _published(tmp_path)
    original_snapshot = manager.snapshot

    def snapshot_with_bad_artifact_producer():  # type: ignore[no-untyped-def]
        snapshot = original_snapshot()
        key = (
            "artifact",
            screen.artifact_binding.identifier,
            screen.artifact_binding.revision,
        )
        if artifact_actor is None:
            snapshot.entity_revision_actor_session_ids.pop(key, None)
        else:
            snapshot.entity_revision_actor_session_ids[key] = artifact_actor
        return snapshot

    monkeypatch.setattr(manager, "snapshot", snapshot_with_bad_artifact_producer)

    with pytest.raises(DesignPackageRefusal) as caught:
        open_design_feedback(
            manager,
            design_package_id=package.design_package_id,
            design_package_revision=package.revision,
            screen_id=SCREEN_ID,
            artifact_revision=screen.artifact_binding.revision,
            producer_task_revision=screen.producer_task_binding.revision,
            body="This must not bind incoherent exact revision actors.",
            idempotency_key=f"bad-artifact-producer-{artifact_actor}",
        )

    assert caught.value.code is DesignPackageRefusalCode.STALE
    assert not original_snapshot().threads


def test_feedback_retry_recovers_crash_after_thread_open_without_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, package, screen = _published(tmp_path)
    original_reply = manager.reply_thread

    def crash_before_reply(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated crash after thread.opened")

    monkeypatch.setattr(manager, "reply_thread", crash_before_reply)
    call = {
        "design_package_id": package.design_package_id,
        "design_package_revision": package.revision,
        "screen_id": SCREEN_ID,
        "artifact_revision": screen.artifact_binding.revision,
        "producer_task_revision": screen.producer_task_binding.revision,
        "body": "Recover this exact feedback once.",
        "idempotency_key": "crash-recovery-gallery-feedback",
    }
    with pytest.raises(RuntimeError, match="simulated crash"):
        open_design_feedback(manager, **call)
    opened = manager.snapshot().threads
    assert len(opened) == 1
    thread_id = next(iter(opened))
    assert opened[thread_id].to_dict().get("messages", []) == []

    monkeypatch.setattr(manager, "reply_thread", original_reply)
    retried = open_design_feedback(manager, **call)

    assert retried["entity_ref"]["id"] == thread_id
    threads = manager.snapshot().threads
    assert list(threads) == [thread_id]
    assert [item["body"] for item in threads[thread_id]["messages"]] == [
        "Recover this exact feedback once."
    ]


def test_feedback_holds_canonical_lock_from_revalidation_through_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, package, screen = _published(tmp_path)
    competitor = CommonsManager(
        manager.repo_root,
        state_root=manager.paths.state_root,
        session_id=manager.session_id,
    )
    attempted = Event()
    completed = Event()

    def revise_task() -> None:
        attempted.set()
        competitor.submit_task(
            screen.producer_task_binding.identifier,
            screen.producer_task_binding.revision,
            summary="Concurrent later task revision.",
            artifact_refs=({"kind": "artifact", "id": screen.artifact_binding.identifier},),
            idempotency_key="concurrent-feedback-task-revision",
        )
        completed.set()

    original_snapshot = manager.snapshot
    original_open = manager.open_thread
    worker: Thread | None = None
    started = False

    def snapshot_and_compete():  # type: ignore[no-untyped-def]
        nonlocal worker, started
        snapshot = original_snapshot()
        if not started:
            started = True
            worker = Thread(target=revise_task)
            worker.start()
            assert attempted.wait(timeout=2)
        return snapshot

    def open_while_competitor_blocked(*args, **kwargs):  # type: ignore[no-untyped-def]
        assert not completed.is_set()
        return original_open(*args, **kwargs)

    monkeypatch.setattr(manager, "snapshot", snapshot_and_compete)
    monkeypatch.setattr(manager, "open_thread", open_while_competitor_blocked)
    replied = open_design_feedback(
        manager,
        design_package_id=package.design_package_id,
        design_package_revision=package.revision,
        screen_id=SCREEN_ID,
        artifact_revision=screen.artifact_binding.revision,
        producer_task_revision=screen.producer_task_binding.revision,
        body="This exact revision wins the canonical race.",
        idempotency_key="locked-gallery-feedback",
    )
    assert replied["event_type"] == "thread.replied"
    assert not completed.is_set()
    assert worker is not None
    worker.join(timeout=5)
    assert completed.is_set()
