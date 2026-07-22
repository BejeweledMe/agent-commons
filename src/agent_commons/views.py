from __future__ import annotations

import html
import os
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import Any

from agent_commons.domain.projection import ProjectSnapshot
from agent_commons.errors import IntegrityError

_TRUNCATION_MARKER = " …[truncated]"


def _truncate_utf8(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    marker = _TRUNCATION_MARKER.encode("utf-8")
    prefix_limit = max(0, max_bytes - len(marker))
    prefix = encoded[:prefix_limit].decode("utf-8", errors="ignore")
    return prefix + _TRUNCATION_MARKER


@dataclass
class _CopyBudget:
    remaining_bytes: int
    remaining_nodes: int
    max_text_bytes: int
    max_children: int
    max_depth: int


def _bounded_copy(value: Any, budget: _CopyBudget, *, depth: int = 0) -> Any:
    if budget.remaining_nodes <= 0 or depth > budget.max_depth:
        return _TRUNCATION_MARKER.strip()
    budget.remaining_nodes -= 1
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        limit = max(0, min(budget.max_text_bytes, budget.remaining_bytes))
        result = _truncate_utf8(value, limit)
        budget.remaining_bytes = max(0, budget.remaining_bytes - len(result.encode("utf-8")))
        return result
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        item_count = len(value)
        limit = item_count if depth == 0 else min(item_count, budget.max_children)
        for raw_key, child in islice(value.items(), limit):
            key = _truncate_utf8(str(raw_key), min(256, budget.max_text_bytes))
            output[key] = _bounded_copy(child, budget, depth=depth + 1)
        if item_count > limit:
            output["[truncated]"] = f"{item_count - limit} fields omitted"
        return output
    if isinstance(value, (list, tuple)):
        item_count = len(value)
        output = [
            _bounded_copy(child, budget, depth=depth + 1)
            for child in islice(value, budget.max_children)
        ]
        if item_count > budget.max_children:
            output.append(f"[truncated: {item_count - budget.max_children} items omitted]")
        return output
    return _bounded_copy(str(value), budget, depth=depth)


def orientation(
    snapshot: ProjectSnapshot,
    *,
    session: Mapping[str, Any] | None = None,
    claims: Iterable[Mapping[str, Any]] = (),
    max_items: int = 20,
    max_text_bytes: int = 4096,
    max_nested_items: int = 32,
    max_total_bytes: int = 131_072,
) -> dict[str, Any]:
    if min(max_items, max_text_bytes, max_nested_items, max_total_bytes) < 1:
        raise ValueError("orientation bounds must be positive")
    if max_text_bytes < len(_TRUNCATION_MARKER.encode("utf-8")):
        raise ValueError("max_text_bytes is too small for the truncation marker")
    role = str((session or {}).get("role_id", ""))
    session_id = str((session or {}).get("session_id", ""))
    addressed = {"*", role, session_id}
    objectives = list(
        islice(
            (item for item in snapshot.objectives.values() if item.get("state") == "active"),
            max_items,
        )
    )
    task_groups = {
        state: list(
            islice(
                (item for item in snapshot.tasks.values() if item.get("state") == state),
                max_items,
            )
        )
        for state in (
            "ready",
            "assigned",
            "active",
            "blocked",
            "completed",
            "review",
            "accepted",
        )
    }
    inbox_threads = list(
        islice(
            (
                item
                for item in snapshot.threads.values()
                if item.get("state") == "open" and addressed.intersection(set(item.get("to") or []))
            ),
            max_items,
        )
    )
    handoffs = list(
        islice(
            (
                item
                for item in snapshot.handoffs.values()
                if item.get("state") == "open" and addressed.intersection(set(item.get("to") or []))
            ),
            max_items,
        )
    )
    requested_reviews = list(
        islice(
            (
                item
                for item in snapshot.reviews.values()
                if item.get("state") == "requested" and item.get("stale") is not True
            ),
            max_items,
        )
    )
    stale_review_judgments = list(
        islice(
            (
                item
                for item in snapshot.reviews.values()
                if item.get("state") != "requested" and item.get("stale") is True
            ),
            max_items,
        )
    )
    delegation_groups = {
        state: list(
            islice(
                (item for item in snapshot.delegations.values() if item.get("state") == state),
                max_items,
            )
        )
        for state in (
            "requested",
            "active",
            "input_needed",
            "succeeded",
            "failed",
            "cancelled",
            "timed_out",
            "needs_operator",
        )
    }
    accepted_decisions = list(
        islice(
            (
                item
                for item in snapshot.decisions.values()
                if item.get("state") == "accepted" and item.get("stale") is not True
            ),
            max_items,
        )
    )
    verified_findings = list(
        islice(
            (
                item
                for item in snapshot.findings.values()
                if item.get("state") == "verified" and item.get("stale") is not True
            ),
            max_items,
        )
    )
    result = {
        "workspace_id": snapshot.workspace_id,
        "session": dict(session or {}),
        "objectives": objectives[:max_items],
        "work": task_groups,
        "pending_reviews": requested_reviews,
        "stale_review_judgments": stale_review_judgments,
        "delegations": delegation_groups,
        "inbox": inbox_threads,
        "handoffs": handoffs,
        "effective_truth": {
            "decisions": accepted_decisions,
            "findings": verified_findings,
        },
        "claims": list(islice(claims, max_items)),
        "warnings": sorted(set(snapshot.warnings))[:max_items],
    }
    bounded = _bounded_copy(
        result,
        _CopyBudget(
            remaining_bytes=max_total_bytes // 2,
            remaining_nodes=max(32, max_total_bytes // 64),
            max_text_bytes=max_text_bytes,
            max_children=max_nested_items,
            max_depth=8,
        ),
    )
    if not isinstance(bounded, dict):  # pragma: no cover - root is fixed above
        raise AssertionError("orientation root must remain an object")
    return bounded


def _markdown_inline(value: Any, *, max_bytes: int = 4096) -> str:
    collapsed = " ".join(str(value).split())
    escaped = html.escape(collapsed, quote=False).replace("`", "&#96;")
    return _truncate_utf8(escaped, max_bytes)


def _line_items(items: Iterable[Mapping[str, Any]], label: str) -> list[str]:
    lines: list[str] = []
    for item in items:
        identifier = _markdown_inline(
            item.get("id") or item.get("task_id") or item.get("decision_id") or "unknown",
            max_bytes=512,
        )
        title = (
            item.get("title")
            or item.get("subject")
            or item.get("summary")
            or item.get("proposal")
            or item.get("target_profile")
            or ""
        )
        title = _markdown_inline(title)
        state = _markdown_inline(item.get("state"), max_bytes=128) if item.get("state") else ""
        suffix = f" [{state}]" if state else ""
        lines.append(f"- `{identifier}`{suffix} — {title or label}")
    return lines or ["- None"]


def _reject_symlink_components(path: Path) -> None:
    absolute = path.absolute()
    for candidate in (absolute, *absolute.parents):
        if candidate.is_symlink():
            raise IntegrityError(f"generated view path must not contain symlinks: {candidate}")


def _atomic_write_text(path: Path, content: str) -> None:
    if path.is_symlink():
        raise IntegrityError(f"generated view target must not be a symlink: {path}")
    if path.exists() and not path.is_file():
        raise IntegrityError(f"generated view target must be a regular file: {path}")
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        temporary_path.unlink(missing_ok=True)


def render_views(snapshot: ProjectSnapshot, destination: str | Path) -> tuple[Path, ...]:
    root = Path(destination)
    _reject_symlink_components(root)
    root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        raise IntegrityError(f"generated view destination must be a directory: {root}")
    outputs: dict[str, list[str]] = {}
    outputs["CURRENT.md"] = [
        "# Current project state",
        "",
        "## Objectives",
        "",
        *_line_items(
            (item for item in snapshot.objectives.values() if item.get("state") == "active"),
            "objective",
        ),
        "",
        "## Accepted decisions",
        "",
        *_line_items(
            (
                item
                for item in snapshot.decisions.values()
                if item.get("state") == "accepted" and item.get("stale") is not True
            ),
            "decision",
        ),
        "",
        "## Verified findings",
        "",
        *_line_items(
            (
                item
                for item in snapshot.findings.values()
                if item.get("state") == "verified" and item.get("stale") is not True
            ),
            "finding",
        ),
    ]
    outputs["WORK_BOARD.md"] = ["# Work board", ""]
    for state in (
        "ready",
        "assigned",
        "active",
        "blocked",
        "completed",
        "review",
        "accepted",
        "cancelled",
    ):
        outputs["WORK_BOARD.md"].extend(
            [
                f"## {state.title()}",
                "",
                *_line_items(
                    (item for item in snapshot.tasks.values() if item.get("state") == state), "task"
                ),
                "",
            ]
        )
    outputs["OPEN_QUESTIONS.md"] = [
        "# Open discussions",
        "",
        *_line_items(
            (item for item in snapshot.threads.values() if item.get("state") == "open"), "thread"
        ),
    ]
    outputs["REVIEWS.md"] = ["# Reviews", "", *_line_items(snapshot.reviews.values(), "review")]
    outputs["DECISIONS.md"] = [
        "# Decisions",
        "",
        *_line_items(snapshot.decisions.values(), "decision"),
    ]
    outputs["KNOWN_RISKS.md"] = [
        "# Known risks",
        "",
        "## Warnings",
        "",
        *([f"- {_markdown_inline(item)}" for item in sorted(set(snapshot.warnings))] or ["- None"]),
        "",
        "## Reported or contested findings",
        "",
        *_line_items(
            (
                item
                for item in snapshot.findings.values()
                if item.get("state") in {"reported", "contested"}
            ),
            "finding",
        ),
    ]
    outputs["HANDOFFS.md"] = ["# Handoffs", "", *_line_items(snapshot.handoffs.values(), "handoff")]
    outputs["DELEGATIONS.md"] = [
        "# Delegations",
        "",
        *_line_items(snapshot.delegations.values(), "delegation"),
    ]
    paths: list[Path] = []
    for name, lines in outputs.items():
        path = root / name
        _atomic_write_text(path, "\n".join(lines).rstrip() + "\n")
        paths.append(path)
    return tuple(paths)
