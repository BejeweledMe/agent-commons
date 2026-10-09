from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from agent_commons.cli import cli
from agent_commons.services import CommonsManager
from tests.services.test_task_evidence_snapshot import active_task, manager  # noqa: F401


@pytest.mark.parametrize("selection", ["omitted", "replace", "clear"])
def test_submit_evidence_omission_retains_and_explicit_selection_replaces(
    manager: CommonsManager,  # noqa: F811 - imported pytest fixture
    selection,
):
    source = manager.repo_root / "result.txt"
    source.write_text("version one")
    artifact = manager.register_artifact(source, idempotency_key="result-artifact")
    active = active_task(manager)
    completed = manager.complete_task(
        active["entity_ref"]["id"],
        active["revision"],
        summary="done",
        artifact_refs=[artifact["entity_ref"]],
        idempotency_key="complete",
    )
    args = []
    expected = [artifact["entity_ref"]]
    if selection == "replace":
        other = manager.repo_root / "replacement.txt"
        other.write_text("version two")
        replacement = manager.register_artifact(other, idempotency_key="replacement")
        args = ["--artifact-ref", "artifact:" + replacement["entity_ref"]["id"]]
        expected = [replacement["entity_ref"]]
    elif selection == "clear":
        args = ["--clear-artifacts"]
        expected = []
    result = CliRunner().invoke(
        cli,
        [
            "--repo",
            str(manager.repo_root),
            "--state-root",
            str(manager.paths.state_root),
            "--session-id",
            manager.session_id,
            "--json",
            "task",
            "submit",
            active["entity_ref"]["id"],
            completed["revision"],
            "--summary",
            "review",
            "--idempotency-key",
            "cli-submit",
            *args,
        ],
    )
    assert result.exit_code == 0, result.output
    submitted = json.loads(result.output)
    payload = manager.show_event(submitted["event_id"])["event"]["payload"]
    assert payload["artifact_refs"] == expected
    assert [binding["ref"] for binding in payload["artifact_bindings"]] == expected


@pytest.mark.parametrize("action", ["complete", "submit"])
def test_clear_and_replacement_are_mutually_exclusive_before_manager(action):
    result = CliRunner().invoke(
        cli,
        [
            "task",
            action,
            "task.test",
            "evt.test",
            "--summary",
            "done",
            "--clear-artifacts",
            "--artifact-ref",
            "artifact:artifact.test",
        ],
    )
    assert result.exit_code != 0
    assert "cannot be combined" in result.output
