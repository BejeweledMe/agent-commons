from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from agent_commons.domain.design_packages import (
    DesignPackageRefusal,
    DesignPackageRefusalCode,
)
from agent_commons.errors import ConfigurationError, LifecycleConflictError
from agent_commons.services import CommonsManager

SCREEN_ID = "screen." + "0" * 25 + "1"


def _png(width: int = 3, height: int = 2) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00\x00\x00\x00\x00"
    )


def _manager(tmp_path: Path) -> CommonsManager:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    CommonsManager.initialize(repo, integrations=(), workspace_name="design-package-tests")
    state = tmp_path / "state"
    bootstrap = CommonsManager(repo, state_root=state)
    session = bootstrap.start_session(
        stable_instance_id="design-package-builder-12345678",
        principal="designer",
        client="codex",
        software="agent-cli",
        role="product-designer",
        capabilities=(),
    )
    return CommonsManager(repo, state_root=state, session_id=session["session_id"])


def _peer_manager(manager: CommonsManager, *, suffix: str) -> CommonsManager:
    session = manager.start_session(
        stable_instance_id=f"design-package-peer-{suffix}-12345678",
        principal=f"peer-{suffix}",
        client="claude",
        software="agent-cli",
        role="product-designer",
        capabilities=(),
    )
    return CommonsManager(
        manager.repo_root,
        state_root=manager.paths.state_root,
        session_id=session["session_id"],
    )


def _screen_work(
    manager: CommonsManager,
) -> tuple[dict[str, object], dict[str, object], Path]:
    source = manager.repo_root / "screens" / "checkout.png"
    source.parent.mkdir()
    source.write_bytes(_png())
    artifact = manager.register_artifact(
        source,
        media_type="image/png",
        classification="internal",
        idempotency_key="screen-artifact",
    )
    task = manager.create_task(
        title="Design checkout",
        description="Produce the reviewed checkout screen.",
        acceptance_criteria=("The PNG is registered exactly.",),
        idempotency_key="design-task",
    )
    taken = manager.take_task(
        str(task["entity_ref"]["id"]),
        str(task["revision"]),
        idempotency_key="take-design-task",
    )
    started = manager.start_task(
        str(task["entity_ref"]["id"]),
        str(taken["revision"]),
        idempotency_key="start-design-task",
    )
    completed = manager.complete_task(
        str(task["entity_ref"]["id"]),
        str(started["revision"]),
        summary="The exact screen artifact is ready.",
        artifact_refs=(artifact["entity_ref"],),
        idempotency_key="complete-design-task",
    )
    return artifact, completed, source


def _task_for_artifact(
    manager: CommonsManager,
    artifact: dict[str, object],
    *,
    suffix: str,
) -> dict[str, object]:
    task = manager.create_task(
        title=f"Design exact screen {suffix}",
        description="Produce the exact registered screen revision.",
        acceptance_criteria=("The current PNG revision is bound.",),
        idempotency_key=f"design-task-{suffix}",
    )
    task_id = str(task["entity_ref"]["id"])
    taken = manager.take_task(
        task_id,
        str(task["revision"]),
        idempotency_key=f"take-design-task-{suffix}",
    )
    started = manager.start_task(
        task_id,
        str(taken["revision"]),
        idempotency_key=f"start-design-task-{suffix}",
    )
    return manager.complete_task(
        task_id,
        str(started["revision"]),
        summary="The exact screen artifact revision is ready.",
        artifact_refs=(artifact["entity_ref"],),
        idempotency_key=f"complete-design-task-{suffix}",
    )


def _draft(
    manager: CommonsManager,
    artifact: dict[str, object],
    task: dict[str, object],
    *,
    title: str = "Checkout flow",
) -> dict[str, object]:
    artifact_id = str(artifact["entity_ref"]["id"])
    projected = manager.snapshot().artifacts[artifact_id]
    return {
        "title": title,
        "screens": [
            {
                "screen_id": SCREEN_ID,
                "ordinal": 1,
                "title": "Checkout",
                "artifact_binding": {
                    "ref": dict(artifact["entity_ref"]),
                    "revision": artifact["revision"],
                },
                "artifact_content_revision": projected["content_revision"],
                "producer_task_binding": {
                    "ref": dict(task["entity_ref"]),
                    "revision": task["revision"],
                },
                "classification": projected["classification"],
                "media_type": "image/png",
                "safe_preview_eligible": True,
            }
        ],
    }


