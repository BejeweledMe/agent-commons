from __future__ import annotations

from collections.abc import Callable, Mapping

import pytest

from agent_commons.domain import envelopes as envelopes_module
from agent_commons.domain.agent_projection import AgentRecord
from agent_commons.domain.artifact_projection import ArtifactRecord
from agent_commons.domain.decision_projection import DecisionRecord
from agent_commons.domain.delegation_projection import DelegationRecord
from agent_commons.domain.envelopes import (
    FrozenJsonArray,
    FrozenJsonObject,
    TypedRef,
    freeze_json_object,
    thaw_json_field,
    thaw_json_object,
)
from agent_commons.domain.finding_projection import FindingRecord
from agent_commons.domain.handoff_projection import HandoffRecord
from agent_commons.domain.objective_projection import ObjectiveRecord
from agent_commons.domain.review_projection import ReviewRecord
from agent_commons.domain.task_projection import TaskRecord
from agent_commons.domain.thread_projection import ThreadRecord
from agent_commons.domain.verification_projection import VerificationRecord

RecordFactory = Callable[[], Mapping[str, object]]

_LARGE = {"blob": "x" * 64, "items": [{"n": index} for index in range(32)]}
_EXTENSIONS = {"source": {"path": "docs/task.md"}}


def _mapping_data(id_field: str, identifier: str) -> dict[str, object]:
    return {
        id_field: identifier,
        "id": identifier,
        "state": "active",
        "revision": "evt.1",
        "effective_revision": "evt.1",
        "title": "selected",
        "extensions": dict(_EXTENSIONS),
        "large": dict(_LARGE),
    }


def _verification_record() -> VerificationRecord:
    return VerificationRecord(
        verification_id="verification.1",
        target_ref=TypedRef(kind="artifact", identifier="artifact.1"),
        target_revision="evt.1",
        claim="ok",
        evidence_refs=(),
        method=None,
        outcome=None,
        extensions=None,
        revision="evt.2",
        effective_revision="evt.2",
        recorded_at="2026-01-01T00:00:00Z",
        actor=freeze_json_object({"session_id": "session.test", "role_id": "builder"}),
        author_session_ids=("session.test",),
        payload=freeze_json_object(
            {
                "verification_id": "verification.1",
                "target_ref": {"kind": "artifact", "id": "artifact.1"},
                "target_revision": "evt.1",
                "claim": "ok",
                "evidence_refs": [],
                "extensions": dict(_EXTENSIONS),
                "large": dict(_LARGE),
            }
        ),
        stale=False,
    )


_DATA_CASES: list[tuple[str, RecordFactory, str, object]] = [
    (
        "task",
        lambda: TaskRecord.from_projected_data(_mapping_data("task_id", "task.1")),
        "title",
        "selected",
    ),
    (
        "agent",
        lambda: AgentRecord.from_projected_data(_mapping_data("agent_id", "agent.1")),
        "title",
        "selected",
    ),
    (
        "artifact",
        lambda: ArtifactRecord.from_projected_data(_mapping_data("artifact_id", "artifact.1")),
        "title",
        "selected",
    ),
    (
        "decision",
        lambda: DecisionRecord.from_projected_data(_mapping_data("decision_id", "decision.1")),
        "title",
        "selected",
    ),
    (
        "delegation",
        lambda: DelegationRecord.from_projected_data(
            _mapping_data("delegation_id", "delegation.1")
        ),
        "title",
        "selected",
    ),
    (
        "finding",
        lambda: FindingRecord.from_projected_data(_mapping_data("finding_id", "finding.1")),
        "title",
        "selected",
    ),
    (
        "handoff",
        lambda: HandoffRecord.from_projected_data(_mapping_data("handoff_id", "handoff.1")),
        "title",
        "selected",
    ),
    (
        "objective",
        lambda: ObjectiveRecord.from_projected_data(_mapping_data("objective_id", "objective.1")),
        "title",
        "selected",
    ),
    (
        "review",
        lambda: ReviewRecord.from_projected_data(_mapping_data("review_id", "review.1")),
        "title",
        "selected",
    ),
    (
        "thread",
        lambda: ThreadRecord.from_projected_data(_mapping_data("thread_id", "thread.1")),
        "title",
        "selected",
    ),
    ("verification", _verification_record, "claim", "ok"),
]


