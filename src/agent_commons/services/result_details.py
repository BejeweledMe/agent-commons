"""Bounded human-readable task evidence pages from one exact task revision."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from agent_commons.core.ids import is_typed_id
from agent_commons.domain.snapshot import ProjectSnapshot
from agent_commons.services.outputs import OutputReadRefusal, unavailable
from agent_commons.services.text_results import TEXT_KIND, validate_report


def task_result_details(
    manager: Any, task_id: str, task_revision: str, *, offset: int = 0, limit: int = 32
) -> dict[str, Any]:
    if (
        not is_typed_id(task_id, "task")
        or not is_typed_id(task_revision, "evt")
        or type(offset) is not int
        or offset < 0
        or type(limit) is not int
        or not 1 <= limit <= 64
    ):
        raise OutputReadRefusal("results_invalid_page", 400, "Result page is invalid.")
    snapshot = manager.snapshot()
    if not isinstance(snapshot, ProjectSnapshot) or any(
        issue.severity == "error" for issue in snapshot.issues
    ):
        raise unavailable()
    task = snapshot.tasks.get(task_id)
    if task is None:
        raise OutputReadRefusal("results_task_not_found", 404, "Task was not found.")
    if snapshot.entity_revision("task", task_id) != task_revision:
        raise OutputReadRefusal(
            "results_task_revision_changed", 409, "Refresh the result after the task changed."
        )
    bindings = task.get("artifact_bindings") or []
    items = []
    for binding in bindings[offset : offset + limit]:
        ref, revision = binding["ref"], binding["revision"]
        value = {
            "ref": dict(ref),
            "revision": revision,
            "kind": "artifact",
            "title": "Evidence unavailable",
            "summary": None,
            "checks": [],
            "media_type": None,
            "content_revision": None,
            "retained": False,
            "stale": True,
        }
        artifact = snapshot.artifacts.get(ref["id"]) if ref["kind"] == "artifact" else None
        if (
            artifact is not None
            and artifact.get("classification") in {"internal", "public"}
            and snapshot.entity_revision("artifact", ref["id"]) == revision
            and artifact.get("manifest_ref") in snapshot.known_manifest_ids
        ):
            manifest = manager.manifests.get(artifact["manifest_ref"]).manifest
            if (
                manifest.get("artifact_id") == ref["id"]
                and manifest.get("revision") == artifact.get("content_revision")
                and manifest.get("classification") == artifact.get("classification")
            ):
                metadata = manifest.get("metadata") or {}
                if metadata.get("output_kind") == TEXT_KIND:
                    validate_report(
                        metadata.get("title"), metadata.get("summary"), metadata.get("checks")
                    )
                    value.update(
                        kind="text_result",
                        title=metadata["title"],
                        summary=metadata["summary"],
                        checks=metadata["checks"],
                    )
                else:
                    source_name = PurePosixPath(
                        str((manifest.get("source") or {}).get("path", ""))
                    ).name
                    title = metadata.get("title")
                    value["title"] = (
                        title if isinstance(title, str) and title else source_name or "Artifact"
                    )
                    if isinstance(metadata.get("summary"), str):
                        value["summary"] = metadata["summary"]
                    checks = metadata.get("checks")
                    if isinstance(checks, list) and all(isinstance(check, str) for check in checks):
                        value["checks"] = checks
                value.update(
                    media_type=manifest.get("media_type"),
                    content_revision=manifest.get("revision"),
                    retained=metadata.get("retained_content")
                    == {
                        "revision": manifest.get("revision"),
                        "size_bytes": manifest.get("size_bytes"),
                    },
                    stale=False,
                )
        items.append(value)
    result = {
        "schema": "agent_commons.task-results.v1",
        "task_id": task_id,
        "task_revision": task_revision,
        "title": task.get("title"),
        "description": task["description"],
        "acceptance_criteria": task["acceptance_criteria"],
        "summary": task.get("summary"),
        "evidence": items,
        "total": len(bindings),
        "offset": offset,
        "limit": limit,
        "next_offset": offset + limit if offset + limit < len(bindings) else None,
    }
    # No fields are silently shortened. An oversized legacy record refuses the
    # page. Smaller pages can reduce evidence size, but task text stays whole.
    from agent_commons.core.canonical import canonical_json_bytes

    if len(canonical_json_bytes(result)) > 512 * 1024:
        raise OutputReadRefusal("results_bounds_exceeded", 409, "Result page exceeds its bounds.")
    manager.policy.assert_safe(result, context="task result details")
    return result
