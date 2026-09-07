from __future__ import annotations

import json

import pytest

from agent_commons.ui.tracker_reads import build_tracker_snapshot
from tests.ui.test_tracker_reads import NOW, _graph, _snapshot

AGENT = "agent." + "0" * 26


def _suggestion_snapshot():
    snapshot = _snapshot()
    snapshot.tasks["task.next"]["extensions"] = {"suggested_agent_id": AGENT, "private": "hidden"}
    snapshot.agents[AGENT] = {
        "id": AGENT,
        "name": "Claude Frontender",
        "profile_id": "claude-builder",
        "state": "active",
        "template": False,
        "recorded_at": "2026-08-30T10:00:55Z",
        "system_prompt": "hidden",
        "private_path": "hidden",
    }
    return snapshot


def _wire(snapshot):
    return build_tracker_snapshot(
        snapshot, [], generated_at=NOW, sequence=1, graph=_graph()
    ).to_wire()


def test_suggested_role_is_bounded_metadata_separate_from_owner_and_readiness() -> None:
    original = _wire(_snapshot())
    suggested = _wire(_suggestion_snapshot())
    before = next(item for item in original["tasks"] if item["task_id"] == "task.next")
    after = next(item for item in suggested["tasks"] if item["task_id"] == "task.next")
    assert after["suggested_agent_id"] == AGENT
    assert after["suggested_role_name"] == "Claude Frontender"
    assert after["suggested_provider"] == "claude"
    assert {
        key: value for key, value in after.items() if not key.startswith("suggested_")
    } == before
    assert after["owner_session_id"] is None and after["role_name"] is None
    assert "hidden" not in json.dumps(suggested)
    assert suggested["freshness"] == original["freshness"]


@pytest.mark.parametrize(
    "change",
    [
        {"state": "retired"},
        {"template": True},
        {"name": None},
        {"profile_id": "private-provider"},
    ],
)
def test_unavailable_suggestions_do_not_become_assignments(change: dict) -> None:
    snapshot = _suggestion_snapshot()
    snapshot.agents[AGENT].update(change)
    record = next(item for item in _wire(snapshot)["tasks"] if item["task_id"] == "task.next")
    assert not any(key.startswith("suggested_") for key in record)
    assert record["owner_session_id"] is None and record["role_name"] is None


def test_suggested_name_is_printable_and_bounded_without_adding_role_content() -> None:
    snapshot = _suggestion_snapshot()
    snapshot.agents[AGENT]["name"] = "\x00\u202e" + "Я" * 200
    record = next(item for item in _wire(snapshot)["tasks"] if item["task_id"] == "task.next")
    assert len(record["suggested_role_name"].encode()) <= 160
    assert all(character.isprintable() for character in record["suggested_role_name"])
    assert "system_prompt" not in json.dumps(record)


def test_malformed_or_missing_suggested_agent_is_omitted() -> None:
    for identifier in ("agent.bad?secret", "agent." + "1" * 26, {"id": AGENT}):
        snapshot = _suggestion_snapshot()
        snapshot.tasks["task.next"]["extensions"]["suggested_agent_id"] = identifier
        record = next(item for item in _wire(snapshot)["tasks"] if item["task_id"] == "task.next")
        assert not any(key.startswith("suggested_") for key in record)
