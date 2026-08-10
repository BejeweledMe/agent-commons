"""Full-text search over the ledger, entered the way a user enters it.

ADR 0008 removed a store that had no producer and no consumer.  The index and
the two things that read it -- `agent-commons search` and the panel's search box
-- land together for that reason, and these tests go through those, not through
the table.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from agent_commons.cli import cli
from agent_commons.index.search_text import (
    SEARCHABLE_LIST_FIELDS,
    SEARCHABLE_TEXT_FIELDS,
    searchable_text,
)
from agent_commons.services import CommonsManager


@pytest.fixture
def workspace(tmp_path: Path) -> dict[str, Any]:
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="search")
    manager = CommonsManager(repo)
    session = manager.start_session(
        stable_instance_id="search-operator-window-01",
        principal="operator",
        client="codex",
        software="codex-cli",
        role="operator",
    )
    manager.session_id = session["session_id"]
    manager.create_task(
        title="Rotate the payment gateway credentials",
        description="The gateway rejects the old signing key after October.",
        acceptance_criteria=("payments settle end to end",),
        idempotency_key="search-task",
    )
    manager.create_agent(
        name="Senior Node.js backend",
        profile_id="claude-builder",
        rationale="the payments surface needs a standing owner between tasks",
        idempotency_key="search-role",
    )
    return {"repo": repo, "manager": manager, "session_id": session["session_id"]}


def _search(workspace: dict[str, Any], *args: str) -> dict[str, Any]:
    result = CliRunner().invoke(
        cli,
        ["--repo", str(workspace["repo"]), "--session-id", workspace["session_id"], "--json",
         "search", *args],
    )
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_search_finds_a_role_by_its_rationale(workspace: dict[str, Any]) -> None:
    payload = _search(workspace, "standing owner")

    assert payload["count"] >= 1
    kinds = {item["subject"]["kind"] for item in payload["results"]}
    assert "agent" in kinds
    assert payload["source"] == "rebuildable_projection"


def test_search_finds_a_task_by_words_from_its_description(workspace: dict[str, Any]) -> None:
    payload = _search(workspace, "signing key")

    assert [item["subject"]["kind"] for item in payload["results"]] == ["task"]
    assert "signing" in payload["results"][0]["excerpt"]


def test_search_can_be_restricted_to_one_entity_kind(workspace: dict[str, Any]) -> None:
    payload = _search(workspace, "payments", "--kind", "agent")

    assert payload["count"] >= 1
    assert {item["subject"]["kind"] for item in payload["results"]} == {"agent"}


def test_prose_that_is_not_valid_fts_syntax_still_searches(workspace: dict[str, Any]) -> None:
    """People type questions, not query grammar."""

    payload = _search(workspace, "what about the gateway credentials?")

    assert payload["count"] >= 1
    # And it says which widening answered, so a loose match never reads as an
    # exact one.
    assert payload["match"] == "any_term"


def test_an_exact_match_is_reported_as_an_exact_match(workspace: dict[str, Any]) -> None:
    assert _search(workspace, "signing key")["match"] == "all_terms"


def test_a_new_event_is_searchable_without_an_explicit_rebuild(
    workspace: dict[str, Any],
) -> None:
    assert _search(workspace, "quarterly reconciliation")["count"] == 0
    workspace["manager"].create_task(
        title="Quarterly reconciliation",
        description="Compare the ledger against the gateway statements.",
        acceptance_criteria=("totals agree",),
        idempotency_key="search-task-2",
    )
    payload = _search(workspace, "quarterly reconciliation")

    assert payload["count"] == 1
    assert payload["index"]["synchronized"] is True


def test_a_read_only_search_never_builds_the_projection(tmp_path: Path) -> None:
    """A read-only command that creates state is a bug this project has shipped."""

    repo = tmp_path / "ro"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="search-ro")
    manager = CommonsManager(repo, read_only=True)
    assert not manager.paths.index_db.exists()

    payload = manager.search_history("anything at all")

    assert payload["results"] == []
    assert payload["index"] == {"available": False, "synchronized": False}
    assert "index rebuild" in payload["unavailable"]
    assert not manager.paths.index_db.exists()
    assert not list(manager.paths.state_root.glob("index.sqlite3*"))


def test_a_read_only_search_reads_an_existing_projection_without_changing_it(
    workspace: dict[str, Any],
) -> None:
    _search(workspace, "warm the index")
    database = workspace["manager"].paths.index_db
    before = database.stat().st_mtime_ns

    reader = CommonsManager(workspace["repo"], read_only=True)
    payload = reader.search_history("signing key")

    assert payload["count"] >= 1
    assert payload["index"]["synchronized"] is False
    assert database.stat().st_mtime_ns == before


def test_only_allowlisted_fields_become_searchable() -> None:
    """A field is indexed because it is listed, never because it exists.

    A denylist would silently index whatever somebody adds next, and the
    material worth keeping out is exactly what gets added without thinking.
    """

    event = {
        "event_type": "task.created",
        "recorded_at": "2026-01-01T00:00:00Z",
        "actor": {"session_id": "session.abc", "role_id": "builder"},
        "payload": {
            "title": "Indexed title",
            "acceptance_criteria": ["indexed criterion"],
            "provider_stdout": "SHOULD NOT BE INDEXED",
            "tool_arguments": {"path": "SHOULD NOT BE INDEXED"},
            "transcript": "SHOULD NOT BE INDEXED",
        },
    }
    text = searchable_text(event)

    assert "Indexed title" in text
    assert "indexed criterion" in text
    assert "builder" in text
    assert "SHOULD NOT BE INDEXED" not in text
    assert not set(SEARCHABLE_TEXT_FIELDS) & {"provider_stdout", "transcript"}
    assert "tool_arguments" not in set(SEARCHABLE_LIST_FIELDS)


def test_the_index_document_is_bounded(workspace: dict[str, Any]) -> None:
    event = {
        "event_type": "task.created",
        "payload": {"description": "x" * 100_000, "title": "y" * 100_000},
    }
    assert len(searchable_text(event)) <= 32_000
