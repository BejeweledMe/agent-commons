from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from agent_commons.errors import IntegrityError
from agent_commons.runtime import TerminalToolAuditStore


def test_terminal_tool_audit_is_private_content_free_and_fail_closed(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    delegation_id = "delegation.01KXZZZZZZZZZZZZZZZZZZZZZZ"
    store = TerminalToolAuditStore(state_root)

    store.record(delegation_id, "commons_succeed_delegation", "called")
    audit = store.record(delegation_id, "commons_succeed_delegation", "completed")

    assert audit.terminal_tool_calls == 1
    assert audit.terminal_tool_completions == 1
    path = next((state_root / "runtime" / "tool-audit").glob("*.json"))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    body = path.read_text(encoding="utf-8")
    assert "summary" not in body and "result_refs" not in body

    store.record(delegation_id, "commons_delegation_needs_operator", "called")
    audit = store.record(
        delegation_id,
        "commons_delegation_needs_operator",
        "rejected",
        error_type="LifecycleConflictError",
        message=(
            "expected revision changed; credential=sk-proj-never-store-this000000; "
            "source=/private/tmp/customer/review.json"
        ),
    )
    assert audit.terminal_tool_rejections == 1
    assert audit.rejection_details[0].ordinal == 1
    assert audit.rejection_details[0].error_type == "LifecycleConflictError"
    assert audit.rejection_details[0].message == "details redacted by security policy"
    body = path.read_text(encoding="utf-8")
    assert "sk-proj-never-store-this" not in body
    assert "/private/tmp/customer" not in body

    value = json.loads(body)
    value["terminal_tool_rejections"] = 3
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
    with pytest.raises(IntegrityError, match="outcomes exceed calls"):
        store.get(delegation_id)
