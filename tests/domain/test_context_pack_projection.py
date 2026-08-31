from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_commons.core.canonical import canonical_sha256
from agent_commons.domain.projection import project_events

WORKSPACE_ID = "workspace." + "0" * 25 + "1"
PACK_ID = "context_pack." + "0" * 25 + "1"


def _event(number: int, event_type: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "event_id": f"evt.{number:026d}",
        "workspace_id": WORKSPACE_ID,
        "event_type": event_type,
        "recorded_at": (datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=number))
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "actor": {"session_id": "session.builder", "role_id": "researcher"},
        "payload": payload,
        "subject_refs": [{"kind": "context_pack", "id": PACK_ID}],
        "relations": [],
    }


def _draft(summary: str) -> dict[str, object]:
    return {
        "summary": summary,
        "facts": [],
        "decision_refs": [],
        "open_questions": [],
    }


def test_old_ledger_replays_with_no_context_pack_wire_shape_change() -> None:
    fixture_path = Path(__file__).parents[1] / "fixtures/context_pack/pre_c1_ledger.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    snapshot = project_events(fixture["events"])

    assert snapshot.context_packs == {}
    assert snapshot.context_pack_revisions == {}
    assert "context_packs" not in snapshot.to_dict()
    assert snapshot.issues == []
    assert snapshot.semantics_required == fixture["expected_semantics_required"] == 1
    assert canonical_sha256(snapshot.to_dict()) == fixture["expected_snapshot_sha256"]


def test_context_pack_projection_retains_exact_immutable_revisions() -> None:
    created = _event(
        1,
        "context_pack.created",
        {"context_pack_id": PACK_ID, **_draft("First frozen baseline")},
    )
    revised = _event(
        2,
        "context_pack.revised",
        {
            "context_pack_id": PACK_ID,
            "expected_revision": created["event_id"],
            **_draft("Second frozen baseline"),
        },
    )

    snapshot = project_events([revised, created])

    assert snapshot.issues == []
    assert snapshot.context_packs[PACK_ID].draft.summary == "Second frozen baseline"
    first = snapshot.context_pack_revisions[(PACK_ID, str(created["event_id"]))]
    second = snapshot.context_pack_revisions[(PACK_ID, str(revised["event_id"]))]
    assert first.draft.summary == "First frozen baseline"
    assert second.draft.summary == "Second frozen baseline"
    assert first is not second
    assert snapshot.to_dict()["context_packs"][0]["revision"] == revised["event_id"]


def test_context_pack_projection_rejects_stale_revision_without_rebinding() -> None:
    created = _event(
        1,
        "context_pack.created",
        {"context_pack_id": PACK_ID, **_draft("First frozen baseline")},
    )
    stale = _event(
        2,
        "context_pack.revised",
        {
            "context_pack_id": PACK_ID,
            "expected_revision": "evt." + "0" * 25 + "9",
            **_draft("Must not apply"),
        },
    )

    snapshot = project_events([created, stale])

    assert snapshot.context_packs[PACK_ID].draft.summary == "First frozen baseline"
    assert any(issue.code == "lifecycle_rejected" for issue in snapshot.issues)
    assert (PACK_ID, str(stale["event_id"])) not in snapshot.context_pack_revisions


def test_context_pack_correction_gets_new_effective_revision_and_stales_successor() -> None:
    created = _event(
        1,
        "context_pack.created",
        {"context_pack_id": PACK_ID, **_draft("First frozen baseline")},
    )
    correction = _event(
        2,
        "event.corrected",
        {
            "target_event_id": created["event_id"],
            "expected_target_sha256": canonical_sha256(created),
            "replacement_payload": {
                "context_pack_id": PACK_ID,
                **_draft("Corrected frozen baseline"),
            },
        },
    )
    correction["subject_refs"] = [{"kind": "event", "id": created["event_id"]}]
    successor = _event(
        3,
        "context_pack.revised",
        {
            "context_pack_id": PACK_ID,
            "expected_revision": created["event_id"],
            **_draft("Successor built on stale bytes"),
        },
    )

    snapshot = project_events([created, correction, successor])

    current = snapshot.context_packs[PACK_ID]
    assert current.revision == correction["event_id"]
    assert current.draft.summary == "Corrected frozen baseline"
    assert (PACK_ID, str(created["event_id"])) not in snapshot.context_pack_revisions
    assert (PACK_ID, str(correction["event_id"])) in snapshot.context_pack_revisions
    assert any(issue.code == "lifecycle_rejected" for issue in snapshot.issues)


def test_context_pack_correction_cannot_change_facts_without_new_revision() -> None:
    task_id = "task." + "0" * 25 + "1"
    source = _event(
        1,
        "task.created",
        {
            "task_id": task_id,
            "title": "Canonical source",
            "description": "A source whose exact revision is bound.",
            "acceptance_criteria": ["The binding remains immutable."],
            "priority": "normal",
        },
    )
    source["subject_refs"] = [{"kind": "task", "id": task_id}]
    created = _event(
        2,
        "context_pack.created",
        {"context_pack_id": PACK_ID, **_draft("Frozen baseline")},
    )
    correction = _event(
        3,
        "event.corrected",
        {
            "target_event_id": created["event_id"],
            "expected_target_sha256": canonical_sha256(created),
            "replacement_payload": {
                "context_pack_id": PACK_ID,
                "summary": "Frozen baseline",
                "facts": [
                    {
                        "statement": "A new fact needs a canonical revision.",
                        "source_refs": [
                            {
                                "ref": {"kind": "task", "id": task_id},
                                "revision": source["event_id"],
                            }
                        ],
                    }
                ],
                "decision_refs": [],
                "open_questions": [],
            },
        },
    )
    correction["subject_refs"] = [{"kind": "event", "id": created["event_id"]}]

    snapshot = project_events([source, created, correction])

    assert PACK_ID not in snapshot.context_packs
    assert any(
        issue.code == "context_pack_correction_provenance_change" for issue in snapshot.issues
    )
    assert any("context_pack.revised" in issue.message for issue in snapshot.issues)


def test_context_pack_semantics_stamp_makes_v2_reader_fail_closed(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    from agent_commons.domain import projection as projection_module

    stamp = _event(
        1,
        "workspace.semantics_required",
        {
            "workspace_id": WORKSPACE_ID,
            "semantics_version": 3,
            "reason": "Context Pack replay requires semantics version 3",
        },
    )
    stamp["subject_refs"] = [{"kind": "workspace", "id": WORKSPACE_ID}]
    created = _event(
        2,
        "context_pack.created",
        {"context_pack_id": PACK_ID, **_draft("Frozen baseline")},
    )
    monkeypatch.setattr(projection_module, "LEDGER_SEMANTICS_VERSION", 2)

    snapshot = projection_module.project_events([stamp, created])

    assert snapshot.semantics_required == 3
    assert [issue.code for issue in snapshot.issues] == ["ledger_ahead_of_code"]
    assert "update agent-commons" in snapshot.issues[0].message
