"""The graph is derived from fields that exist; absent relations stay absent."""

from __future__ import annotations

from typing import Any

from agent_commons.domain.projection import ProjectSnapshot
from agent_commons.ui.graph import MAX_NODES, build_graph

_COMMON = {
    "generated_at": "2026-08-09T00:00:00Z",
    "ledger_fingerprint": "sha256:0",
    "server_instance_id": "instance",
    "seq": 1,
}


def graph_of(snapshot: ProjectSnapshot, **kwargs: Any) -> dict[str, Any]:
    return build_graph(snapshot, **{**_COMMON, **kwargs})


def test_delegation_edges_follow_real_projection_fields() -> None:
    snapshot = ProjectSnapshot(
        workspace_id="workspace.1",
        tasks={"task.1": {"state": "active", "title": "Build"}},
        delegations={
            "delegation.parent": {"state": "succeeded", "target_profile": "claude-builder"},
            "delegation.child": {
                "state": "active",
                "target_profile": "codex-builder",
                "purpose": "implementation",
                "parent_delegation_id": "delegation.parent",
                "target_ref": {"kind": "task", "id": "task.1"},
                "target_revision": "evt.1",
                "parent_session_id": "session.a",
                "child_session_id": "session.b",
            },
        },
    )
    sessions = [{"session_id": "session.a", "state": "active"}, {"session_id": "session.b"}]
    graph = graph_of(snapshot, sessions=sessions)
    kinds = {(edge["kind"], edge["from"], edge["to"]) for edge in graph["edges"]}
    assert ("spawned", "delegation.parent", "delegation.child") in kinds
    assert ("targets", "delegation.child", "task.1") in kinds
    assert ("requested_by", "session.a", "delegation.child") in kinds
    assert ("runs_as", "delegation.child", "session.b") in kinds


def test_runs_as_edge_is_absent_before_the_child_session_exists() -> None:
    snapshot = ProjectSnapshot(
        delegations={
            "delegation.1": {
                "state": "requested",
                "target_profile": "claude-builder",
                "parent_session_id": "session.a",
            }
        }
    )
    graph = graph_of(snapshot, sessions=[{"session_id": "session.a"}])
    assert not [edge for edge in graph["edges"] if edge["kind"] == "runs_as"]


def test_objectives_never_gain_a_fabricated_task_edge() -> None:
    """The ledger has no objective->task relation.  Drawing a plausible one
    would be indistinguishable from a real edge once rendered."""

    snapshot = ProjectSnapshot(
        objectives={"objective.1": {"state": "active", "title": "Ship"}},
        tasks={"task.1": {"state": "ready", "title": "Do"}},
    )
    graph = graph_of(snapshot)
    assert graph["edges"] == []
    objective = next(node for node in graph["nodes"] if node["kind"] == "objective")
    assert objective["attrs"]["attached"] is False


def test_stale_evidence_marks_both_the_node_and_the_edge() -> None:
    snapshot = ProjectSnapshot(
        tasks={"task.1": {"state": "review", "title": "Do"}},
        reviews={
            "review.1": {
                "verdict": "approved",
                "stale": True,
                "target_ref": {"kind": "task", "id": "task.1"},
                "target_revision": "evt.1",
            }
        },
    )
    graph = graph_of(snapshot)
    review = next(node for node in graph["nodes"] if node["kind"] == "review")
    assert review["stale"] is True
    edge = next(edge for edge in graph["edges"] if edge["kind"] == "reviews")
    assert edge["attrs"]["stale"] is True


def test_task_dependencies_become_edges() -> None:
    snapshot = ProjectSnapshot(
        tasks={
            "task.1": {"state": "ready", "title": "A", "dependencies": ["task.2"]},
            "task.2": {"state": "accepted", "title": "B"},
        }
    )
    graph = graph_of(snapshot)
    assert ("depends_on", "task.1", "task.2") in {
        (edge["kind"], edge["from"], edge["to"]) for edge in graph["edges"]
    }


def test_graph_is_bounded_and_reports_truncation() -> None:
    snapshot = ProjectSnapshot(
        tasks={
            f"task.{index}": {"state": "accepted", "title": f"T{index}"}
            for index in range(MAX_NODES + 500)
        }
    )
    graph = graph_of(snapshot)
    assert len(graph["nodes"]) == MAX_NODES
    assert graph["limits"]["truncated"] is True


def test_no_edge_survives_without_both_endpoints() -> None:
    snapshot = ProjectSnapshot(
        tasks={
            f"task.{index}": {
                "state": "accepted",
                "title": "T",
                "dependencies": [f"task.{index + 1}"],
            }
            for index in range(MAX_NODES + 200)
        }
    )
    graph = graph_of(snapshot)
    identifiers = {node["id"] for node in graph["nodes"]}
    for edge in graph["edges"]:
        assert edge["from"] in identifiers
        assert edge["to"] in identifiers


