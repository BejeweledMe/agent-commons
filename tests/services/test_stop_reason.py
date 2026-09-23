"""Closed-set StopReason/LaunchRefusal encoding and its delegation runtime uses.

WP-40 added ``agent_commons.runtime.refusals`` (``LaunchRefusal``,
``StopReason``, ``verify_executor_access``) and wired ``StopReason.summary()``
into the bounded delegation summary the runtime writes for the
process-succeeded-without-canonical-result (``needs_operator`` /
``invalid_result``) and the provider-failed transitions. These tests prove
the encoding is sanitized and closed-set, and that both delegation runtime
transitions actually carry it.

Fixtures and fake runners are reused from ``tests/services/test_delegation_launch_plan.py``
(``_workspace``, ``_service``, ``_Initialization``, ``_ReadyAuth``,
``_SuccessWithoutTerminalMcp``) rather than inventing a second harness.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent_commons.errors import ConfigurationError
from agent_commons.runtime import ProcessResult, ProviderInitializationState, RunOutcome, RunReason
from agent_commons.runtime.refusals import LaunchRefusal, StopReason, decode_stop_reason
from tests.services.test_delegation_launch_plan import (
    _Initialization,
    _service,
    _SuccessWithoutTerminalMcp,
    _workspace,
)


def _parse_stop_reason(summary: str) -> dict[str, str]:
    """Extract and decode the ``stop_reason:{...}`` segment of a written summary."""

    marker = "stop_reason:"
    assert marker in summary
    return json.loads(summary.split(marker, 1)[1])


class _AlwaysFailingProvider:
    """A provider process that exits non-zero before any canonical start."""

    def __init__(self) -> None:
        self.invocations: list[Any] = []

    def run(self, invocation: Any, **values: Any) -> ProcessResult:
        self.invocations.append(invocation)
        values["on_started"](values.pop("test_pid", 51000))
        return ProcessResult(
            outcome=RunOutcome.FAILED,
            reason=RunReason.NONZERO_EXIT,
            exit_code=1,
            pid=51000,
            duration_seconds=0.01,
            stdout=b"",
            stderr=b"synthetic non-zero exit for stop-reason coverage",
            stdout_bytes_seen=0,
            stderr_bytes_seen=48,
            output_truncated=False,
        )


# --- StopReason.summary() encoding ------------------------------------------------


def test_stop_reason_summary_has_prefix_and_exactly_the_closed_keys() -> None:
    stop_reason = StopReason(
        code="provider_reported_error",
        reason="The provider exited with a diagnostic.",
        next_action="inspect_provider_diagnostic",
    )

    text = stop_reason.summary()

    assert text.startswith("stop_reason:")
    payload = json.loads(text[len("stop_reason:") :])
    assert set(payload) == {"code", "reason", "next_action"}
    assert payload == {
        "code": "provider_reported_error",
        "reason": "The provider exited with a diagnostic.",
        "next_action": "inspect_provider_diagnostic",
    }


@pytest.mark.parametrize(
    "code",
    [
        "not_a_real_code",
        "",
        "provider_reported_error ",  # trailing whitespace is not an allowlisted code
    ],
)
def test_stop_reason_refuses_codes_outside_the_closed_set(code: str) -> None:
    with pytest.raises(ConfigurationError, match="stop reason code is not allowlisted"):
        StopReason(code=code, reason="irrelevant", next_action="irrelevant")


def test_stop_reason_accepts_every_documented_code() -> None:
    # Each of these is exercised as a public behavior: construction must not
    # raise for any code the module itself is documented to use.
    for code in (
        "workspace_snapshot_unreadable",
        "executor_access_missing",
        "provider_mcp_handshake_failed",
        "unknown",
        "executor_stopped_by_environment",
        "provider_reported_error",
    ):
        StopReason(code=code, reason="ok", next_action="ok")


def test_stop_reason_sanitizes_absolute_paths() -> None:
    stop_reason = StopReason(
        code="unknown",
        reason="Failed while reading /Users/example/project/config.json for launch.",
        next_action="Inspect /var/tmp/agent-commons/state/attempt.json manually.",
    )

    assert "/Users/example/project/config.json" not in stop_reason.reason
    assert "[redacted path]" in stop_reason.reason
    assert "/var/tmp/agent-commons/state/attempt.json" not in stop_reason.next_action
    assert "[redacted path]" in stop_reason.next_action


def test_stop_reason_sanitizes_control_characters() -> None:
    stop_reason = StopReason(
        code="unknown",
        reason="Bad\x07input\x1bhere\x7fend",
        next_action="ok",
    )

    for control_char in ("\x07", "\x1b", "\x7f"):
        assert control_char not in stop_reason.reason
    assert "Bad" in stop_reason.reason and "input" in stop_reason.reason


def test_stop_reason_bounds_reason_to_256_characters() -> None:
    stop_reason = StopReason(code="unknown", reason="A" * 300, next_action="ok")

    assert len(stop_reason.reason) == 256
    assert stop_reason.reason == "A" * 256


def test_stop_reason_bounds_next_action_to_256_characters() -> None:
    stop_reason = StopReason(code="unknown", reason="ok", next_action="B" * 300)

    assert len(stop_reason.next_action) == 256
    assert stop_reason.next_action == "B" * 256


# --- decode_stop_reason(): the pure read-back of that encoding ----------------------


def test_decode_stop_reason_round_trips_the_whole_summary() -> None:
    stop_reason = StopReason(
        code="executor_stopped_by_environment",
        reason="The environment stopped the worker before it recorded a result.",
        next_action="inspect_canonical_outcome",
    )

    assert decode_stop_reason(stop_reason.summary()) == stop_reason


def test_decode_stop_reason_reads_the_trailing_segment_after_one_space() -> None:
    """This is exactly the shape the delegation runtime writes into a summary."""

    stop_reason = StopReason(
        code="provider_reported_error",
        reason="The provider exited with a diagnostic.",
        next_action="inspect_provider_diagnostic",
    )
    summary = (
        "The allowlisted provider exited without a canonical successful result. "
        + stop_reason.summary()
    )

    assert decode_stop_reason(summary) == stop_reason


def test_decode_stop_reason_revalidates_through_the_dataclass() -> None:
    """A decoded value is sanitized and bounded by StopReason, not trusted as written."""

    crafted = 'stop_reason:{"code":"unknown","reason":"Read /Users/example/secret.json here",'
    crafted += '"next_action":"' + "B" * 300 + '"}'

    decoded = decode_stop_reason(crafted)

    assert decoded is not None
    assert "/Users/example/secret.json" not in decoded.reason
    assert "[redacted path]" in decoded.reason
    assert len(decoded.next_action) == 256


@pytest.mark.parametrize(
    "summary",
    [
        None,
        "",
        "A summary with no marker at all.",
        "stop_reason:",  # the marker with nothing after it
        "stop_reason:{not json",
        'stop_reason:{"code":"made_up_code","reason":"r","next_action":"n"}',
        'stop_reason:{"code":"unknown","reason":"r"}',  # missing a closed key
        'stop_reason:{"code":"unknown","reason":"r","next_action":"n","extra":"x"}',
        'stop_reason:{"code":"unknown","reason":"","next_action":"n"}',  # empty text
        'stop_reason:{"code":"unknown","reason":3,"next_action":"n"}',  # not a string
        'stop_reason:["unknown","r","n"]',  # not an object
        'stop_reason:"unknown"',  # not an object
        # the JSON must be the entire remainder of the summary
        'prose stop_reason:{"code":"unknown","reason":"r"} trailing text',
        'prosestop_reason:{"code":"unknown","reason":"r","next_action":"n"}',  # no space boundary
    ],
)
def test_decode_stop_reason_returns_none_for_anything_it_cannot_read(summary: str | None) -> None:
    assert decode_stop_reason(summary) is None


def test_decode_stop_reason_refuses_an_unbounded_marker_payload() -> None:
    """A hostile summary is never parsed past the widest legitimate encoding."""

    oversized = 'stop_reason:{"code":"unknown","reason":"' + "A" * 5000 + '","next_action":"n"}'

    assert decode_stop_reason(oversized) is None


# --- LaunchRefusal.as_error() -------------------------------------------------------


def test_launch_refusal_as_error_yields_the_typed_configuration_error() -> None:
    refusal = LaunchRefusal(
        "executor_access_missing",
        "The selected checkout is not a Git worktree root.",
        "select_git_worktree",
    )

    error = refusal.as_error()

    # This is exactly the error the launch path raises: see
    # `DelegationRuntimeService.run` -> `raise access_refusal.as_error()` in
    # src/agent_commons/services/delegation_runtime.py.
    assert isinstance(error, ConfigurationError)
    assert error.code == "executor_access_missing"  # type: ignore[attr-defined]
    assert error.safe_next_actions == ("select_git_worktree",)  # type: ignore[attr-defined]
    assert "The selected checkout is not a Git worktree root." in str(error)


def test_launch_refusal_refuses_codes_outside_its_narrower_closed_set() -> None:
    # "executor_stopped_by_environment" and "provider_reported_error" are
    # valid StopReason codes but are not admitted launch-refusal codes: a
    # refusal happens before any attempt exists, so a stop-only code here
    # would misdescribe when the failure was discovered.
    with pytest.raises(ConfigurationError, match="launch refusal code is not allowlisted"):
        LaunchRefusal("provider_reported_error", "irrelevant", "irrelevant")
    with pytest.raises(ConfigurationError, match="launch refusal code is not allowlisted"):
        LaunchRefusal("executor_stopped_by_environment", "irrelevant", "irrelevant")


# --- Delegation runtime transitions that must carry the encoded stop reason --------


def test_delegation_runtime_needs_operator_summary_carries_a_closed_set_stop_reason(
    tmp_path: Path,
) -> None:
    """Process succeeded without a canonical terminal result -> needs_operator."""

    manager, delegation = _workspace(tmp_path)
    initialization = _Initialization(ProviderInitializationState.READY)
    runner = _SuccessWithoutTerminalMcp()
    service = _service(manager, runner=runner, initialization=initialization)

    result = service.run(
        str(delegation["entity_ref"]["id"]),
        str(delegation["revision"]),
        idempotency_key="stop-reason-needs-operator",
    )

    assert result["delegation"]["state"] == "needs_operator"
    assert result["delegation"]["reason_code"] == "invalid_result"
    assert bool(result["attempt"]["process_canonical_mismatch"]) is True

    summary = str(result["delegation"]["summary"])
    payload = _parse_stop_reason(summary)
    assert set(payload) == {"code", "reason", "next_action"}
    # Round-tripping through the public dataclass is itself the closed-set
    # proof: StopReason.__post_init__ rejects any code outside its allowlist.
    reconstructed = StopReason(**payload)
    assert reconstructed.code == "unknown"


def test_delegation_runtime_failed_summary_carries_a_closed_set_stop_reason(
    tmp_path: Path,
) -> None:
    """A provider that exits non-zero before canonical start -> failed."""

    manager, delegation = _workspace(tmp_path)
    initialization = _Initialization(ProviderInitializationState.READY)
    runner = _AlwaysFailingProvider()
    service = _service(manager, runner=runner, initialization=initialization)

    result = service.run(
        str(delegation["entity_ref"]["id"]),
        str(delegation["revision"]),
        idempotency_key="stop-reason-failed",
    )

    assert result["delegation"]["state"] == "failed"
    assert len(runner.invocations) == 1

    summary = str(result["delegation"]["summary"])
    payload = _parse_stop_reason(summary)
    assert set(payload) == {"code", "reason", "next_action"}
    reconstructed = StopReason(**payload)
    assert reconstructed.code == "provider_reported_error"
    # The reader the tracker uses sees the same object in the written summary.
    assert decode_stop_reason(summary) == reconstructed
