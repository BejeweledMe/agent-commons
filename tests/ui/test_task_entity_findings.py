"""Task entity records expose their projection-bound findings."""

from __future__ import annotations

from typing import Any

from agent_commons.services import CommonsManager
from tests.ui.conftest import authorized


def _manager(populated: dict[str, Any]) -> CommonsManager:
    return CommonsManager(
        populated["repo"],
        state_root=populated["state_root"],
        session_id=populated["session_id"],
    )


def _report(manager: CommonsManager, *, summary: str, evidence: dict[str, str], key: str) -> None:
    manager.report_finding(
        summary=summary,
        severity="medium",
        evidence_refs=(evidence,),
        idempotency_key=key,
    )


def _task_record(client: Any, task_id: str) -> dict[str, Any]:
    response = client.get(f"/api/entities/task/{task_id}", headers=authorized())
    assert response.status_code == 200, response.text
    return response.json()["record"]


def test_task_record_lists_its_findings_in_projection_order(
    client, populated: dict[str, Any]
) -> None:
    manager = _manager(populated)
    task_ref = {"kind": "task", "id": populated["task_id"]}
    _report(manager, summary="first finding", evidence=task_ref, key="task-finding-first")
    _report(manager, summary="second finding", evidence=task_ref, key="task-finding-second")

    findings = _task_record(client, populated["task_id"])["findings"]

    assert [finding["title"] for finding in findings] == ["first finding", "second finding"]
    assert all(set(finding) == {"id", "title", "severity", "state"} for finding in findings)
    assert all(finding["severity"] == "medium" for finding in findings)
    assert all(finding["state"] == "reported" for finding in findings)


def test_task_record_has_an_empty_findings_list_when_unbound(
    client, populated: dict[str, Any]
) -> None:
    assert _task_record(client, populated["task_id"])["findings"] == []


def test_task_record_excludes_findings_bound_elsewhere(client, populated: dict[str, Any]) -> None:
    manager = _manager(populated)
    other = manager.create_task(
        title="Other task",
        description="Must not leak findings.",
        acceptance_criteria=("separate",),
        idempotency_key="other-task-for-findings",
    )
    _report(
        manager,
        summary="other task finding",
        evidence={"kind": "task", "id": other["entity_ref"]["id"]},
        key="other-task-finding",
    )
    source = populated["repo"] / "evidence.txt"
    source.write_text("unrelated artifact", encoding="utf-8")
    artifact = manager.register_artifact(
        source,
        media_type="text/plain",
        idempotency_key="artifact-for-findings",
    )
    _report(
        manager,
        summary="artifact finding",
        evidence=artifact["entity_ref"],
        key="artifact-finding",
    )

    assert _task_record(client, populated["task_id"])["findings"] == []


def test_task_record_caps_findings_and_marks_truncation(client, populated: dict[str, Any]) -> None:
    manager = _manager(populated)
    task_ref = {"kind": "task", "id": populated["task_id"]}
    for index in range(51):
        _report(
            manager,
            summary=f"finding {index}",
            evidence=task_ref,
            key=f"task-finding-{index}",
        )

    record = _task_record(client, populated["task_id"])

    assert len(record["findings"]) == 50
    assert record["findings"][0]["title"] == "finding 0"
    assert record["findings"][-1]["title"] == "finding 49"
    assert record["findings_truncated"] is True