def _frozen_root(record: Mapping[str, object]) -> FrozenJsonObject:
    if isinstance(record, VerificationRecord):
        return record.payload
    data = record.data  # type: ignore[attr-defined]
    assert isinstance(data, FrozenJsonObject)
    return data


def _frozen_child(record: Mapping[str, object], field: str) -> FrozenJsonObject | FrozenJsonArray:
    for key, child in _frozen_root(record).values:
        if key == field:
            assert isinstance(child, (FrozenJsonObject, FrozenJsonArray))
            return child
    raise AssertionError(f"{field} is missing from frozen projection data")


def test_thaw_json_field_copies_one_field_and_raises_for_missing_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen = freeze_json_object({"small": "ok", "nested": dict(_EXTENSIONS), "large": dict(_LARGE)})
    large_child = next(child for key, child in frozen.values if key == "large")
    walked: list[object] = []
    original = envelopes_module._thaw_json

    def tracked(value: object) -> object:
        walked.append(value)
        return original(value)

    monkeypatch.setattr(envelopes_module, "_thaw_json", tracked)

    assert thaw_json_field(frozen, "small") == "ok"
    assert large_child not in walked
    with pytest.raises(KeyError, match="^'missing'$"):
        thaw_json_field(frozen, "missing")

    nested = thaw_json_field(frozen, "nested")
    assert nested == _EXTENSIONS
    assert isinstance(nested, dict)
    nested["source"]["path"] = "tampered"
    assert thaw_json_field(frozen, "nested") == _EXTENSIONS
    assert thaw_json_object(frozen)["nested"] == _EXTENSIONS


@pytest.mark.parametrize(
    ("factory", "selected_key", "selected_value"),
    [item[1:] for item in _DATA_CASES],
    ids=[item[0] for item in _DATA_CASES],
)
def test_nested_field_mutations_cannot_change_frozen_records(
    factory: RecordFactory,
    selected_key: str,
    selected_value: object,
) -> None:
    record = factory()
    first = record["extensions"]
    second = record["extensions"]
    assert isinstance(first, dict)
    assert first == _EXTENSIONS
    assert first is not second
    first["source"]["path"] = "tampered"
    assert record["extensions"] == _EXTENSIONS
    assert record[selected_key] == selected_value
    with pytest.raises(KeyError):
        record["missing"]  # type: ignore[index]


@pytest.mark.parametrize(
    ("factory", "selected_key", "selected_value"),
    [item[1:] for item in _DATA_CASES],
    ids=[item[0] for item in _DATA_CASES],
)
def test_selected_field_access_does_not_walk_unrelated_large_fields(
    factory: RecordFactory,
    selected_key: str,
    selected_value: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = factory()
    large_child = _frozen_child(record, "large")
    walked: list[object] = []
    original = envelopes_module._thaw_json

    def tracked(value: object) -> object:
        walked.append(value)
        return original(value)

    monkeypatch.setattr(envelopes_module, "_thaw_json", tracked)
    monkeypatch.setattr(
        envelopes_module,
        "thaw_json_object",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("whole-record thaw")),
    )

    assert record[selected_key] == selected_value
    assert large_child not in walked


@pytest.mark.parametrize(
    "factory",
    [item[1] for item in _DATA_CASES],
    ids=[item[0] for item in _DATA_CASES],
)
def test_projection_mapping_output_matches_to_dict(factory: RecordFactory) -> None:
    record = factory()
    wire = record.to_dict()  # type: ignore[attr-defined]
    assert list(record) == list(wire)
    assert len(record) == len(wire)
    assert dict(record) == wire
