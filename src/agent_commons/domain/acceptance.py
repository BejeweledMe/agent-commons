"""Pure task-acceptance review selection."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .chronology import chronological_key
from .snapshot import ProjectSnapshot


def select_qualifying_review(
    snapshot: ProjectSnapshot,
    task_id: str,
) -> Mapping[str, Any] | None:
    """Return the newest current independent approval by a non-author."""

    task = snapshot.tasks.get(task_id)
    if task is None:
        return None
    target_revision = str(task.get("effective_revision") or task.get("revision"))
    work_author_sessions = {
        str(session_id) for session_id in task.get("work_author_session_ids", []) if str(session_id)
    }
    qualifying = sorted(
        (
            review
            for review in snapshot.reviews.values()
            if review.get("state") == "approved"
            and review.get("independent") is True
            and review.get("stale") is False
            and review.get("target_ref") == {"kind": "task", "id": task_id}
            and review.get("target_revision") == target_revision
            and str((review.get("actor") or {}).get("session_id", "")) not in work_author_sessions
        ),
        key=lambda review: chronological_key(review.get("recorded_at"), review.get("id")),
    )
    return qualifying[-1] if qualifying else None
