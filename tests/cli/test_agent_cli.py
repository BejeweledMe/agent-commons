"""Standing roles, exercised through the command a user actually types.

Every assertion here enters through the CLI rather than a helper, because a
guarantee proved one layer beside the real path has been green over a broken
product four times in this branch.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner, Result

from agent_commons.cli import cli
from agent_commons.services import CommonsManager

LIMITS = {
    "max_depth": 0,
    "wall_time_seconds": 600,
    "max_attempts": 1,
    "max_concurrency": 1,
    "budget": {"unit": "tokens", "limit": 8000},
}


_AUTOMATIC_LEVEL_WITHHELD = (
    "the automatic grant level is withheld until its guarantees hold "
    "(docs/audits/2026-08-10-standing-roles-review.md, remediation step 1); "
    "restored later in this branch"
)


def _invoke(runner: CliRunner, repo: Path, session_id: str, *args: str) -> Result:
    return runner.invoke(cli, ["--repo", str(repo), "--session-id", session_id, "--json", *args])


def _json(result: Result) -> Any:
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _refused(result: Result, fragment: str) -> None:
    assert result.exit_code != 0, result.output
    assert fragment in result.output, result.output


@pytest.fixture
def workspace(tmp_path: Path) -> dict[str, Any]:
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="agent-cli")
    manager = CommonsManager(repo)
    human = manager.start_session(
        stable_instance_id="agent-cli-human-1234567890",
        principal="operator",
        client="codex",
        software="codex-cli",
        role="operator",
    )
    manager.session_id = human["session_id"]
    return {"repo": repo, "manager": manager, "human": human, "runner": CliRunner()}


def _create(
    workspace: dict[str, Any],
    key: str,
    *extra: str,
    session_id: str | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    result = _invoke(
        workspace["runner"],
        workspace["repo"],
        session_id or workspace["human"]["session_id"],
        "agent",
        "create",
        "--name",
        name or key,
        "--profile",
        "claude-builder",
        "--rationale",
        f"seam coverage for {key}",
        "--idempotency-key",
        key,
        *extra,
    )
    return _json(result)


def _new_session(workspace: dict[str, Any], suffix: str) -> str:
    manager = CommonsManager(workspace["repo"])
    session = manager.start_session(
        stable_instance_id=f"agent-cli-{suffix}".ljust(24, "0")[:40],
        principal="operator",
        client="claude",
        software="claude-code",
        role="builder",
    )
    return str(session["session_id"])


def _run_as(workspace: dict[str, Any], agent_id: str, key: str) -> str:
    """Start a delegation bound to a role and return its child session id.

    This is the only way a session comes to act for a role, so tests that check
    an agent's authority have to go through it rather than assert on a helper.
    """

    manager: CommonsManager = workspace["manager"]
    task = manager.create_task(
        title=f"work for {key}",
        description="target for a role-bound run",
        acceptance_criteria=("bound",),
        idempotency_key=f"{key}-task",
    )
    delegation = manager.create_delegation(
        target_ref={"kind": "task", "id": task["entity_ref"]["id"]},
        target_revision=task["revision"],
        target_profile="claude-builder",
        purpose="implementation",
        limits=LIMITS,
        on_behalf_of_agent_id=agent_id,
        idempotency_key=f"{key}-delegation",
    )
    child_session_id = _new_session(workspace, key)
    started = manager.start_delegation(
        delegation["entity_ref"]["id"],
        delegation["revision"],
        child_session_id=child_session_id,
        idempotency_key=f"{key}-start",
    )
    workspace.setdefault("runs", {})[key] = started
    return child_session_id


def _finish_run(workspace: dict[str, Any], key: str) -> None:
    """End the run so the role stops owing live work."""

    started = workspace["runs"][key]
    workspace["manager"].fail_delegation(
        started["entity_ref"]["id"],
        started["revision"],
        reason_code="runtime_error",
        summary="ended for the purposes of this test",
        idempotency_key=f"{key}-fail",
    )


# -- shape and provenance ----------------------------------------------------


def test_a_human_created_role_is_marked_as_such_and_denies_everything_by_default(
    workspace: dict[str, Any],
) -> None:
    created = _create(workspace, "plain-role")
    shown = _json(
        _invoke(
            workspace["runner"],
            workspace["repo"],
            workspace["human"]["session_id"],
            "agent",
            "show",
            created["entity_ref"]["id"],
        )
    )
    assert shown["origin"] == "human"
    assert shown["approval"] == "human"
    assert shown["effective_grants"] == {
        "create_roles": "deny",
        "retire_roles": "deny",
        "open_links": "deny",
    }
    assert shown["rationale"] == "seam coverage for plain-role"


def test_requesting_auto_warns_that_the_level_is_withheld(
    workspace: dict[str, Any],
) -> None:
    """Round 2: `auto` is accepted but withheld, so an interactive create says
    so rather than letting the operator believe the role acts autonomously."""

    result = workspace["runner"].invoke(
        cli,
        [
            "--repo",
            str(workspace["repo"]),
            "--session-id",
            workspace["human"]["session_id"],
            "agent",
            "create",
            "--name",
            "Autonomous?",
            "--profile",
            "claude-builder",
            "--rationale",
            "wants auto",
            "--create-roles",
            "auto",
            "--turnover-budget",
            "4",
            "--idempotency-key",
            "auto-warn",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "automatic grant level is currently withheld" in result.output


def test_granting_creation_without_a_turnover_budget_is_refused(
    workspace: dict[str, Any],
) -> None:
    _refused(
        _invoke(
            workspace["runner"],
            workspace["repo"],
            workspace["human"]["session_id"],
            "agent",
            "create",
            "--name",
            "unbounded",
            "--profile",
            "claude-builder",
            "--rationale",
            "no ceiling",
            "--create-roles",
            "auto",
            "--idempotency-key",
            "unbounded",
        ),
        "turnover_budget",
    )


# -- guarantee 3: the level strictly decreases -------------------------------


@pytest.mark.skip(reason=_AUTOMATIC_LEVEL_WITHHELD)
def test_an_automatic_generation_narrows_and_the_third_is_refused(
    workspace: dict[str, Any],
) -> None:
    root = _create(
        workspace,
        "root-auto",
        "--create-roles",
        "auto",
        "--turnover-budget",
        "8",
    )
    root_id = root["entity_ref"]["id"]
    root_session = _run_as(workspace, root_id, "gen1")

    second = _create(
        workspace,
        "gen2",
        "--create-roles",
        "ask",
        "--turnover-budget",
        "4",
        "--created-by-agent",
        root_id,
        session_id=root_session,
    )
    assert second["event_type"] == "agent.created"

    # Same level again would let one grant produce generations without end.
    _refused(
        _invoke(
            workspace["runner"],
            workspace["repo"],
            root_session,
            "agent",
            "create",
            "--name",
            "gen2-clone",
            "--profile",
            "claude-builder",
            "--rationale",
            "same level again",
            "--create-roles",
            "auto",
            "--turnover-budget",
            "4",
            "--created-by-agent",
            root_id,
            "--idempotency-key",
            "gen2-clone",
        ),
        "strictly narrower",
    )


@pytest.mark.skip(reason=_AUTOMATIC_LEVEL_WITHHELD)
def test_a_created_role_cannot_hold_a_wider_grant_than_its_creator(
    workspace: dict[str, Any],
) -> None:
    root = _create(
        workspace,
        "narrow-root",
        "--create-roles",
        "auto",
        "--open-links",
        "deny",
        "--turnover-budget",
        "8",
    )
    root_id = root["entity_ref"]["id"]
    session = _run_as(workspace, root_id, "widen")
    _refused(
        _invoke(
            workspace["runner"],
            workspace["repo"],
            session,
            "agent",
            "create",
            "--name",
            "wider",
            "--profile",
            "claude-builder",
            "--rationale",
            "wants more",
            "--create-roles",
            "ask",
            "--open-links",
            "auto",
            "--turnover-budget",
            "4",
            "--created-by-agent",
            root_id,
            "--idempotency-key",
            "wider",
        ),
        "wider open_links grant",
    )


@pytest.mark.skip(reason=_AUTOMATIC_LEVEL_WITHHELD)
def test_a_created_role_cannot_hold_a_wider_provider_profile(
    workspace: dict[str, Any],
) -> None:
    root = _json(
        _invoke(
            workspace["runner"],
            workspace["repo"],
            workspace["human"]["session_id"],
            "agent",
            "create",
            "--name",
            "reviewer-root",
            "--profile",
            "claude-independent-reviewer",
            "--rationale",
            "reviews only",
            "--create-roles",
            "auto",
            "--turnover-budget",
            "8",
            "--idempotency-key",
            "reviewer-root",
        )
    )
    root_id = root["entity_ref"]["id"]
    manager: CommonsManager = workspace["manager"]
    task = manager.create_task(
        title="reviewer work",
        description="target",
        acceptance_criteria=("bound",),
        idempotency_key="reviewer-task",
    )
    review = manager.request_review(
        target_ref={"kind": "task", "id": task["entity_ref"]["id"]},
        target_revision=task["revision"],
        criteria=("independent",),
        independent=True,
        idempotency_key="reviewer-review",
    )
    delegation = manager.create_delegation(
        target_ref={"kind": "review", "id": review["entity_ref"]["id"]},
        target_revision=review["revision"],
        target_profile="claude-independent-reviewer",
        purpose="independent_review",
        limits=LIMITS,
        on_behalf_of_agent_id=root_id,
        idempotency_key="reviewer-delegation",
    )
    session = _new_session(workspace, "reviewer-child")
    manager.start_delegation(
        delegation["entity_ref"]["id"],
        delegation["revision"],
        child_session_id=session,
        idempotency_key="reviewer-start",
    )
    _refused(
        _invoke(
            workspace["runner"],
            workspace["repo"],
            session,
            "agent",
            "create",
            "--name",
            "builder-child",
            "--profile",
            "claude-builder",
            "--rationale",
            "wants write access",
            "--turnover-budget",
            "1",
            "--created-by-agent",
            root_id,
            "--idempotency-key",
            "builder-child",
        ),
        "wider provider profile",
    )


# -- guarantee 1: the turnover ceiling counts both directions ----------------


@pytest.mark.skip(reason=_AUTOMATIC_LEVEL_WITHHELD)
def test_the_turnover_budget_counts_creations_and_retirements_together(
    workspace: dict[str, Any],
) -> None:
    root = _create(
        workspace,
        "budget-root",
        "--create-roles",
        "auto",
        "--retire-roles",
        "auto",
        "--turnover-budget",
        "3",
    )
    root_id = root["entity_ref"]["id"]
    session = _run_as(workspace, root_id, "budget")

    first = _create(
        workspace,
        "budget-child-1",
        "--create-roles",
        "ask",
        "--retire-roles",
        "ask",
        "--turnover-budget",
        "1",
        "--created-by-agent",
        root_id,
        session_id=session,
    )
    retired = _json(
        _invoke(
            workspace["runner"],
            workspace["repo"],
            session,
            "agent",
            "retire",
            first["entity_ref"]["id"],
            "--reason",
            "scope changed",
            "--idempotency-key",
            "budget-retire-1",
        )
    )
    assert retired["count"] == 1

    # One create plus one retire is two units of a three-unit budget; a second
    # create is the third, and the fourth step has to fail.
    _create(
        workspace,
        "budget-child-2",
        "--turnover-budget",
        "1",
        "--created-by-agent",
        root_id,
        session_id=session,
    )
    _refused(
        _invoke(
            workspace["runner"],
            workspace["repo"],
            session,
            "agent",
            "create",
            "--name",
            "budget-child-3",
            "--profile",
            "claude-builder",
            "--rationale",
            "one too many",
            "--turnover-budget",
            "1",
            "--created-by-agent",
            root_id,
            "--idempotency-key",
            "budget-child-3",
        ),
        "turnover budget is exhausted",
    )


# -- guarantee 7: a downgrade reaches work already running -------------------


@pytest.mark.skip(reason=_AUTOMATIC_LEVEL_WITHHELD)
def test_lowering_an_ancestor_grant_stops_a_running_descendant_immediately(
    workspace: dict[str, Any],
) -> None:
    root = _create(
        workspace,
        "downgrade-root",
        "--create-roles",
        "auto",
        "--turnover-budget",
        "8",
    )
    root_id = root["entity_ref"]["id"]
    session = _run_as(workspace, root_id, "downgrade")
    _create(
        workspace,
        "downgrade-child-1",
        "--create-roles",
        "ask",
        "--turnover-budget",
        "4",
        "--created-by-agent",
        root_id,
        session_id=session,
    )

    current = _json(
        _invoke(
            workspace["runner"],
            workspace["repo"],
            workspace["human"]["session_id"],
            "agent",
            "show",
            root_id,
        )
    )
    _json(
        _invoke(
            workspace["runner"],
            workspace["repo"],
            workspace["human"]["session_id"],
            "agent",
            "reconfigure",
            root_id,
            current["revision"],
            "--changes-json",
            json.dumps(
                {
                    "grants": {
                        "create_roles": "deny",
                        "retire_roles": "deny",
                        "open_links": "deny",
                    }
                }
            ),
            "--reason",
            "the org stopped growing",
            "--idempotency-key",
            "downgrade",
        )
    )

    # The delegation opened before the downgrade is still live.
    _refused(
        _invoke(
            workspace["runner"],
            workspace["repo"],
            session,
            "agent",
            "create",
            "--name",
            "downgrade-child-2",
            "--profile",
            "claude-builder",
            "--rationale",
            "after the downgrade",
            "--turnover-budget",
            "1",
            "--created-by-agent",
            root_id,
            "--idempotency-key",
            "downgrade-child-2",
        ),
        "may not create roles",
    )


# -- guarantee 5 and the retirement invariants -------------------------------


@pytest.mark.skip(reason=_AUTOMATIC_LEVEL_WITHHELD)
def test_a_cascade_retires_a_whole_lineage_in_one_command(
    workspace: dict[str, Any],
) -> None:
    root = _create(
        workspace,
        "cascade-root",
        "--create-roles",
        "auto",
        "--turnover-budget",
        "16",
    )
    root_id = root["entity_ref"]["id"]
    session = _run_as(workspace, root_id, "cascade")
    child = _create(
        workspace,
        "cascade-child",
        "--create-roles",
        "ask",
        "--turnover-budget",
        "4",
        "--created-by-agent",
        root_id,
        session_id=session,
    )
    # The third generation goes through the proposal flow, because a role at
    # `ask` cannot record one itself and a confirmation must point at what was
    # actually proposed.
    child_session = _run_as(workspace, child["entity_ref"]["id"], "cascade-child-run")
    proposed = _json(
        _invoke(
            workspace["runner"],
            workspace["repo"],
            child_session,
            "agent",
            "propose",
            "--name",
            "cascade-grandchild",
            "--profile",
            "claude-builder",
            "--rationale",
            "the third generation asks rather than records",
            "--idempotency-key",
            "cascade-grandchild-proposal",
        )
    )
    _json(
        _invoke(
            workspace["runner"],
            workspace["repo"],
            workspace["human"]["session_id"],
            "agent",
            "approve",
            proposed["entity_ref"]["id"],
            "--idempotency-key",
            "cascade-grandchild-approve",
        )
    )
    _finish_run(workspace, "cascade-child-run")

    # A live run blocks retirement whoever asks, so the cascade only becomes
    # possible once the work it owes is over.
    _finish_run(workspace, "cascade")
    retired = _json(
        _invoke(
            workspace["runner"],
            workspace["repo"],
            workspace["human"]["session_id"],
            "agent",
            "retire",
            root_id,
            "--cascade",
            "--reason",
            "the programme ended",
            "--idempotency-key",
            "cascade-retire",
        )
    )
    assert retired["count"] == 3
    remaining = _json(
        _invoke(
            workspace["runner"],
            workspace["repo"],
            workspace["human"]["session_id"],
            "agent",
            "list",
        )
    )
    assert [item["id"] for item in remaining] == []


def test_a_role_owing_a_live_delegation_cannot_be_retired_by_anyone(
    workspace: dict[str, Any],
) -> None:
    role = _create(workspace, "busy-role")
    role_id = role["entity_ref"]["id"]
    _run_as(workspace, role_id, "busy")
    _refused(
        _invoke(
            workspace["runner"],
            workspace["repo"],
            workspace["human"]["session_id"],
            "agent",
            "retire",
            role_id,
            "--reason",
            "no longer needed",
            "--idempotency-key",
            "busy-retire",
        ),
        "owing live work",
    )


def test_a_role_never_retires_a_human_created_role(workspace: dict[str, Any]) -> None:
    root = _create(
        workspace,
        "polite-root",
        "--retire-roles",
        "auto",
        "--turnover-budget",
        "8",
    )
    peer = _create(workspace, "human-peer")
    session = _run_as(workspace, root["entity_ref"]["id"], "polite")
    _refused(
        _invoke(
            workspace["runner"],
            workspace["repo"],
            session,
            "agent",
            "retire",
            peer["entity_ref"]["id"],
            "--reason",
            "tidying up",
            "--idempotency-key",
            "polite-retire",
        ),
        "never retires a human-created role",
    )


def test_a_task_scoped_role_leaves_service_when_its_task_is_cancelled(
    workspace: dict[str, Any],
) -> None:
    manager: CommonsManager = workspace["manager"]
    task = manager.create_task(
        title="short-lived work",
        description="the role exists only for this",
        acceptance_criteria=("done",),
        idempotency_key="ephemeral-task",
    )
    task_id = task["entity_ref"]["id"]
    created = _create(workspace, "ephemeral", "--retire-with-task", task_id)

    listed = _json(
        _invoke(
            workspace["runner"],
            workspace["repo"],
            workspace["human"]["session_id"],
            "agent",
            "list",
        )
    )
    assert created["entity_ref"]["id"] in [item["id"] for item in listed]

    cancelled = manager.cancel_task(task_id, task["revision"], reason="descoped")
    after = _json(
        _invoke(
            workspace["runner"],
            workspace["repo"],
            workspace["human"]["session_id"],
            "agent",
            "show",
            created["entity_ref"]["id"],
        )
    )
    assert after["state"] == "retired"
    assert after["retired_by"] == "lifetime"

    # Retirement is terminal: reopening the task must not bring the role back.
    # A post-pass over final task state used to do exactly that, because a
    # reopened task is active again (H3, 2026-08-10 review).
    manager.reopen_task(task_id, cancelled["revision"], reason="back in scope")
    reopened = _json(
        _invoke(
            workspace["runner"],
            workspace["repo"],
            workspace["human"]["session_id"],
            "agent",
            "show",
            created["entity_ref"]["id"],
        )
    )
    assert reopened["state"] == "retired"
    assert reopened["retired_by"] == "lifetime"


# -- context isolation -------------------------------------------------------


def test_weakening_context_isolation_is_refused_without_a_recorded_downgrade(
    workspace: dict[str, Any],
) -> None:
    created = _create(workspace, "isolated", "--context-mode", "fresh")
    _refused(
        _invoke(
            workspace["runner"],
            workspace["repo"],
            workspace["human"]["session_id"],
            "agent",
            "reconfigure",
            created["entity_ref"]["id"],
            created["revision"],
            "--changes-json",
            json.dumps({"context_mode": "accumulated"}),
            "--reason",
            "a small optimisation",
            "--idempotency-key",
            "weaken",
        ),
        "isolation_downgrade",
    )


def test_strengthening_context_isolation_needs_no_ceremony(
    workspace: dict[str, Any],
) -> None:
    created = _create(workspace, "loose", "--context-mode", "accumulated")
    result = _json(
        _invoke(
            workspace["runner"],
            workspace["repo"],
            workspace["human"]["session_id"],
            "agent",
            "reconfigure",
            created["entity_ref"]["id"],
            created["revision"],
            "--changes-json",
            json.dumps({"context_mode": "fresh"}),
            "--reason",
            "reviews must start clean",
            "--idempotency-key",
            "tighten",
        )
    )
    assert result["event_type"] == "agent.reconfigured"


# -- links -------------------------------------------------------------------


def test_a_link_records_the_action_it_permits_rather_than_an_open_flag(
    workspace: dict[str, Any],
) -> None:
    left = _create(workspace, "link-left")
    right = _create(workspace, "link-right")
    opened = _json(
        _invoke(
            workspace["runner"],
            workspace["repo"],
            workspace["human"]["session_id"],
            "agent",
            "link",
            "--from-agent",
            left["entity_ref"]["id"],
            "--to-agent",
            right["entity_ref"]["id"],
            "--deadline-seconds",
            "600",
            "--reason",
            "one bounded question",
            "--idempotency-key",
            "link-open",
        )
    )
    assert opened["entity_ref"]["kind"] == "agent_link"
    link = _json(
        _invoke(
            workspace["runner"],
            workspace["repo"],
            workspace["human"]["session_id"],
            "agent",
            "unlink",
            opened["entity_ref"]["id"],
            opened["revision"],
            "--reason",
            "answered",
            "--idempotency-key",
            "link-close",
        )
    )
    assert link["event_type"] == "agent.link_closed"


def test_a_retired_role_cannot_take_new_work(workspace: dict[str, Any]) -> None:
    manager: CommonsManager = workspace["manager"]
    created = _create(workspace, "gone")
    agent_id = created["entity_ref"]["id"]
    _json(
        _invoke(
            workspace["runner"],
            workspace["repo"],
            workspace["human"]["session_id"],
            "agent",
            "retire",
            agent_id,
            "--reason",
            "team disbanded",
            "--idempotency-key",
            "gone-retire",
        )
    )
    task = manager.create_task(
        title="late work",
        description="arrives after retirement",
        acceptance_criteria=("none",),
        idempotency_key="late-task",
    )
    with pytest.raises(Exception, match="retired role cannot take new work"):
        manager.create_delegation(
            target_ref={"kind": "task", "id": task["entity_ref"]["id"]},
            target_revision=task["revision"],
            target_profile="claude-builder",
            purpose="implementation",
            limits=LIMITS,
            on_behalf_of_agent_id=agent_id,
            idempotency_key="late-delegation",
        )


# -- the binding survives the run (C1) ----------------------------------------


def test_a_worker_that_reported_success_is_still_its_role(
    workspace: dict[str, Any],
) -> None:
    """The happy-path escape from the 2026-08-10 review, closed.

    A worker records its own `delegation.succeeded` and its process keeps
    running until the parent reaps it.  In that gap it used to become an
    unbound human window: role creation succeeded, and the ledger recorded the
    result as human-created.  The binding now survives terminalization, so the
    same refusals apply after success as before it.
    """

    manager: CommonsManager = workspace["manager"]
    created = _create(workspace, "sticky-role")
    agent_id = created["entity_ref"]["id"]
    child_session_id = _run_as(workspace, agent_id, "sticky-run")
    started = workspace["runs"]["sticky-run"]

    refusal = _invoke(
        workspace["runner"],
        workspace["repo"],
        child_session_id,
        "agent",
        "create",
        "--name",
        "helper while bound",
        "--profile",
        "claude-builder",
        "--rationale",
        "a deny-all role hires while bound",
        "--idempotency-key",
        "sticky-bound-create",
    )
    _refused(refusal, "may not create roles")

    delegation = manager.get_delegation(started["entity_ref"]["id"])
    succeeded = _invoke(
        workspace["runner"],
        workspace["repo"],
        child_session_id,
        "delegation",
        "succeed",
        started["entity_ref"]["id"],
        str(delegation["revision"]),
        "--summary",
        "done",
        "--result-ref",
        "task:" + str((delegation["target_ref"] or {}).get("id")),
        "--idempotency-key",
        "sticky-succeed",
    )
    _json(succeeded)

    # Still the role: creating under a wider grant refuses exactly as while
    # bound, instead of landing as an all-auto human-created record.
    after = _invoke(
        workspace["runner"],
        workspace["repo"],
        child_session_id,
        "agent",
        "create",
        "--name",
        "helper after success",
        "--profile",
        "claude-builder",
        "--rationale",
        "the happy-path escape",
        "--create-roles",
        "auto",
        "--turnover-budget",
        "8",
        "--idempotency-key",
        "sticky-after-create",
    )
    _refused(after, "may not create roles")

    # And it cannot commission fresh work as if it were a person either.
    task = manager.create_task(
        title="post-run work",
        description="a spent worker tries to open a new root delegation",
        acceptance_criteria=("none",),
        idempotency_key="sticky-late-task",
    )
    escape = _invoke(
        workspace["runner"],
        workspace["repo"],
        child_session_id,
        "delegation",
        "create",
        "--target-ref",
        "task:" + task["entity_ref"]["id"],
        "--target-revision",
        task["revision"],
        "--target-profile",
        "claude-builder",
        "--purpose",
        "implementation",
        "--limits-json",
        json.dumps(LIMITS),
        "--idempotency-key",
        "sticky-escape-delegation",
    )
    _refused(escape, "cannot escape its lineage")


# -- the second path re-checks what creation checks (C2, H1) -------------------


def _shown_event(workspace: dict[str, Any], event_id: str) -> dict[str, Any]:
    return workspace["manager"].show_event(event_id)


def test_a_correction_cannot_widen_a_roles_grants_or_isolation(
    workspace: dict[str, Any],
) -> None:
    """C2 through the seam: a correction fixes a typo, it is not a back door
    around the reconfiguration gates.  The authority, budget, and isolation of a
    role are frozen against correction on the write path and on replay."""

    created = _create(workspace, "correctable")
    shown = _shown_event(workspace, created["event_id"])
    widened = {
        **shown["event"]["payload"],
        "grants": {"create_roles": "auto", "retire_roles": "auto", "open_links": "auto"},
        "turnover_budget": 1024,
        "context_mode": "accumulated",
    }
    refusal = _invoke(
        workspace["runner"],
        workspace["repo"],
        workspace["human"]["session_id"],
        "event",
        "correct",
        created["event_id"],
        "--expected-target-sha256",
        shown["canonical_sha256"],
        "--replacement-payload-json",
        json.dumps(widened),
        "--idempotency-key",
        "correctable-widen",
    )
    _refused(refusal, "grants")

    # A descriptive typo is still correctable: name and rationale carry no
    # authority, so freezing the governance fields does not freeze the record.
    fixed = {**shown["event"]["payload"], "rationale": "corrected rationale text"}
    accepted = _invoke(
        workspace["runner"],
        workspace["repo"],
        workspace["human"]["session_id"],
        "event",
        "correct",
        created["event_id"],
        "--expected-target-sha256",
        shown["canonical_sha256"],
        "--replacement-payload-json",
        json.dumps(fixed),
        "--idempotency-key",
        "correctable-typo",
    )
    _json(accepted)


def test_reconfiguration_re_checks_the_turnover_budget(
    workspace: dict[str, Any],
) -> None:
    """H1 through the seam: granting create_roles by reconfigure demands a
    ceiling, exactly as creation does, and the ceiling is now settable so the
    operator remedy exists."""

    created = _create(workspace, "widen-me")
    agent_id = created["entity_ref"]["id"]

    refusal = _invoke(
        workspace["runner"],
        workspace["repo"],
        workspace["human"]["session_id"],
        "agent",
        "reconfigure",
        agent_id,
        created["revision"],
        "--changes-json",
        json.dumps(
            {"grants": {"create_roles": "ask", "retire_roles": "deny", "open_links": "deny"}}
        ),
        "--reason",
        "grant creation with no ceiling",
        "--idempotency-key",
        "widen-nobudget",
    )
    _refused(refusal, "turnover_budget")

    # The remedy the review said was unreachable: set the grant and its ceiling
    # together.  turnover_budget is mutable now, so this is expressible.
    accepted = _json(
        _invoke(
            workspace["runner"],
            workspace["repo"],
            workspace["human"]["session_id"],
            "agent",
            "reconfigure",
            agent_id,
            created["revision"],
            "--changes-json",
            json.dumps(
                {
                    "grants": {"create_roles": "ask", "retire_roles": "deny", "open_links": "deny"},
                    "turnover_budget": 4,
                }
            ),
            "--reason",
            "grant creation with a ceiling",
            "--idempotency-key",
            "widen-withbudget",
        )
    )
    shown = _json(
        _invoke(
            workspace["runner"],
            workspace["repo"],
            workspace["human"]["session_id"],
            "agent",
            "show",
            agent_id,
        )
    )
    assert shown["effective_grants"]["create_roles"] == "ask"
    assert shown["turnover_budget"] == 4
    assert accepted["event_type"] == "agent.reconfigured"


# -- cascade retirement is atomic and leaves-first (M6) ------------------------


def _lineage_via_proposals(workspace: dict[str, Any]) -> dict[str, str]:
    """Build root -> mid -> leaf without the withheld automatic level.

    Each agent-created role comes about the way one really does now: a session
    running as a role proposes it, and the operator confirms.  Returns the three
    role ids and ends every run so the cascade is not blocked by live work.
    """

    manager: CommonsManager = workspace["manager"]
    root = _json(
        _invoke(
            workspace["runner"],
            workspace["repo"],
            workspace["human"]["session_id"],
            "agent",
            "create",
            "--name",
            "root",
            "--profile",
            "claude-builder",
            "--rationale",
            "top of the lineage",
            "--create-roles",
            "ask",
            "--turnover-budget",
            "8",
            "--idempotency-key",
            "cascade-root",
        )
    )
    root_id = root["entity_ref"]["id"]

    def propose_and_confirm(parent_id: str, name: str, grant: str, key: str) -> str:
        worker_session = _run_as(workspace, parent_id, f"{key}-run")
        worker = CommonsManager(
            workspace["repo"],
            session_id=worker_session,
            state_root=manager.paths.state_root,
        )
        proposal = worker.propose_agent(
            name=name,
            profile_id="claude-builder",
            rationale=f"{name} under {parent_id}",
            grants={"create_roles": grant, "retire_roles": "deny", "open_links": "deny"},
            turnover_budget=4 if grant != "deny" else None,
            idempotency_key=f"{key}-proposal",
        )
        confirmed = _json(
            _invoke(
                workspace["runner"],
                workspace["repo"],
                workspace["human"]["session_id"],
                "agent",
                "approve",
                proposal["entity_ref"]["id"],
                "--idempotency-key",
                f"{key}-approve",
            )
        )
        _finish_run(workspace, f"{key}-run")
        return confirmed["entity_ref"]["id"]

    mid_id = propose_and_confirm(root_id, "mid", "ask", "cascade-mid")
    leaf_id = propose_and_confirm(mid_id, "leaf", "deny", "cascade-leaf")
    return {"root": root_id, "mid": mid_id, "leaf": leaf_id}


def test_a_cascade_retires_leaves_first_and_as_a_whole(workspace: dict[str, Any]) -> None:
    lineage = _lineage_via_proposals(workspace)

    retired = _json(
        _invoke(
            workspace["runner"],
            workspace["repo"],
            workspace["human"]["session_id"],
            "agent",
            "retire",
            lineage["root"],
            "--reason",
            "programme wound down",
            "--cascade",
            "--idempotency-key",
            "cascade-retire",
        )
    )
    order = [item["entity_ref"]["id"] for item in retired["retired"]]
    # A role is always written before the creator it collapses, so a partial
    # cascade never leaves a child active under a retired parent.
    assert order == [lineage["leaf"], lineage["mid"], lineage["root"]]

    manager: CommonsManager = workspace["manager"]
    snapshot = manager.snapshot()
    for identifier in lineage.values():
        assert snapshot.agents[identifier]["state"] == "retired"
    assert snapshot.agents[lineage["root"]]["retired_by"] == "human"
    assert snapshot.agents[lineage["mid"]]["retired_by"] == "cascade"
    assert snapshot.agents[lineage["leaf"]]["retired_by"] == "cascade"
