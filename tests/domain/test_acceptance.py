from __future__ import annotations

from agent_commons.domain.acceptance import select_qualifying_review
from agent_commons.domain.snapshot import ProjectSnapshot


def review(identifier: str, **changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": identifier,
        "state": "approved",
        "independent": True,
        "stale": False,
        "target_ref": {"kind": "task", "id": "task.1"},
        "target_revision": "evt.current",
        "actor": {"session_id": "session.reviewer"},
        "recorded_at": "2026-08-19T00:00:00Z",
    }
    value.update(changes)
    return value


def test_select_qualifying_review_excludes_stale_dependent_and_author_reviews() -> None:
    snapshot = ProjectSnapshot(
        tasks={
            "task.1": {
                "id": "task.1",
                "revision": "evt.stored",
                "effective_revision": "evt.current",
                "work_author_session_ids": ["session.author"],
            }
        },
        reviews={
            "review.stale": review("review.stale", stale=True),
            "review.dependent": review("review.dependent", independent=False),
            "review.author": review(
                "review.author",
                actor={"session_id": "session.author"},
            ),
            "review.old-target": review(
                "review.old-target",
                target_revision="evt.stored",
            ),
            "review.approved": review("review.approved"),
        },
    )

    selected = select_qualifying_review(snapshot, "task.1")

    assert selected is snapshot.reviews["review.approved"]


def test_select_qualifying_review_uses_recorded_time_then_id() -> None:
    snapshot = ProjectSnapshot(
        tasks={"task.1": {"id": "task.1", "revision": "evt.current"}},
        reviews={
            "review.a": review("review.a"),
            "review.b": review("review.b"),
            "review.older": review(
                "review.older",
                recorded_at="2026-08-18T23:59:59Z",
            ),
        },
    )

    assert select_qualifying_review(snapshot, "task.1") is snapshot.reviews["review.b"]
    assert select_qualifying_review(snapshot, "task.missing") is None


def test_select_qualifying_review_orders_mixed_precision_timestamps_as_instants() -> None:
    snapshot = ProjectSnapshot(
        tasks={"task.1": {"id": "task.1", "revision": "evt.current"}},
        reviews={
            "review.whole-second": review(
                "review.whole-second",
                recorded_at="2026-08-19T00:00:00Z",
            ),
            "review.fractional-second": review(
                "review.fractional-second",
                recorded_at="2026-08-19T00:00:00.100000Z",
            ),
        },
    )

    assert (
        select_qualifying_review(snapshot, "task.1") is snapshot.reviews["review.fractional-second"]
    )
