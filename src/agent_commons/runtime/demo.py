"""A no-provider demo runner.

Demo mode exists for one reason: a newcomer should be able to see the whole
Hire -> Task -> Run -> result loop close in a scratch workspace, without a
subscription and without launching any billable process.  The DemoRunner stands
in for the provider CLI at the exact runner seam the real broker uses -- so this
is still the one launch path, not a second one -- and, as the run's bound child
session, records an honest ``delegation.succeeded`` whose summary says plainly
that no provider ran.

It only self-completes an ``implementation`` run.  It never fabricates a review
or a verification: for those purposes it returns without a canonical outcome, so
the runtime's own finalizer classifies the run ``needs_operator`` rather than
letting a demo invent a verdict.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from agent_commons.errors import CommonsError
from agent_commons.runtime.model import RunnerInvocation
from agent_commons.runtime.subprocess_runner import ProcessResult, RunOutcome, RunReason

_LOG = logging.getLogger("agent_commons.runtime.demo")

#: A recognisable fake pid.  The run is terminal the moment ``run`` returns, so
#: liveness is never actually probed against it.
_DEMO_PID = 424242


class DemoRunner:
    """Complete a run without launching a provider, honestly labelled as a demo."""

    def __init__(self, state_root: str | Path) -> None:
        self._state_root = Path(state_root)

    def run(
        self,
        invocation: RunnerInvocation,
        *,
        cwd: str | Path,
        child_session_id: str,
        delegation_id: str | None = None,
        timeout_seconds: int,
        max_output_bytes: int,
        cancellation: object | None = None,
        on_started: Callable[[int], None] | None = None,
    ) -> ProcessResult:
        del invocation, timeout_seconds, max_output_bytes, cancellation
        # Signalling start lets the broker record the canonical delegation.started,
        # exactly as a real provider start would, before the child acts.
        if on_started is not None:
            on_started(_DEMO_PID)
        from agent_commons.services import CommonsManager

        child = CommonsManager(
            Path(cwd), session_id=child_session_id, state_root=self._state_root
        )
        bound = [
            record
            for record in child.snapshot().delegations.values()
            if str(record.get("child_session_id")) == child_session_id
            and record.get("state") == "active"
        ]
        if bound:
            delegation = bound[0]
            if str(delegation.get("purpose")) == "implementation":
                target = delegation.get("target_ref") or {}
                try:
                    child.succeed_delegation(
                        str(delegation["id"]),
                        str(delegation.get("effective_revision") or delegation["revision"]),
                        summary="demo run — no provider was launched; simulated success",
                        result_refs=(dict(target),),
                        idempotency_key=f"demo-succeed-{delegation['id']}",
                    )
                except CommonsError as exc:
                    # Leave it for the runtime finalizer, which will classify it
                    # needs_operator; never crash the demo, never hide the cause.
                    _LOG.warning("demo run could not self-complete %s: %s", delegation["id"], exc)
        return ProcessResult(
            outcome=RunOutcome.SUCCEEDED,
            reason=RunReason.COMPLETED,
            exit_code=0,
            pid=_DEMO_PID,
            duration_seconds=0.0,
            stdout=b"",
            stderr=b"",
            stdout_bytes_seen=0,
            stderr_bytes_seen=0,
            output_truncated=False,
        )
