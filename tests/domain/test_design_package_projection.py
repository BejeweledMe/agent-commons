from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_commons.core.canonical import canonical_sha256
from agent_commons.domain.projection import project_events

WORKSPACE_ID = "workspace." + "0" * 25 + "1"
PACKAGE_ID = "design_package." + "0" * 25 + "1"
ARTIFACT_ID = "artifact." + "0" * 25 + "1"
TASK_ID = "task." + "0" * 25 + "1"
SCREEN_ID = "screen." + "0" * 25 + "1"


def _event(number: int, event_type: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "event_id": f"evt.{number:026d}",
        "workspace_id": WORKSPACE_ID,
        "event_type": event_type,
        "recorded_at": (datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=number))
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "actor": {"session_id": "session.builder", "role_id": "designer"},
        "payload": payload,
        "subject_refs": [{"kind": "design_package", "id": PACKAGE_ID}],
        "relations": [],
    }


def _draft(title: str) -> dict[str, object]:
    return {
        "title": title,
        "screens": [
            {
                "screen_id": SCREEN_ID,
                "ordinal": 1,
                "title": "Checkout",
                "artifact_binding": {
                    "ref": {"kind": "artifact", "id": ARTIFACT_ID},
                    "revision": "evt." + "0" * 25 + "1",
                },
                "artifact_content_revision": "sha256:" + "a" * 64,
                "producer_task_binding": {
                    "ref": {"kind": "task", "id": TASK_ID},
                    "revision": "evt." + "0" * 25 + "4",
                },
                "classification": "internal",
                "media_type": "image/png",
                "safe_preview_eligible": True,
            }
        ],
    }


def _provenance_events() -> list[dict[str, object]]:
    artifact_ref = {"kind": "artifact", "id": ARTIFACT_ID}
    task_ref = {"kind": "task", "id": TASK_ID}
    artifact = _event(
        1,
        "artifact.registered",
        {
            "artifact_id": ARTIFACT_ID,
            "manifest_ref": "mft.artifact.sha256." + "1" * 64,
            "revision": "sha256:" + "a" * 64,
            "classification": "internal",
        },
    )
    artifact["subject_refs"] = [artifact_ref]
    task = _event(
        2,
        "task.created",
        {
            "task_id": TASK_ID,
            "title": "Produce checkout",
            "description": "Create the exact screen artifact.",
            "acceptance_criteria": ["The artifact revision is bound."],
            "priority": "normal",
        },
    )
    task["subject_refs"] = [task_ref]
    started = _event(
        3,
        "task.started",
        {"task_id": TASK_ID, "expected_revision": task["event_id"]},
    )
    started["subject_refs"] = [task_ref]
    completed = _event(
        4,
        "task.completed",
        {
            "task_id": TASK_ID,
            "expected_revision": started["event_id"],
            "summary": "The exact screen artifact is ready.",
            "artifact_refs": [artifact_ref],
            "artifact_bindings": [{"ref": artifact_ref, "revision": artifact["event_id"]}],
        },
    )
    completed["subject_refs"] = [task_ref]
    return [artifact, task, started, completed]


def test_pre_g1_ledger_has_no_design_package_wire_shape_change() -> None:
    fixture_path = Path(__file__).parents[1] / "fixtures/design_package/pre_g1_ledger.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    snapshot = project_events(fixture["events"])

    assert snapshot.design_packages == {}
    assert snapshot.design_package_revisions == {}
    assert "design_packages" not in snapshot.to_dict()
    assert snapshot.issues == []
    assert snapshot.semantics_required == fixture["expected_semantics_required"] == 1
    assert canonical_sha256(snapshot.to_dict()) == fixture["expected_snapshot_sha256"]


def test_design_package_projection_retains_exact_revisions() -> None:
    # The package reducer is tested with dependencies represented by canonical
    # current records in service tests; this test pins deterministic history.
    created = _event(
        5,
        "design_package.created",
        {"design_package_id": PACKAGE_ID, **_draft("First package")},
    )
    revised = _event(
        6,
        "design_package.revised",
        {
            "design_package_id": PACKAGE_ID,
            "expected_revision": created["event_id"],
            **_draft("Second package"),
        },
    )
    snapshot = project_events([*_provenance_events(), created, revised])

    assert snapshot.issues == []
    assert snapshot.design_packages[PACKAGE_ID].draft.title == "Second package"
    first = snapshot.design_package_revisions[(PACKAGE_ID, str(created["event_id"]))]
    second = snapshot.design_package_revisions[(PACKAGE_ID, str(revised["event_id"]))]
    assert first.draft.title == "First package"
    assert second.draft.title == "Second package"
    assert first.producer_session_id == "session.builder"
    assert first is not second


def test_design_package_correction_cannot_change_screen_provenance() -> None:
    created = _event(
        5,
        "design_package.created",
        {"design_package_id": PACKAGE_ID, **_draft("Package")},
    )
    changed = _draft("Package")
    screens = changed["screens"]
    assert isinstance(screens, list) and isinstance(screens[0], dict)
    screens[0]["artifact_content_revision"] = "sha256:" + "b" * 64
    correction = _event(
        6,
        "event.corrected",
        {
            "target_event_id": created["event_id"],
            "expected_target_sha256": canonical_sha256(created),
            "replacement_payload": {"design_package_id": PACKAGE_ID, **changed},
        },
    )
    correction["subject_refs"] = [{"kind": "event", "id": created["event_id"]}]

    snapshot = project_events([*_provenance_events(), created, correction])

    assert PACKAGE_ID not in snapshot.design_packages
    assert any(
        issue.code == "design_package_correction_provenance_change" for issue in snapshot.issues
    )


def test_design_package_semantics_stamp_makes_v3_reader_fail_closed(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    from agent_commons.domain import projection as projection_module

    stamp = _event(
        0,
        "workspace.semantics_required",
        {
            "workspace_id": WORKSPACE_ID,
            "semantics_version": 4,
            "reason": "Design Package replay requires semantics version 4",
        },
    )
    stamp["subject_refs"] = [{"kind": "workspace", "id": WORKSPACE_ID}]
    created = _event(
        5,
        "design_package.created",
        {"design_package_id": PACKAGE_ID, **_draft("Package")},
    )
    monkeypatch.setattr(projection_module, "LEDGER_SEMANTICS_VERSION", 3)

    snapshot = projection_module.project_events([stamp, *_provenance_events(), created])

    assert snapshot.semantics_required == 4
    assert [issue.code for issue in snapshot.issues] == ["ledger_ahead_of_code"]
    assert "update agent-commons" in snapshot.issues[0].message
