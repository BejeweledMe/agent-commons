from __future__ import annotations

import json

import pytest

from agent_commons.domain.projection import ProjectSnapshot
from agent_commons.errors import IntegrityError
from agent_commons.views import orientation, render_views


def test_render_views_rejects_symlink_destination_and_target(tmp_path) -> None:
    snapshot = ProjectSnapshot()
    outside = tmp_path / "outside"
    outside.mkdir()
    linked_root = tmp_path / "linked-views"
    linked_root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(IntegrityError, match="must not contain symlinks"):
        render_views(snapshot, linked_root)
    assert list(outside.iterdir()) == []

    root = tmp_path / "views"
    root.mkdir()
    protected = outside / "protected.md"
    protected.write_text("do not overwrite\n", encoding="utf-8")
    (root / "CURRENT.md").symlink_to(protected)
    with pytest.raises(IntegrityError, match="must not be a symlink"):
        render_views(snapshot, root)
    assert protected.read_text(encoding="utf-8") == "do not overwrite\n"


def test_render_views_collapses_and_escapes_untrusted_markdown(tmp_path) -> None:
    objective_id = "objective.00000000000000000000000001"
    snapshot = ProjectSnapshot(
        objectives={
            objective_id: {
                "id": objective_id,
                "state": "active",
                "title": "Safe line\n# injected <script>&raw</script>",
            }
        },
        warnings=["warning\n## injected <!-- raw --> &"],
    )

    render_views(snapshot, tmp_path / "views")
    current = (tmp_path / "views" / "CURRENT.md").read_text(encoding="utf-8")
    risks = (tmp_path / "views" / "KNOWN_RISKS.md").read_text(encoding="utf-8")

    assert "\n# injected" not in current
    assert "&lt;script&gt;&amp;raw&lt;/script&gt;" in current
    assert "\n## injected" not in risks
    assert "&lt;!-- raw --&gt; &amp;" in risks
    assert not list((tmp_path / "views").glob(".*.tmp"))


def test_orientation_bounds_nested_messages_without_mutating_snapshot() -> None:
    thread_id = "thread.00000000000000000000000001"
    huge_body = "🙂" * 20_000
    messages = [{"message_id": f"message.{index:026d}", "body": huge_body} for index in range(200)]
    accepted_task_id = "task.00000000000000000000000001"
    snapshot = ProjectSnapshot(
        tasks={
            accepted_task_id: {
                "id": accepted_task_id,
                "state": "accepted",
                "title": "Accepted work",
            }
        },
        threads={
            thread_id: {
                "id": thread_id,
                "state": "open",
                "to": ["*"],
                "subject": huge_body,
                "messages": messages,
            }
        },
    )

    result = orientation(
        snapshot,
        session={"session_id": "session.viewer", "role_id": "reviewer"},
        max_items=20,
        max_text_bytes=256,
        max_nested_items=8,
        max_total_bytes=8192,
    )
    encoded = json.dumps(result, ensure_ascii=False).encode("utf-8")

    assert len(encoded) < 16_384
    assert "[truncated" in encoded.decode("utf-8")
    assert result["work"]["accepted"][0]["id"] == accepted_task_id
    assert len(snapshot.threads[thread_id]["messages"]) == 200
    assert snapshot.threads[thread_id]["messages"][0]["body"] == huge_body


def test_current_view_excludes_stale_effective_truth_but_keeps_fresh_truth(tmp_path) -> None:
    stale_decision = "decision.00000000000000000000000001"
    fresh_decision = "decision.00000000000000000000000002"
    stale_finding = "finding.00000000000000000000000001"
    fresh_finding = "finding.00000000000000000000000002"
    snapshot = ProjectSnapshot(
        decisions={
            stale_decision: {
                "id": stale_decision,
                "state": "accepted",
                "proposal": "stale decision",
                "stale": True,
            },
            fresh_decision: {
                "id": fresh_decision,
                "state": "accepted",
                "proposal": "fresh decision",
                "stale": False,
            },
        },
        findings={
            stale_finding: {
                "id": stale_finding,
                "state": "verified",
                "summary": "stale finding",
                "stale": True,
            },
            fresh_finding: {
                "id": fresh_finding,
                "state": "verified",
                "summary": "fresh finding",
                "stale": False,
            },
        },
    )

    render_views(snapshot, tmp_path / "views")
    current = (tmp_path / "views" / "CURRENT.md").read_text(encoding="utf-8")

    assert stale_decision not in current
    assert stale_finding not in current
    assert fresh_decision in current
    assert fresh_finding in current
