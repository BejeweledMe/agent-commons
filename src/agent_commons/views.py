from __future__ import annotations

import html
import json
import os
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import Any

from agent_commons.domain.projection import ProjectSnapshot
from agent_commons.errors import IntegrityError

_TRUNCATION_MARKER = " …[truncated]"
_COMPACT_TEXT_BYTES = 384
_DEFAULT_COMPACT_BYTES = 20 * 1024


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


def truncate_utf8(value: str, max_bytes: int) -> str:
    """Public wrapper so other surfaces truncate exactly like the CLI views."""

    return _truncate_utf8(value, max_bytes)


def bounded_copy(
    value: Any,
    *,
    max_total_bytes: int = 65_536,
    max_text_bytes: int = 4_096,
    max_children: int = 32,
    max_depth: int = 8,
) -> Any:
    """Bound an arbitrary projected record for display.

    Shared with the local UI so a record can never be rendered under one set of
    truncation rules here and a different set there.
    """

    budget = _CopyBudget(
        remaining_bytes=max_total_bytes,
        remaining_nodes=4_096,
        max_text_bytes=max_text_bytes,
        max_children=max_children,
        max_depth=max_depth,
    )
    return _bounded_copy(value, budget)


def _addressed_items(
    values: Iterable[Mapping[str, Any]],
    *,
    addressed: set[str],
    max_items: int,
) -> list[Mapping[str, Any]]:
    return list(
        islice(
            (
                item
                for item in sorted(values, key=lambda value: str(value.get("id", "")))
                if item.get("state") == "open" and addressed.intersection(set(item.get("to") or []))
            ),
            max_items,
        )
    )


def addressed_spellings(
    role: str, session_id: str, acting_agent_ids: Iterable[Any] = ()
) -> set[str]:
    """Every spelling a recipient list may use to reach this session.

    Recipients arrive bare — a role name, a session id, an agent id — or
    kind-prefixed, the way references are spelled everywhere else
    ("role:independent-reviewer").  The matchers compared bare names only, so
    a prefixed handoff was unacknowledgeable and invisible in every inbox
    (finding.7B0CXG5QTQ5SCY2JMCTW7W2SVH).
    """

    spellings = {"*", role, session_id, f"role:{role}", f"session:{session_id}"}
    for item in acting_agent_ids:
        name = str(item)
        spellings.add(name)
        spellings.add(f"agent:{name}")
    spellings.discard("")
    spellings.discard("role:")
    spellings.discard("session:")
    spellings.discard("agent:")
    return spellings


def _compact_record(item: Mapping[str, Any]) -> dict[str, Any]:
    """Return a stable, action-oriented entity summary without actor or prose payloads."""

    result: dict[str, Any] = {}
    for key in (
        "id",
        "claim_id",
        "state",
        "title",
        "subject",
        "summary",
        "proposal",
        "target_profile",
        "priority",
        "revision",
        "effective_revision",
        "stale",
        "target_ref",
        "target_revision",
        "from_session_id",
        "owner_session_id",
        "to",
        "resources",
    ):
        value = item.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            result[key] = _truncate_utf8(value, _COMPACT_TEXT_BYTES)
        elif isinstance(value, Mapping):
            result[key] = {
                str(child_key): _truncate_utf8(str(child), _COMPACT_TEXT_BYTES)
                for child_key, child in islice(value.items(), 8)
            }
        elif isinstance(value, (list, tuple)):
            result[key] = [
                _truncate_utf8(str(child), _COMPACT_TEXT_BYTES) for child in islice(value, 8)
            ]
        elif isinstance(value, (bool, int, float)):
            result[key] = value
    return result


