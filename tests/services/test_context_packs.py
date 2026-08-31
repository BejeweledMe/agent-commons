from __future__ import annotations

from pathlib import Path

import pytest

from agent_commons.domain.context_pack import ContextPackRefusal, ContextPackRefusalCode
from agent_commons.domain.projection import LEDGER_SEMANTICS_VERSION, SEMANTICS_SENSITIVE_EVENTS
from agent_commons.errors import ConfigurationError, LifecycleConflictError
from agent_commons.services import CommonsManager


def _manager(tmp_path: Path) -> CommonsManager:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="context-pack-tests")
    manager = CommonsManager(repo, state_root=state)
    session = manager.start_session(
        stable_instance_id="context-pack-builder-12345678",
        principal="operator",
        client="codex",
        software="agent-cli",
        role="researcher",
        capabilities=(),
    )
    manager.session_id = str(session["session_id"])
    return manager


def _artifact(manager: CommonsManager, *, classification: str = "internal") -> dict[str, object]:
    source = manager.repo_root / f"source-{classification}.txt"
    source.write_text("verified source", encoding="utf-8")
    return manager.register_artifact(
        source,
        media_type="text/plain",
        classification=classification,
        idempotency_key=f"source-{classification}",
    )


def _draft(artifact: dict[str, object], summary: str = "First baseline") -> dict[str, object]:
    return {
        "summary": summary,
        "facts": [
            {
                "statement": "The artifact is the exact verified input.",
                "source_refs": [
                    {
                        "ref": artifact["entity_ref"],
                        "revision": artifact["revision"],
                    }
                ],
            }
        ],
        "decision_refs": [],
        "open_questions": ["What should be implemented next?"],
    }