def test_labels_are_bounded_and_stripped_of_control_characters() -> None:
    hostile = "a" + chr(0) + "b\r\n" + "x" * 500
    snapshot = ProjectSnapshot(tasks={"task.1": {"state": "ready", "title": hostile}})
    graph = graph_of(snapshot)
    label = graph["nodes"][0]["label"]
    assert chr(0) not in label
    assert "\r" not in label
    assert len(label.encode("utf-8")) <= 120


def test_delegation_label_uses_enumerated_fields_not_free_text() -> None:
    snapshot = ProjectSnapshot(
        delegations={
            "delegation.1": {
                "state": "active",
                "target_profile": "codex-builder",
                "purpose": "independent_review",
                "title": "attacker controlled",
            }
        }
    )
    graph = graph_of(snapshot)
    assert graph["nodes"][0]["label"] == "codex-builder · independent_review"


def test_counts_and_warnings_are_carried_through() -> None:
    snapshot = ProjectSnapshot(
        tasks={"task.1": {"state": "ready"}, "task.2": {"state": "ready"}},
        warnings=["review review.1 is stale for current target revision"],
    )
    graph = graph_of(snapshot)
    assert graph["counts"]["tasks"]["ready"] == 2
    assert len(graph["warnings"]) == 1


def test_a_real_session_renders_as_a_node(populated, context) -> None:  # type: ignore[no-untyped-def]
    """Regression: an empty workspace never exercised the session path, so a
    wrong attribute name on Session survived every other test."""

    graph = context.rebuild_graph()
    sessions = [node for node in graph["nodes"] if node["kind"] == "session"]
    assert sessions, "the session was not projected"
    assert sessions[0]["id"] == populated["session_id"]
    assert sessions[0]["state"] in {"active", "expired", "closed"}
    tasks = [node for node in graph["nodes"] if node["kind"] == "task"]
    assert any(node["id"] == populated["task_id"] for node in tasks)


def test_a_populated_workspace_serves_a_graph_over_http(populated, client) -> None:  # type: ignore[no-untyped-def]
    from tests.ui.conftest import authorized

    response = client.get("/api/graph", headers=authorized())
    assert response.status_code == 200
    payload = response.json()
    assert payload["nodes"], "the live endpoint returned an empty graph"
    kinds = {node["kind"] for node in payload["nodes"]}
    assert "session" in kinds
    assert "task" in kinds


def test_bands_are_a_chain_of_command_not_a_kind_grouping() -> None:
    """Grouping by record kind put every session in one row and showed no
    hierarchy at all.  A band is now distance from the operator, walked along
    real ledger links: an undelegated session answers to the human, a delegation
    sits under the session that requested it, and its child session under that.
    """

    snapshot = ProjectSnapshot(
        tasks={"task.1": {"state": "active", "title": "Work", "owner_session_id": "session.b"}},
        delegations={
            "delegation.1": {
                "state": "active",
                "target_profile": "claude-builder",
                "parent_session_id": "session.a",
                "child_session_id": "session.b",
            }
        },
    )
    sessions = [{"session_id": "session.a"}, {"session_id": "session.b"}]
    graph = graph_of(snapshot, sessions=sessions)
    bands = {node["id"]: node["band"] for node in graph["nodes"]}

    assert bands["session.a"] == 0, "nobody delegated to it, so it answers to the operator"
    assert bands["delegation.1"] == 1
    assert bands["session.b"] == 2, "the child session is one step further from the human"
    assert bands["task.1"] == 3, "work hangs off the session that owns it"

    top = {node["id"] for node in graph["nodes"] if node["reports_to_operator"]}
    assert top == {"session.a"}


def test_unowned_work_stays_at_the_top_where_it_is_visible() -> None:
    snapshot = ProjectSnapshot(tasks={"task.1": {"state": "ready", "title": "Nobody took it"}})
    graph = graph_of(snapshot)
    assert graph["nodes"][0]["band"] == 0


def test_edges_declare_whether_a_relationship_is_standing_or_one_off() -> None:
    """Reporting lines are standing structure; a review is one exchange bound to
    a revision.  The projection carries the distinction so the frontend does not
    have to infer it from a list of edge kinds."""

    snapshot = ProjectSnapshot(
        tasks={"task.1": {"state": "review", "title": "T", "owner_session_id": "session.a"}},
        reviews={
            "review.1": {
                "verdict": "approved",
                "target_ref": {"kind": "task", "id": "task.1"},
                "target_revision": "evt.1",
            }
        },
    )
    graph = graph_of(snapshot, sessions=[{"session_id": "session.a"}])
    relations = {edge["kind"]: edge["relation"] for edge in graph["edges"]}
    assert relations["owns"] == "permanent"
    assert relations["reviews"] == "temporary"