def _assert_no_idempotency_reservation(manager: CommonsManager, key: str) -> None:
    session = manager._active_session()
    namespace = manager._namespace(session)
    assert manager.events.idempotency.lookup(namespace=namespace, key=key) is None
    assert manager._event_for_idempotency_identity(namespace, key) is None


def test_publish_revise_retry_and_exact_history(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    artifact, task, _source = _screen_work(manager)
    first = manager.design_packages.publish(
        _draft(manager, artifact, task), idempotency_key="publish-package"
    )
    repeated = manager.design_packages.publish(
        _draft(manager, artifact, task), idempotency_key="publish-package"
    )
    second = manager.design_packages.revise(
        first.design_package_id,
        first.revision,
        _draft(manager, artifact, task, title="Checkout flow v2"),
        idempotency_key="revise-package",
    )

    assert repeated is not first
    assert repeated.to_dict() == first.to_dict()
    assert manager.design_packages.get(first.design_package_id).revision == second.revision
    assert (
        manager.design_packages.get(first.design_package_id, revision=first.revision).draft.title
        == "Checkout flow"
    )
    assert manager.snapshot().semantics_required == 4
    assert manager.doctor()["ok"] is True


def test_correction_cannot_rewrite_screen_provenance(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    artifact, task, _source = _screen_work(manager)
    package = manager.design_packages.publish(
        _draft(manager, artifact, task), idempotency_key="publish-package"
    )
    stored = manager.show_event(package.source_event_id)
    replacement = dict(stored["event"]["payload"])
    screens = replacement["screens"]
    assert isinstance(screens, list) and isinstance(screens[0], dict)
    replacement["screens"] = [{**screens[0], "artifact_content_revision": "sha256:" + "0" * 64}]
    before = len(list(manager.events.iter_events()))

    with pytest.raises(
        LifecycleConflictError,
        match="design_package.revised instead: screens",
    ):
        manager.correct_event(
            package.source_event_id,
            expected_target_sha256=stored["canonical_sha256"],
            replacement_payload=replacement,
            idempotency_key="forbidden-package-correction",
        )

    assert len(list(manager.events.iter_events())) == before
    assert manager.doctor()["ok"] is True


def test_stale_artifact_and_task_bindings_refuse_without_write(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    artifact, task, source = _screen_work(manager)
    draft = _draft(manager, artifact, task)
    source.write_bytes(_png(width=4, height=4))
    revised_artifact = manager.revise_artifact(
        str(artifact["entity_ref"]["id"]),
        str(artifact["revision"]),
        source,
        media_type="image/png",
        classification="internal",
        idempotency_key="revise-screen-artifact",
    )
    before = len(list(manager.events.iter_events()))

    with pytest.raises(DesignPackageRefusal) as stale_artifact:
        manager.design_packages.publish(draft, idempotency_key="stale-artifact-package")
    assert stale_artifact.value.code is DesignPackageRefusalCode.STALE
    assert len(list(manager.events.iter_events())) == before

    current_artifact = manager.snapshot().artifacts[str(artifact["entity_ref"]["id"])]
    new_draft = _draft(
        manager,
        {
            "entity_ref": artifact["entity_ref"],
            "revision": revised_artifact["revision"],
        },
        task,
    )
    screens = new_draft["screens"]
    assert isinstance(screens, list) and isinstance(screens[0], dict)
    screens[0]["artifact_content_revision"] = current_artifact["content_revision"]
    with pytest.raises(DesignPackageRefusal) as stale_task_provenance:
        manager.design_packages.publish(new_draft, idempotency_key="stale-task-provenance-package")
    assert stale_task_provenance.value.code is DesignPackageRefusalCode.UNAUTHORIZED
    assert len(list(manager.events.iter_events())) == before


def test_prior_artifact_author_cannot_publish_another_actors_exact_revision(
    tmp_path: Path,
) -> None:
    author = _manager(tmp_path)
    artifact, _old_task, source = _screen_work(author)
    reviser = _peer_manager(author, suffix="artifact-reviser")
    source.write_bytes(_png(width=7, height=5))
    revised_artifact = reviser.revise_artifact(
        str(artifact["entity_ref"]["id"]),
        str(artifact["revision"]),
        source,
        media_type="image/png",
        classification="internal",
        idempotency_key="peer-revise-screen-artifact",
    )
    exact_artifact = {
        "entity_ref": artifact["entity_ref"],
        "revision": revised_artifact["revision"],
    }
    author_task = _task_for_artifact(author, exact_artifact, suffix="after-peer-artifact")
    draft = _draft(author, exact_artifact, author_task)
    snapshot = author.snapshot()
    artifact_id = str(artifact["entity_ref"]["id"])
    assert author.session_id in snapshot.artifacts[artifact_id]["evidence_author_session_ids"]
    assert snapshot.entity_revision_actor(
        "artifact", artifact_id, str(revised_artifact["revision"])
    ) == str(reviser.session_id)
    before = len(list(author.events.iter_events()))

    narrow_key = "prior-artifact-author-package"
    with pytest.raises(DesignPackageRefusal) as narrow:
        author.design_packages.publish(draft, idempotency_key=narrow_key)
    assert narrow.value.code is DesignPackageRefusalCode.UNAUTHORIZED
    assert str(reviser.session_id) not in str(narrow.value)
    _assert_no_idempotency_reservation(author, narrow_key)

    raw_key = "prior-artifact-author-direct-package"
    with pytest.raises(DesignPackageRefusal) as direct:
        author.record_event(
            "design_package.created",
            {
                "design_package_id": "design_package." + "0" * 25 + "6",
                **draft,
            },
            idempotency_key=raw_key,
            tags=("design_package",),
        )
    assert direct.value.code is DesignPackageRefusalCode.UNAUTHORIZED
    assert str(reviser.session_id) not in str(direct.value)
    _assert_no_idempotency_reservation(author, raw_key)
    assert author.snapshot().semantics_required == 1
    assert len(list(author.events.iter_events())) == before


def test_prior_task_author_cannot_publish_another_actors_exact_revision(
    tmp_path: Path,
) -> None:
    author = _manager(tmp_path)
    artifact, task, _source = _screen_work(author)
    reviser = _peer_manager(author, suffix="task-reviser")
    task_id = str(task["entity_ref"]["id"])
    revised_task = reviser.revise_task(
        task_id,
        str(task["revision"]),
        changes={"title": "Design checkout after peer revision"},
        idempotency_key="peer-revise-design-task",
    )
    exact_task = {
        "entity_ref": task["entity_ref"],
        "revision": revised_task["revision"],
    }
    draft = _draft(author, artifact, exact_task)
    snapshot = author.snapshot()
    assert author.session_id in snapshot.tasks[task_id]["work_author_session_ids"]
    assert snapshot.entity_revision_actor("task", task_id, str(revised_task["revision"])) == str(
        reviser.session_id
    )
    before = len(list(author.events.iter_events()))

    narrow_key = "prior-task-author-package"
    with pytest.raises(DesignPackageRefusal) as narrow:
        author.design_packages.publish(draft, idempotency_key=narrow_key)
    assert narrow.value.code is DesignPackageRefusalCode.UNAUTHORIZED
    assert str(reviser.session_id) not in str(narrow.value)
    _assert_no_idempotency_reservation(author, narrow_key)

    raw_key = "prior-task-author-direct-package"
    with pytest.raises(DesignPackageRefusal) as direct:
        author.record_event(
            "design_package.created",
            {
                "design_package_id": "design_package." + "0" * 25 + "7",
                **draft,
            },
            idempotency_key=raw_key,
            tags=("design_package",),
        )
    assert direct.value.code is DesignPackageRefusalCode.UNAUTHORIZED
    assert str(reviser.session_id) not in str(direct.value)
    _assert_no_idempotency_reservation(author, raw_key)
    assert author.snapshot().semantics_required == 1
    assert len(list(author.events.iter_events())) == before


def test_foreign_session_cannot_publish_another_producers_screen(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    artifact, task, _source = _screen_work(manager)
    draft = _draft(manager, artifact, task)
    other = manager.start_session(
        stable_instance_id="foreign-package-publisher-12345678",
        principal="other-designer",
        client="claude",
        software="agent-cli",
        role="product-designer",
        capabilities=(),
    )
    foreign = CommonsManager(
        manager.repo_root,
        state_root=manager.paths.state_root,
        session_id=other["session_id"],
    )
    before = len(list(manager.events.iter_events()))

    with pytest.raises(DesignPackageRefusal) as unauthorized:
        foreign.design_packages.publish(draft, idempotency_key="foreign-package")

    assert unauthorized.value.code is DesignPackageRefusalCode.UNAUTHORIZED
    assert len(list(manager.events.iter_events())) == before

    with pytest.raises(DesignPackageRefusal) as direct_bypass:
        foreign.record_event(
            "design_package.created",
            {
                "design_package_id": "design_package." + "0" * 25 + "9",
                **draft,
            },
            idempotency_key="foreign-direct-package",
            tags=("design_package",),
        )
    assert direct_bypass.value.code is DesignPackageRefusalCode.UNAUTHORIZED
    assert foreign.snapshot().semantics_required == 1
    assert len(list(manager.events.iter_events())) == before


@pytest.mark.parametrize(
    "break_source",
    [
        lambda source, _tmp: source.write_bytes(_png(width=9, height=9)),
        lambda source, tmp: (
            source.unlink(),
            (tmp / "outside.png").write_bytes(_png()),
            source.symlink_to(tmp / "outside.png"),
        ),
    ],
    ids=["hash-replacement", "symlink"],
)
def test_changed_or_symlinked_preview_source_fails_closed_without_path_echo(
    tmp_path: Path,
    break_source: Callable[[Path, Path], object],
) -> None:
    manager = _manager(tmp_path)
    artifact, task, source = _screen_work(manager)
    draft = _draft(manager, artifact, task)
    break_source(source, tmp_path)
    before = len(list(manager.events.iter_events()))

    with pytest.raises(DesignPackageRefusal) as refused:
        manager.design_packages.publish(draft, idempotency_key="broken-preview-package")

    assert refused.value.code in {
        DesignPackageRefusalCode.STALE,
        DesignPackageRefusalCode.UNSAFE,
    }
    assert str(source) not in str(refused.value)
    assert len(list(manager.events.iter_events())) == before


def test_manifest_traversal_and_hash_mismatch_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path)
    artifact, task, _source = _screen_work(manager)
    draft = _draft(manager, artifact, task)
    artifact_id = str(artifact["entity_ref"]["id"])
    bundle = manager.get_artifact_bundle(artifact_id)
    bundle["manifest"]["source"] = {"path": "../outside.png"}
    monkeypatch.setattr(manager, "get_artifact_bundle", lambda _artifact_id: bundle)
    before = len(list(manager.events.iter_events()))

    with pytest.raises(DesignPackageRefusal) as traversal:
        manager.design_packages.publish(draft, idempotency_key="traversal-package")
    assert traversal.value.code is DesignPackageRefusalCode.UNSAFE
    assert "../outside.png" not in str(traversal.value)
    assert len(list(manager.events.iter_events())) == before

    bundle["manifest"]["source"] = {"path": "screens/checkout.png"}
    bundle["manifest"]["revision"] = "sha256:" + "0" * 64
    with pytest.raises(DesignPackageRefusal) as hash_mismatch:
        manager.design_packages.publish(draft, idempotency_key="hash-mismatch-package")
    assert hash_mismatch.value.code is DesignPackageRefusalCode.UNSAFE
    assert len(list(manager.events.iter_events())) == before


def test_secret_refusal_and_write_disable_precede_reservation(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    artifact, task, _source = _screen_work(manager)
    draft = _draft(manager, artifact, task)
    draft["title"] = "password: do-not-store-this"
    before = len(list(manager.events.iter_events()))

    with pytest.raises(DesignPackageRefusal) as secret:
        manager.design_packages.publish(draft, idempotency_key="secret-package")
    assert secret.value.code is DesignPackageRefusalCode.UNSAFE
    assert "do-not-store-this" not in str(secret.value)
    assert len(list(manager.events.iter_events())) == before

    disabled = CommonsManager(
        manager.repo_root,
        state_root=manager.paths.state_root,
        session_id=manager.session_id,
        design_package_writes_enabled=False,
    )
    safe_draft = _draft(manager, artifact, task)
    with pytest.raises(DesignPackageRefusal) as narrow_disabled:
        disabled.design_packages.publish(safe_draft, idempotency_key="disabled-package")
    assert narrow_disabled.value.code is DesignPackageRefusalCode.UNAVAILABLE
    with pytest.raises(DesignPackageRefusal) as raw_disabled:
        disabled.record_event(
            "design_package.created",
            {
                "design_package_id": "design_package." + "0" * 25 + "8",
                **safe_draft,
            },
            idempotency_key="disabled-raw-package",
            tags=("design_package",),
        )
    assert raw_disabled.value.code is DesignPackageRefusalCode.UNAVAILABLE
    assert len(list(manager.events.iter_events())) == before

    with pytest.raises(ConfigurationError, match="design_package_writes_enabled"):
        CommonsManager(
            manager.repo_root,
            state_root=manager.paths.state_root,
            session_id=manager.session_id,
            design_package_writes_enabled="false",  # type: ignore[arg-type]
        )