def _count_states(values: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in values:
        state = str(item.get("state", "unknown"))
        counts[state] = counts.get(state, 0) + 1
    return dict(sorted(counts.items()))


def _encoded_size(value: Mapping[str, Any]) -> int:
    return len(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def _fit_compact_result(
    result: dict[str, Any],
    *,
    collections: Iterable[list[Any]],
    max_total_bytes: int,
) -> dict[str, Any]:
    """Fit records exactly while preserving every documented container type."""

    mutable = list(collections)
    truncated = False
    while _encoded_size(result) > max_total_bytes:
        target = next((items for items in mutable if items), None)
        if target is None:
            break
        target.pop()
        truncated = True
    limits = result.get("limits")
    if isinstance(limits, dict):
        limits["truncated"] = truncated
    return result


def _verbose_orientation(
    snapshot: ProjectSnapshot,
    *,
    session: Mapping[str, Any] | None = None,
    claims: Iterable[Mapping[str, Any]] = (),
    max_items: int = 20,
    max_text_bytes: int = 4096,
    max_nested_items: int = 32,
    max_total_bytes: int = 131_072,
    acting_agent_ids: Sequence[str] = (),
) -> dict[str, Any]:
    role = str((session or {}).get("role_id", ""))
    session_id = str((session or {}).get("session_id", ""))
    # A thread addressed to a standing role must reach the session running as
    # it, whichever spelling the sender used — bare or kind-prefixed.
    addressed = addressed_spellings(role, session_id, acting_agent_ids)
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


def orientation(
    snapshot: ProjectSnapshot,
    *,
    session: Mapping[str, Any] | None = None,
    claims: Iterable[Mapping[str, Any]] = (),
    max_items: int = 20,
    max_text_bytes: int = 4096,
    max_nested_items: int = 32,
    max_total_bytes: int = 131_072,
    verbose: bool = False,
    read_diagnostics: Mapping[str, Any] | None = None,
    acting_agent_ids: Sequence[str] = (),
) -> dict[str, Any]:
    """Build a bounded workspace brief; detailed legacy records require ``verbose``."""

    if min(max_items, max_text_bytes, max_nested_items, max_total_bytes) < 1:
        raise ValueError("orientation bounds must be positive")
    if max_text_bytes < len(_TRUNCATION_MARKER.encode("utf-8")):
        raise ValueError("max_text_bytes is too small for the truncation marker")
    if verbose:
        result = _verbose_orientation(
            snapshot,
            session=session,
            claims=claims,
            max_items=max_items,
            max_text_bytes=max_text_bytes,
            max_nested_items=max_nested_items,
            max_total_bytes=max_total_bytes,
            acting_agent_ids=acting_agent_ids,
        )
        if read_diagnostics is not None:
            result["read_diagnostics"] = dict(read_diagnostics)
        return result

    compact_limit = min(max_total_bytes, _DEFAULT_COMPACT_BYTES)
    claims_all = list(claims)
    role = str((session or {}).get("role_id", ""))
    session_id = str((session or {}).get("session_id", ""))
    # A thread addressed to a standing role must reach the session running as
    # it, whichever spelling the sender used — bare or kind-prefixed.
    addressed = addressed_spellings(role, session_id, acting_agent_ids)
    objectives_all = sorted(snapshot.objectives.values(), key=lambda item: str(item.get("id", "")))
    objectives = [
        _compact_record(item)
        for item in islice(
            (item for item in objectives_all if item.get("state") == "active"), max_items
        )
    ]
    task_states = ("ready", "assigned", "active", "blocked", "completed", "review", "accepted")
    tasks_all = sorted(snapshot.tasks.values(), key=lambda item: str(item.get("id", "")))
    work = {
        state: [
            _compact_record(item)
            for item in islice(
                (item for item in tasks_all if item.get("state") == state), max_items
            )
        ]
        for state in task_states
    }
    reviews_all = sorted(snapshot.reviews.values(), key=lambda item: str(item.get("id", "")))
    pending_reviews = [
        _compact_record(item)
        for item in islice(
            (
                item
                for item in reviews_all
                if item.get("state") == "requested" and item.get("stale") is not True
            ),
            max_items,
        )
    ]
    stale_reviews = [
        _compact_record(item)
        for item in islice(
            (
                item
                for item in reviews_all
                if item.get("state") != "requested" and item.get("stale") is True
            ),
            max_items,
        )
    ]
    delegation_states = (
        "requested",
        "active",
        "input_needed",
        "succeeded",
        "failed",
        "cancelled",
        "timed_out",
        "needs_operator",
    )
    delegations_all = sorted(
        snapshot.delegations.values(), key=lambda item: str(item.get("id", ""))
    )
    delegations = {
        state: [
            _compact_record(item)
            for item in islice(
                (item for item in delegations_all if item.get("state") == state), max_items
            )
        ]
        for state in delegation_states
    }
    inbox = [
        _compact_record(item)
        for item in _addressed_items(
            snapshot.threads.values(), addressed=addressed, max_items=max_items
        )
    ]
    handoffs = [
        _compact_record(item)
        for item in _addressed_items(
            snapshot.handoffs.values(), addressed=addressed, max_items=max_items
        )
    ]
    decisions = [
        _compact_record(item)
        for item in islice(
            (
                item
                for item in sorted(
                    snapshot.decisions.values(), key=lambda value: str(value.get("id", ""))
                )
                if item.get("state") == "accepted" and item.get("stale") is not True
            ),
            max_items,
        )
    ]
    findings = [
        _compact_record(item)
        for item in islice(
            (
                item
                for item in sorted(
                    snapshot.findings.values(), key=lambda value: str(value.get("id", ""))
                )
                if item.get("state") == "verified" and item.get("stale") is not True
            ),
            max_items,
        )
    ]
    compact_claims = [
        _compact_record(item)
        for item in islice(
            sorted(claims_all, key=lambda value: str(value.get("claim_id", ""))), max_items
        )
    ]
    warnings = [
        _truncate_utf8(value, _COMPACT_TEXT_BYTES)
        for value in sorted(set(snapshot.warnings))[:max_items]
    ]
    compact_session = {
        key: (session or {})[key]
        for key in ("session_id", "role_id", "client")
        if key in (session or {})
    }
    result: dict[str, Any] = {
        "schema": "agent_commons.orientation.v1",
        "workspace_id": snapshot.workspace_id,
        "session": compact_session,
        "counts": {
            "objectives": _count_states(objectives_all),
            "tasks": _count_states(tasks_all),
            "threads": _count_states(snapshot.threads.values()),
            "reviews": _count_states(reviews_all),
            "delegations": _count_states(delegations_all),
            "handoffs": _count_states(snapshot.handoffs.values()),
            "decisions": _count_states(snapshot.decisions.values()),
            "findings": _count_states(snapshot.findings.values()),
            "active_claims": len(claims_all),
        },
        "objectives": objectives,
        "work": work,
        "pending_reviews": pending_reviews,
        "stale_review_judgments": stale_reviews,
        "delegations": delegations,
        "inbox": inbox,
        "handoffs": handoffs,
        "effective_truth": {"decisions": decisions, "findings": findings},
        "claims": compact_claims,
        "warnings": warnings,
        "read_diagnostics": dict(read_diagnostics or {}),
        "limits": {
            "max_items_per_section": max_items,
            "max_total_bytes": compact_limit,
            "truncated": False,
        },
    }
    trim_order = [
        work["accepted"],
        work["completed"],
        delegations["succeeded"],
        delegations["failed"],
        delegations["cancelled"],
        decisions,
        findings,
        stale_reviews,
        objectives,
        work["ready"],
        work["assigned"],
        work["active"],
        work["blocked"],
        work["review"],
        *[delegations[state] for state in delegation_states],
        compact_claims,
        warnings,
        inbox,
        handoffs,
        pending_reviews,
    ]
    return _fit_compact_result(result, collections=trim_order, max_total_bytes=compact_limit)


def inbox_view(
    snapshot: ProjectSnapshot,
    *,
    session: Mapping[str, Any] | None = None,
    max_items: int = 20,
    max_total_bytes: int = 131_072,
    verbose: bool = False,
    read_diagnostics: Mapping[str, Any] | None = None,
    acting_agent_ids: Sequence[str] = (),
) -> dict[str, Any]:
    """Build the addressed inbox directly, without constructing orientation."""

    role = str((session or {}).get("role_id", ""))
    session_id = str((session or {}).get("session_id", ""))
    # A thread addressed to a standing role must reach the session running as
    # it, whichever spelling the sender used — bare or kind-prefixed.
    addressed = addressed_spellings(role, session_id, acting_agent_ids)
    all_threads = _addressed_items(
        snapshot.threads.values(), addressed=addressed, max_items=max(1, len(snapshot.threads))
    )
    all_handoffs = _addressed_items(
        snapshot.handoffs.values(), addressed=addressed, max_items=max(1, len(snapshot.handoffs))
    )
    threads = all_threads[:max_items]
    handoffs = all_handoffs[:max_items]
    effective_limit = max_total_bytes if verbose else min(max_total_bytes, _DEFAULT_COMPACT_BYTES)
    result: dict[str, Any] = {
        "schema": "agent_commons.inbox.v1",
        "workspace_id": snapshot.workspace_id,
        "counts": {"threads": len(all_threads), "handoffs": len(all_handoffs)},
        "threads": [dict(item) if verbose else _compact_record(item) for item in threads],
        "handoffs": [dict(item) if verbose else _compact_record(item) for item in handoffs],
        "read_diagnostics": dict(read_diagnostics or {}),
        "limits": {
            "max_items_per_section": max_items,
            "max_total_bytes": effective_limit,
            "truncated": False,
        },
    }
    return _fit_compact_result(
        result,
        collections=(result["threads"], result["handoffs"]),
        max_total_bytes=effective_limit,
    )


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