def test_context_pack_publish_revise_idempotency_and_exact_history(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    artifact = _artifact(manager)
    first = manager.context_packs.publish(_draft(artifact), idempotency_key="publish-pack")
    repeated = manager.context_packs.publish(_draft(artifact), idempotency_key="publish-pack")
    second = manager.context_packs.revise(
        first.context_pack_id,
        first.revision,
        _draft(artifact, "Second baseline"),
        idempotency_key="revise-pack",
    )
    retried_first = manager.context_packs.publish(_draft(artifact), idempotency_key="publish-pack")

    assert repeated is not first
    assert repeated.to_dict() == first.to_dict()
    assert manager.context_packs.get(first.context_pack_id).revision == second.revision
    assert (
        manager.context_packs.get(first.context_pack_id, revision=first.revision).draft.summary
        == "First baseline"
    )
    assert retried_first.revision == first.revision
    assert len(manager.context_packs.list()) == 1
    assert manager.doctor()["ok"] is True


def test_context_pack_stale_cas_and_missing_revision_are_typed_refusals(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    artifact = _artifact(manager)
    first = manager.context_packs.publish(_draft(artifact), idempotency_key="publish-pack")
    second = manager.context_packs.revise(
        first.context_pack_id,
        first.revision,
        _draft(artifact, "Second baseline"),
        idempotency_key="revise-pack",
    )

    with pytest.raises(ContextPackRefusal) as stale_cas:
        manager.context_packs.revise(
            first.context_pack_id,
            first.revision,
            _draft(artifact, "Stale write"),
            idempotency_key="stale-revise",
        )
    assert stale_cas.value.code is ContextPackRefusalCode.STALE

    with pytest.raises(ContextPackRefusal) as missing_revision:
        manager.context_packs.get(
            first.context_pack_id,
            revision="evt." + "0" * 25 + "9",
        )
    assert missing_revision.value.code is ContextPackRefusalCode.STALE
    assert manager.context_packs.get(first.context_pack_id).revision == second.revision


def test_direct_duplicate_context_pack_creation_is_refused_before_append(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    artifact = _artifact(manager)
    first = manager.context_packs.publish(_draft(artifact), idempotency_key="publish-pack")
    before = len(list(manager.events.iter_events()))

    with pytest.raises(LifecycleConflictError, match="context_pack already exists"):
        manager.record_event(
            "context_pack.created",
            {"context_pack_id": first.context_pack_id, **_draft(artifact)},
            idempotency_key="duplicate-direct-create",
            tags=("context_pack",),
        )

    assert len(list(manager.events.iter_events())) == before
    assert manager.doctor()["ok"] is True


def test_context_pack_refuses_restricted_and_changed_sources_without_writing(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    restricted = _artifact(manager, classification="restricted")
    before = len(list(manager.events.iter_events()))
    with pytest.raises(ContextPackRefusal) as unsafe:
        manager.context_packs.publish(_draft(restricted), idempotency_key="restricted-pack")
    assert unsafe.value.code is ContextPackRefusalCode.UNSAFE
    assert len(list(manager.events.iter_events())) == before

    artifact = _artifact(manager)
    pack = manager.context_packs.publish(_draft(artifact), idempotency_key="publish-pack")
    source = manager.repo_root / "source-internal.txt"
    source.write_text("changed verified source", encoding="utf-8")
    manager.revise_artifact(
        str(artifact["entity_ref"]["id"]),
        str(artifact["revision"]),
        source,
        media_type="text/plain",
        classification="internal",
        idempotency_key="revise-source",
    )

    with pytest.raises(ContextPackRefusal) as stale_source:
        manager.context_packs.compile(pack.context_pack_id, pack.revision)
    assert stale_source.value.code is ContextPackRefusalCode.STALE


def test_context_pack_secret_and_oversized_content_return_bounded_typed_refusals(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    artifact = _artifact(manager)
    secret_draft = _draft(artifact)
    secret_draft["summary"] = "password: do-not-store-this"
    before = len(list(manager.events.iter_events()))

    with pytest.raises(ContextPackRefusal) as unsafe:
        manager.context_packs.publish(secret_draft, idempotency_key="unsafe-secret-pack")
    assert unsafe.value.code is ContextPackRefusalCode.UNSAFE
    assert "do-not-store-this" not in str(unsafe.value)
    assert len(list(manager.events.iter_events())) == before

    oversized_draft = _draft(artifact)
    oversized_draft["summary"] = "x" * 4097
    with pytest.raises(ContextPackRefusal) as oversized:
        manager.context_packs.publish(oversized_draft, idempotency_key="oversized-pack")
    assert oversized.value.code is ContextPackRefusalCode.OVERSIZED
    assert len(list(manager.events.iter_events())) == before


def test_context_pack_compiles_old_revision_unchanged_after_new_revision(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    artifact = _artifact(manager)
    first = manager.context_packs.publish(_draft(artifact), idempotency_key="publish-pack")
    compiled_before = manager.context_packs.compile(first.context_pack_id, first.revision)
    manager.context_packs.revise(
        first.context_pack_id,
        first.revision,
        _draft(artifact, "Second baseline"),
        idempotency_key="revise-pack",
    )
    compiled_after = manager.context_packs.compile(first.context_pack_id, first.revision)

    assert compiled_after.text == compiled_before.text
    assert compiled_after.binding == compiled_before.binding


def test_context_pack_decision_refs_are_exact_and_compiler_carries_only_the_ref(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    artifact = _artifact(manager)
    decision = manager.propose_decision(
        scope="context.rollout",
        proposal="Begin with one bounded cohort.",
        alternatives=("Launch to every role at once.",),
        idempotency_key="context-decision",
    )
    draft = _draft(artifact)
    draft["decision_refs"] = [{"ref": decision["entity_ref"], "revision": decision["revision"]}]

    pack = manager.context_packs.publish(draft, idempotency_key="publish-with-decision")
    compiled = manager.context_packs.compile(pack.context_pack_id, pack.revision)

    assert len(compiled.decision_refs) == 1
    assert compiled.decision_refs[0].identifier == decision["entity_ref"]["id"]
    assert "Begin with one bounded cohort" not in compiled.text


def test_context_pack_correction_cannot_change_provenance_fields(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    artifact = _artifact(manager)
    pack = manager.context_packs.publish(_draft(artifact), idempotency_key="publish-pack")
    stored = manager.show_event(pack.source_event_id)
    replacement = dict(stored["event"]["payload"])
    replacement["facts"] = [
        {
            "statement": "A correction must not rewrite an extracted fact.",
            "source_refs": _draft(artifact)["facts"][0]["source_refs"],
        }
    ]
    before = len(list(manager.events.iter_events()))

    with pytest.raises(LifecycleConflictError, match="context_pack.revised instead: facts"):
        manager.correct_event(
            pack.source_event_id,
            expected_target_sha256=stored["canonical_sha256"],
            replacement_payload=replacement,
            idempotency_key="forbidden-pack-correction",
        )

    assert len(list(manager.events.iter_events())) == before
    assert manager.doctor()["ok"] is True


def test_context_pack_correction_cannot_change_decision_refs(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    artifact = _artifact(manager)
    pack = manager.context_packs.publish(_draft(artifact), idempotency_key="publish-pack")
    decision = manager.propose_decision(
        scope="context.correction",
        proposal="Use a canonical revision for new provenance.",
        alternatives=("Rewrite the old event.",),
        idempotency_key="pack-correction-decision",
    )
    stored = manager.show_event(pack.source_event_id)
    replacement = dict(stored["event"]["payload"])
    replacement["decision_refs"] = [
        {"ref": decision["entity_ref"], "revision": decision["revision"]}
    ]

    with pytest.raises(
        LifecycleConflictError,
        match="context_pack.revised instead: decision_refs",
    ):
        manager.correct_event(
            pack.source_event_id,
            expected_target_sha256=stored["canonical_sha256"],
            replacement_payload=replacement,
            idempotency_key="forbidden-decision-correction",
        )

    assert manager.doctor()["ok"] is True


def test_imported_context_pack_provenance_correction_fails_doctor(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    first_artifact = _artifact(manager)
    pack = manager.context_packs.publish(_draft(first_artifact), idempotency_key="publish-pack")
    second_source = manager.repo_root / "source-second.txt"
    second_source.write_text("second verified source", encoding="utf-8")
    second_artifact = manager.register_artifact(
        second_source,
        media_type="text/plain",
        classification="internal",
        idempotency_key="source-second",
    )
    stored = manager.show_event(pack.source_event_id)
    replacement = dict(stored["event"]["payload"])
    replacement["facts"] = _draft(second_artifact)["facts"]
    imported = manager.events.append_event(
        workspace_id=manager.workspace_id,
        event_type="event.corrected",
        payload_schema="commons.payload.maintenance.v1",
        payload={
            "target_event_id": pack.source_event_id,
            "expected_target_sha256": stored["canonical_sha256"],
            "replacement_payload": replacement,
        },
        actor=manager._actor(),
        subject_refs=({"kind": "event", "id": pack.source_event_id},),
        idempotency_namespace="imported-history",
        idempotency_key="context-pack-provenance-correction",
        provenance={
            "writer": "merge-test",
            "writer_version": "1",
            "source_kind": "manual",
            "source_refs": [],
        },
        tags=("maintenance", "correction"),
    )

    report = manager.doctor()

    assert imported.event_id
    assert report["ok"] is False
    assert any("publish context_pack.revised instead: facts" in issue for issue in report["issues"])


def test_context_pack_create_and_revise_stamp_semantics_floor_once(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    artifact = _artifact(manager)
    assert manager.snapshot().semantics_required == 1

    first = manager.context_packs.publish(_draft(artifact), idempotency_key="publish-pack")
    second = manager.context_packs.revise(
        first.context_pack_id,
        first.revision,
        _draft(artifact, "Second baseline"),
        idempotency_key="revise-pack",
    )
    event_types = [record.event["event_type"] for record in manager.events.iter_events()]

    assert manager.snapshot().semantics_required == 3
    assert SEMANTICS_SENSITIVE_EVENTS["context_pack.created"] == 3
    assert SEMANTICS_SENSITIVE_EVENTS["context_pack.revised"] == 3
    assert LEDGER_SEMANTICS_VERSION >= 3
    assert event_types.count("workspace.semantics_required") == 1
    assert event_types.index("workspace.semantics_required") < event_types.index(
        "context_pack.created"
    )
    assert second.revision != first.revision


def test_context_pack_revise_stamps_imported_unstamped_ledger(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    artifact = _artifact(manager)
    pack_id = "context_pack." + "0" * 25 + "7"
    imported = manager.events.append_event(
        workspace_id=manager.workspace_id,
        event_type="context_pack.created",
        payload_schema="commons.payload.context_pack.v1",
        payload={"context_pack_id": pack_id, **_draft(artifact)},
        actor=manager._actor(),
        subject_refs=({"kind": "context_pack", "id": pack_id},),
        idempotency_namespace="imported-history",
        idempotency_key="imported-unstamped-pack",
        provenance={
            "writer": "legacy-import",
            "writer_version": "1",
            "source_kind": "manual",
            "source_refs": [],
        },
        tags=("context_pack",),
    )
    assert manager.snapshot().semantics_required == 1

    revised = manager.context_packs.revise(
        pack_id,
        imported.event_id,
        _draft(artifact, "Revision after import"),
        idempotency_key="revise-imported-pack",
    )

    assert manager.snapshot().semantics_required == 3
    assert revised.draft.summary == "Revision after import"


def test_context_pack_post_lock_source_race_is_typed_and_unrelated_error_is_not_masked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)
    artifact = _artifact(manager)
    original_record_event = manager.record_event
    raced = False

    def record_with_source_race(event_type, payload, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal raced
        if event_type == "context_pack.created" and not raced:
            raced = True
            source = manager.repo_root / "source-internal.txt"
            source.write_text("revision created inside deterministic race", encoding="utf-8")
            manager.revise_artifact(
                str(artifact["entity_ref"]["id"]),
                str(artifact["revision"]),
                source,
                media_type="text/plain",
                classification="internal",
                idempotency_key="race-source-revision",
            )
        return original_record_event(event_type, payload, **kwargs)

    monkeypatch.setattr(manager, "record_event", record_with_source_race)
    with pytest.raises(ContextPackRefusal) as raced_refusal:
        manager.context_packs.publish(_draft(artifact), idempotency_key="raced-pack")
    assert raced_refusal.value.code is ContextPackRefusalCode.STALE
    assert not manager.snapshot().context_packs

    monkeypatch.setattr(manager, "record_event", original_record_event)
    artifact_id = str(artifact["entity_ref"]["id"])
    current_record = manager.snapshot().artifacts[artifact_id]
    current_artifact: dict[str, object] = {
        "entity_ref": {"kind": "artifact", "id": artifact_id},
        "revision": current_record["effective_revision"],
    }
    pack = manager.context_packs.publish(
        _draft(current_artifact), idempotency_key="stable-pack-after-race"
    )

    def unrelated_failure(event_type, payload, **kwargs):  # type: ignore[no-untyped-def]
        raise LifecycleConflictError("unrelated lifecycle invariant")

    monkeypatch.setattr(manager, "record_event", unrelated_failure)
    with pytest.raises(LifecycleConflictError, match="unrelated lifecycle invariant"):
        manager.context_packs.revise(
            pack.context_pack_id,
            pack.revision,
            _draft(current_artifact, "Unrelated failure"),
            idempotency_key="unrelated-failure",
        )


def test_context_pack_write_disable_preserves_read_compile_and_other_work(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    artifact = _artifact(manager)
    pack = manager.context_packs.publish(_draft(artifact), idempotency_key="publish-pack")
    before = len(list(manager.events.iter_events()))
    disabled = CommonsManager(
        manager.repo_root,
        state_root=manager.paths.state_root,
        session_id=manager.session_id,
        context_pack_writes_enabled=False,
    )

    assert disabled.context_packs.get(pack.context_pack_id, revision=pack.revision).revision == (
        pack.revision
    )
    assert disabled.context_packs.compile(
        pack.context_pack_id, pack.revision
    ).compiled_context_fingerprint
    with pytest.raises(ContextPackRefusal) as publish_disabled:
        disabled.context_packs.publish(_draft(artifact), idempotency_key="disabled-publish")
    assert publish_disabled.value.code is ContextPackRefusalCode.UNAVAILABLE
    with pytest.raises(ContextPackRefusal) as revise_disabled:
        disabled.context_packs.revise(
            pack.context_pack_id,
            pack.revision,
            _draft(artifact, "Disabled revision"),
            idempotency_key="disabled-revise",
        )
    assert revise_disabled.value.code is ContextPackRefusalCode.UNAVAILABLE
    with pytest.raises(ContextPackRefusal) as raw_write_disabled:
        disabled.record_event(
            "context_pack.created",
            {
                "context_pack_id": "context_pack." + "0" * 25 + "8",
                **_draft(artifact),
            },
            idempotency_key="disabled-raw-write",
            tags=("context_pack",),
        )
    assert raw_write_disabled.value.code is ContextPackRefusalCode.UNAVAILABLE
    assert len(list(disabled.events.iter_events())) == before

    fresh_work = disabled.create_task(
        title="Fresh-mode work remains available",
        description="Context Pack rollback does not disable unrelated work.",
        acceptance_criteria=("The task is canonical.",),
        idempotency_key="fresh-mode-still-available",
    )
    assert fresh_work["event_type"] == "task.created"

    with pytest.raises(ConfigurationError, match="must be a boolean"):
        CommonsManager(
            manager.repo_root,
            state_root=manager.paths.state_root,
            context_pack_writes_enabled="false",  # type: ignore[arg-type]
        )
