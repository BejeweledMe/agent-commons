from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from agent_commons.mcp.server import build_server


class FakeServer:
    def __init__(self, name: str) -> None:
        self.name = name
        self.tools: dict[str, Any] = {}

    def tool(self, *_args: Any, **_kwargs: Any) -> Any:
        def register(function: Any) -> Any:
            self.tools[function.__name__] = function
            return function

        return register

    def run(self, *, transport: str) -> None:
        raise AssertionError(f"unexpected transport run: {transport}")


class FakeManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def orient(self, *, max_items: int) -> dict[str, Any]:
        return {"kind": "orient", "max_items": max_items}

    def inbox(self, *, max_items: int) -> dict[str, Any]:
        return {"kind": "inbox", "max_items": max_items}

    def list_tasks(self, *, state: str | None) -> list[dict[str, Any]]:
        return [{"kind": "task", "state": state}]

    def list_delegations(self, *, state: str | None) -> list[dict[str, Any]]:
        return [{"kind": "delegation", "state": state}]

    def get_delegation(self, delegation_id: str) -> dict[str, Any]:
        return {"delegation_id": delegation_id}

    def list_reviews(self, *, state: str | None) -> list[dict[str, Any]]:
        return [{"id": "review.01K00000000000000000000000", "state": state}]

    def list_artifacts(self) -> list[dict[str, Any]]:
        return [{"id": "artifact.01K00000000000000000000000"}]

    def get_artifact_bundle(self, artifact_id: str) -> dict[str, Any]:
        return {
            "artifact": {"id": artifact_id},
            "manifest": {
                "artifact_id": artifact_id,
                "revision": "sha256:" + "a" * 64,
            },
        }

    def create_delegation(self, **values: Any) -> dict[str, Any]:
        self.calls.append(("create", values))
        return {"delegation_id": "delegation.01K00000000000000000000000", **values}

    def cancel_delegation(
        self,
        delegation_id: str,
        expected_revision: str,
        *,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        values = {
            "delegation_id": delegation_id,
            "expected_revision": expected_revision,
            "reason": reason,
            "idempotency_key": idempotency_key,
        }
        self.calls.append(("cancel", values))
        return values

    def recover_delegation(
        self,
        delegation_id: str,
        expected_revision: str,
        *,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        values = {
            "delegation_id": delegation_id,
            "expected_revision": expected_revision,
            "reason": reason,
            "idempotency_key": idempotency_key,
        }
        self.calls.append(("recover", values))
        return values

    def complete_review(
        self,
        review_id: str,
        expected_revision: str,
        **values: Any,
    ) -> dict[str, Any]:
        result = {
            "review_id": review_id,
            "expected_revision": expected_revision,
            **values,
        }
        self.calls.append(("complete_review", result))
        return result

    def record_verification(self, **values: Any) -> dict[str, Any]:
        self.calls.append(("record_verification", values))
        return values

    def mark_delegation_input_needed(
        self, delegation_id: str, expected_revision: str, **values: Any
    ) -> dict[str, Any]:
        result = {"delegation_id": delegation_id, "expected_revision": expected_revision, **values}
        self.calls.append(("input_needed", result))
        return result

    def succeed_delegation(
        self, delegation_id: str, expected_revision: str, **values: Any
    ) -> dict[str, Any]:
        result = {"delegation_id": delegation_id, "expected_revision": expected_revision, **values}
        self.calls.append(("succeed", result))
        return result

    def mark_delegation_needs_operator(
        self, delegation_id: str, expected_revision: str, **values: Any
    ) -> dict[str, Any]:
        result = {"delegation_id": delegation_id, "expected_revision": expected_revision, **values}
        self.calls.append(("needs_operator", result))
        return result


class FakeRuntime:
    def profile_summaries(self) -> list[dict[str, Any]]:
        return [{"profile_id": "claude-independent-reviewer"}]

    def list_attempts(self) -> list[dict[str, Any]]:
        return [{"attempt_id": "attempt.01K00000000000000000000000"}]

    def run(
        self,
        delegation_id: str,
        expected_revision: str,
        *,
        idempotency_key: str,
        retry: bool = False,
    ) -> dict[str, Any]:
        return {
            "delegation_id": delegation_id,
            "expected_revision": expected_revision,
            "idempotency_key": idempotency_key,
            "retry": retry,
        }

    def reconcile(self) -> list[dict[str, Any]]:
        return [{"state": "needs_operator"}]


def test_bounded_tools_delegate_to_the_manager() -> None:
    manager = FakeManager()
    server = build_server(
        Path("."),
        manager=manager,  # type: ignore[arg-type]
        server_factory=FakeServer,
    )

    assert isinstance(server, FakeServer)
    assert set(server.tools) == {
        "commons_orient",
        "commons_inbox",
        "commons_list_tasks",
        "commons_list_delegations",
        "commons_show_delegation",
        "commons_list_reviews",
        "commons_show_review",
        "commons_show_artifact",
        "commons_request_delegation",
        "commons_cancel_delegation",
        "commons_recover_delegation",
        "commons_complete_review",
        "commons_record_verification",
        "commons_delegation_input_needed",
        "commons_succeed_delegation",
        "commons_delegation_needs_operator",
    }
    assert server.tools["commons_orient"](7) == {"kind": "orient", "max_items": 7}
    assert server.tools["commons_list_delegations"]("active") == [
        {"kind": "delegation", "state": "active"}
    ]
    assert server.tools["commons_show_review"]("review.01K00000000000000000000000")[
        "id"
    ].startswith("review.")
    assert server.tools["commons_show_artifact"]("artifact.01K00000000000000000000000")["artifact"][
        "id"
    ].startswith("artifact.")

    created = server.tools["commons_request_delegation"](
        "task:task.01K00000000000000000000000",
        "evt.01K00000000000000000000000",
        "claude-independent-reviewer",
        "independent_review",
        "review-task-01",
        1,
        600,
        1,
        1,
        "micro_usd",
        500_000,
        None,
    )
    assert created["target_ref"] == {
        "kind": "task",
        "id": "task.01K00000000000000000000000",
    }
    assert created["limits"] == {
        "max_depth": 1,
        "wall_time_seconds": 600,
        "max_attempts": 1,
        "max_concurrency": 1,
        "budget": {"unit": "micro_usd", "limit": 500_000},
    }

    cancelled = server.tools["commons_cancel_delegation"](
        created["delegation_id"],
        "evt.01K00000000000000000000001",
        "operator stopped it",
        "cancel-review-task-01",
    )
    assert cancelled["reason"] == "operator stopped it"
    recovered = server.tools["commons_recover_delegation"](
        created["delegation_id"],
        "evt.01K00000000000000000000001",
        "requester unavailable",
        "recover-review-task-01",
    )
    assert recovered["reason"] == "requester unavailable"
    completed = server.tools["commons_complete_review"](
        "review.01K00000000000000000000000",
        "evt.01K00000000000000000000001",
        "evt.01K00000000000000000000002",
        "approved",
        "The exact target satisfies the requested criteria.",
        "complete-review-01",
        ["artifact:artifact.01K00000000000000000000000"],
    )
    assert completed["evidence_refs"] == (
        {"kind": "artifact", "id": "artifact.01K00000000000000000000000"},
    )
    succeeded = server.tools["commons_succeed_delegation"](
        created["delegation_id"],
        "evt.01K00000000000000000000003",
        "Review recorded.",
        ["review:review.01K00000000000000000000000"],
        "succeed-review-delegation-01",
    )
    assert succeeded["result_refs"][0]["kind"] == "review"
    assert [name for name, _ in manager.calls] == [
        "create",
        "cancel",
        "recover",
        "complete_review",
        "succeed",
    ]


def test_stable_fastmcp_sdk_exposes_the_bounded_contract() -> None:
    pytest.importorskip("mcp.server.fastmcp")
    server = build_server(Path("."), manager=FakeManager())  # type: ignore[arg-type]

    tools = asyncio.run(server.list_tools())  # type: ignore[attr-defined]
    by_name = {tool.name: tool for tool in tools}

    assert set(by_name) == {
        "commons_orient",
        "commons_inbox",
        "commons_list_tasks",
        "commons_list_delegations",
        "commons_show_delegation",
        "commons_list_reviews",
        "commons_show_review",
        "commons_show_artifact",
        "commons_request_delegation",
        "commons_cancel_delegation",
        "commons_recover_delegation",
        "commons_complete_review",
        "commons_record_verification",
        "commons_delegation_input_needed",
        "commons_succeed_delegation",
        "commons_delegation_needs_operator",
    }
    request_schema = by_name["commons_request_delegation"].model_dump()["inputSchema"]
    assert set(request_schema["required"]) == {
        "target_ref",
        "target_revision",
        "target_profile",
        "purpose",
        "idempotency_key",
    }
    assert "command" not in request_schema["properties"]
    assert "environment" not in request_schema["properties"]
    assert "prompt" not in request_schema["properties"]
    assert by_name["commons_orient"].annotations.readOnlyHint is True
    assert by_name["commons_request_delegation"].annotations.idempotentHint is True
    assert by_name["commons_cancel_delegation"].annotations.destructiveHint is True
    assert by_name["commons_recover_delegation"].annotations.destructiveHint is True
    assert by_name["commons_complete_review"].annotations.idempotentHint is True
    assert by_name["commons_show_review"].annotations.readOnlyHint is True


def test_runtime_tools_are_explicitly_feature_gated_and_bounded() -> None:
    server = build_server(
        Path("."),
        manager=FakeManager(),  # type: ignore[arg-type]
        runtime=FakeRuntime(),
        server_factory=FakeServer,
    )

    assert isinstance(server, FakeServer)
    assert {
        "commons_runtime_profiles",
        "commons_runtime_attempts",
        "commons_run_delegation",
        "commons_reconcile_runtime",
    }.issubset(server.tools)
    result = server.tools["commons_run_delegation"](
        "delegation.01K00000000000000000000000",
        "evt.01K00000000000000000000000",
        "launch-review-01",
        False,
    )
    assert result == {
        "delegation_id": "delegation.01K00000000000000000000000",
        "expected_revision": "evt.01K00000000000000000000000",
        "idempotency_key": "launch-review-01",
        "retry": False,
    }
    assert "command" not in server.tools["commons_run_delegation"].__annotations__
    assert "prompt" not in server.tools["commons_run_delegation"].__annotations__
