"""Bind bounded operational communication to one live canonical delegation.

The underlying :mod:`agent_commons.runtime.communication` store deliberately
knows nothing about canonical lifecycle.  This service derives its participant
graph and immutable target from the canonical delegation plus its durable
runtime attempt, so callers cannot select arbitrary recipients or widen scope.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from agent_commons.core.ids import stable_id
from agent_commons.errors import LifecycleConflictError, ValidationError
from agent_commons.runtime import (
    Attempt,
    AttemptState,
    AttemptStore,
    CommunicationScope,
    CommunicationStore,
    OperationKind,
    OperationRecord,
    OperationRequestSpec,
    checkout_fingerprint,
)

from .manager import CommonsManager

_CANONICAL_INPUT_NEEDED = "Delegated work is waiting for bounded parent input."
_CANONICAL_INPUT_RESOLVED = "Bounded parent input was supplied through the private runtime channel."


def _bounded_text(name: str, value: str, *, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise ValidationError(f"{name} must contain 1 to {max_length} characters")
    return value.strip()


def _operation_key(kind: str, delegation_id: str, caller_key: str) -> str:
    _bounded_text("idempotency_key", caller_key, max_length=256)
    digest = hashlib.sha256(f"{kind}\0{delegation_id}\0{caller_key}".encode()).hexdigest()
    return f"comm-{kind}-{digest}"


def _canonical_key(operation_id: str, action: str) -> str:
    digest = hashlib.sha256(f"{operation_id}\0{action}".encode()).hexdigest()[:32]
    return f"communication-{action}-{digest}"


def _participant_id(session_id: str) -> str:
    """Use a typed, deterministic pseudonym rather than expose a registry UUID."""

    return stable_id("session", session_id)


class CommunicationRuntimeService:
    """Task-scoped parent/child communication with exact runtime bindings."""

    def __init__(
        self,
        manager: CommonsManager,
        *,
        attempts: AttemptStore | None = None,
        store: CommunicationStore | None = None,
    ) -> None:
        self.manager = manager
        self.attempts = attempts or AttemptStore(
            manager.paths.state_root,
            security_policy=manager.policy,
            read_only=manager.read_only,
        )
        self.store = store or CommunicationStore(
            manager.paths.state_root,
            security_policy=manager.policy,
            read_only=manager.read_only,
        )

    def _active_session_id(self) -> str:
        session = self.manager.sessions.require_active(self.manager.session_id)
        return session.session_id

    def _delegation(self, delegation_id: str) -> dict[str, Any]:
        delegation = self.manager.get_delegation(delegation_id)
        target = delegation.get("target_ref") or {}
        if target.get("kind") != "task":
            raise LifecycleConflictError(
                "task-scoped communication requires a task-target delegation"
            )
        return delegation

    def _assert_target_current(
        self,
        delegation: Mapping[str, Any],
        *,
        target_revision: str,
    ) -> None:
        target = delegation.get("target_ref") or {}
        if delegation.get("target_revision") != target_revision:
            raise LifecycleConflictError("communication target revision is stale")
        task = next(
            (
                item
                for item in self.manager.list_tasks(state=None)
                if item.get("id") == target.get("id")
            ),
            None,
        )
        if task is None:
            raise LifecycleConflictError("communication target task no longer exists")
        current = str(task.get("effective_revision") or task.get("revision"))
        if current != target_revision:
            raise LifecycleConflictError("communication target revision is stale")

    def _latest_live_attempt(self, delegation: Mapping[str, Any]) -> Attempt:
        delegation_id = str(delegation["id"])
        matches = [
            attempt
            for attempt in self.attempts.list_attempts()
            if attempt.correlation.delegation_id == delegation_id
        ]
        if not matches:
            raise LifecycleConflictError("delegation has no durable operational attempt")
        latest = matches[-1]
        target = delegation.get("target_ref") or {}
        expected = {
            "target_kind": target.get("kind"),
            "target_id": target.get("id"),
            "target_revision": delegation.get("target_revision"),
            "parent_session_id": delegation.get("parent_session_id"),
            "child_session_id": delegation.get("child_session_id"),
        }
        actual = {
            "target_kind": latest.correlation.target_kind,
            "target_id": latest.correlation.target_id,
            "target_revision": latest.correlation.target_revision,
            "parent_session_id": latest.correlation.parent_session_id,
            "child_session_id": latest.correlation.child_session_id,
        }
        if actual != expected:
            raise LifecycleConflictError("operational attempt does not match delegation scope")
        if latest.checkout_fingerprint != checkout_fingerprint(self.manager.repo_root):
            raise LifecycleConflictError("operational attempt belongs to another checkout")
        if latest.state is not AttemptState.RUNNING:
            raise LifecycleConflictError("delegation has no live running provider attempt")
        return latest

    def _child_scope(
        self,
        delegation_id: str,
        *,
        allowed_states: frozenset[str] = frozenset({"active"}),
    ) -> tuple[dict[str, Any], CommunicationScope]:
        delegation = self._delegation(delegation_id)
        if delegation.get("state") not in allowed_states:
            raise LifecycleConflictError("child communication requires a live delegation")
        session_id = self._active_session_id()
        if session_id != delegation.get("child_session_id"):
            raise LifecycleConflictError("only the bound delegation child may open communication")
        target_revision = str(delegation["target_revision"])
        self._assert_target_current(delegation, target_revision=target_revision)
        attempt = self._latest_live_attempt(delegation)
        target = delegation["target_ref"]
        return delegation, CommunicationScope(
            workspace_fingerprint=checkout_fingerprint(self.manager.repo_root),
            delegation_id=str(delegation["id"]),
            task_id=str(target["id"]),
            target_revision=target_revision,
            attempt_id=attempt.attempt_id,
            sender_session_id=_participant_id(session_id),
            allowed_recipient_session_ids=(_participant_id(str(delegation["parent_session_id"])),),
        )

    def _parent_scope(
        self,
        delegation_id: str,
        *,
        allowed_states: frozenset[str] = frozenset({"active"}),
    ) -> tuple[dict[str, Any], CommunicationScope]:
        delegation = self._delegation(delegation_id)
        if delegation.get("state") not in allowed_states:
            raise LifecycleConflictError("parent control requires a live delegation")
        session_id = self._active_session_id()
        if session_id != delegation.get("parent_session_id"):
            raise LifecycleConflictError("only the canonical delegation parent may send control")
        target_revision = str(delegation["target_revision"])
        self._assert_target_current(delegation, target_revision=target_revision)
        attempt = self._latest_live_attempt(delegation)
        target = delegation["target_ref"]
        return delegation, CommunicationScope(
            workspace_fingerprint=checkout_fingerprint(self.manager.repo_root),
            delegation_id=str(delegation["id"]),
            task_id=str(target["id"]),
            target_revision=target_revision,
            attempt_id=attempt.attempt_id,
            sender_session_id=_participant_id(session_id),
            allowed_recipient_session_ids=(_participant_id(str(delegation["child_session_id"])),),
        )

    def _operation_for_current_session(self, operation_id: str) -> OperationRecord:
        session_id = self._active_session_id()
        record = self.store.check(
            operation_id,
            requester_session_id=_participant_id(session_id),
        )
        delegation = self._delegation(record.scope.delegation_id)
        self._assert_target_current(
            delegation,
            target_revision=record.scope.target_revision,
        )
        parent_id = _participant_id(str(delegation.get("parent_session_id")))
        child_id = _participant_id(str(delegation.get("child_session_id")))
        expected_sender, expected_recipient = (
            (parent_id, child_id)
            if record.kind in {OperationKind.GUIDANCE, OperationKind.CHECKPOINT}
            else (child_id, parent_id)
        )
        if (
            expected_recipient != record.scope.allowed_recipient_session_ids[0]
            or expected_sender != record.scope.sender_session_id
            or (delegation.get("target_ref") or {}).get("id") != record.scope.task_id
        ):
            raise LifecycleConflictError("communication participants no longer match delegation")
        return record

    def request_input(
        self,
        delegation_id: str,
        *,
        idempotency_key: str,
        question: str,
        why_needed: str,
        safe_context: Mapping[str, Any],
        desired_outcome: str,
        blocking: bool = True,
        deadline_seconds: int = 900,
    ) -> dict[str, Any]:
        if not isinstance(blocking, bool):
            raise ValidationError("blocking must be a boolean")
        if not isinstance(safe_context, Mapping):
            raise ValidationError("safe_context must be a mapping")
        delegation, scope = self._child_scope(
            delegation_id,
            allowed_states=frozenset({"active", "input_needed"}),
        )
        metadata = {
            "question": _bounded_text("question", question, max_length=1_000),
            "why_needed": _bounded_text("why_needed", why_needed, max_length=1_000),
            "safe_context": dict(safe_context),
            "desired_outcome": _bounded_text("desired_outcome", desired_outcome, max_length=1_000),
            "blocking": blocking,
        }
        operation = self.store.request(
            OperationRequestSpec(
                idempotency_key=_operation_key("input", delegation_id, idempotency_key),
                kind=OperationKind.REQUEST,
                scope=scope,
                metadata=metadata,
                deadline_seconds=deadline_seconds,
            )
        )
        current = delegation
        if blocking:
            if current.get("state") == "active":
                result = self.manager.mark_delegation_input_needed(
                    delegation_id,
                    str(current["revision"]),
                    summary=_CANONICAL_INPUT_NEEDED,
                    idempotency_key=_canonical_key(operation.operation_id, "input-needed"),
                )
                current = self.manager.get_delegation(delegation_id)
                if result.get("revision") != current.get("revision"):
                    raise LifecycleConflictError("input-needed transition did not become current")
            elif current.get("state") != "input_needed":
                raise LifecycleConflictError("blocking input cannot bind a terminal delegation")
        return {"operation": operation.as_dict(), "delegation": dict(current)}

    def _notify(
        self,
        delegation_id: str,
        *,
        idempotency_key: str,
        kind: OperationKind,
        metadata: Mapping[str, Any],
        deadline_seconds: int,
    ) -> dict[str, Any]:
        delegation, scope = self._child_scope(delegation_id)
        operation = self.store.request(
            OperationRequestSpec(
                idempotency_key=_operation_key(kind.value, delegation_id, idempotency_key),
                kind=kind,
                scope=scope,
                metadata=dict(metadata),
                deadline_seconds=deadline_seconds,
            )
        )
        return {"operation": operation.as_dict(), "delegation": delegation}

    def _parent_notify(
        self,
        delegation_id: str,
        *,
        idempotency_key: str,
        kind: OperationKind,
        metadata: Mapping[str, Any],
        deadline_seconds: int,
    ) -> dict[str, Any]:
        delegation, scope = self._parent_scope(delegation_id)
        operation = self.store.request(
            OperationRequestSpec(
                idempotency_key=_operation_key(kind.value, delegation_id, idempotency_key),
                kind=kind,
                scope=scope,
                metadata=dict(metadata),
                deadline_seconds=deadline_seconds,
            )
        )
        return {"operation": operation.as_dict(), "delegation": delegation}

    def send_guidance(
        self,
        delegation_id: str,
        *,
        idempotency_key: str,
        instruction: str,
        rationale: str,
        expected_effect: str,
        deadline_seconds: int = 900,
    ) -> dict[str, Any]:
        return self._parent_notify(
            delegation_id,
            idempotency_key=idempotency_key,
            kind=OperationKind.GUIDANCE,
            metadata={
                "instruction": _bounded_text("instruction", instruction, max_length=1_000),
                "rationale": _bounded_text("rationale", rationale, max_length=1_000),
                "expected_effect": _bounded_text(
                    "expected_effect", expected_effect, max_length=1_000
                ),
            },
            deadline_seconds=deadline_seconds,
        )

    def request_checkpoint(
        self,
        delegation_id: str,
        *,
        idempotency_key: str,
        reason: str,
        safe_boundary: str,
        expected_ack: str,
        deadline_seconds: int = 900,
    ) -> dict[str, Any]:
        return self._parent_notify(
            delegation_id,
            idempotency_key=idempotency_key,
            kind=OperationKind.CHECKPOINT,
            metadata={
                "reason": _bounded_text("reason", reason, max_length=1_000),
                "safe_boundary": _bounded_text("safe_boundary", safe_boundary, max_length=1_000),
                "expected_ack": _bounded_text("expected_ack", expected_ack, max_length=1_000),
            },
            deadline_seconds=deadline_seconds,
        )

    def share_progress(
        self,
        delegation_id: str,
        *,
        idempotency_key: str,
        summary: str,
        completed_units: int | None = None,
        total_units: int | None = None,
        deadline_seconds: int = 900,
    ) -> dict[str, Any]:
        if (completed_units is None) != (total_units is None):
            raise ValidationError("progress units must be supplied together")
        if completed_units is not None and (
            isinstance(completed_units, bool)
            or isinstance(total_units, bool)
            or completed_units < 0
            or total_units is None
            or total_units < 1
            or completed_units > total_units
        ):
            raise ValidationError("progress units are invalid")
        metadata: dict[str, Any] = {"summary": _bounded_text("summary", summary, max_length=1_000)}
        if completed_units is not None:
            metadata.update({"completed_units": completed_units, "total_units": total_units})
        return self._notify(
            delegation_id,
            idempotency_key=idempotency_key,
            kind=OperationKind.PROGRESS,
            metadata=metadata,
            deadline_seconds=deadline_seconds,
        )

    def report_blocker(
        self,
        delegation_id: str,
        *,
        idempotency_key: str,
        summary: str,
        impact: str,
        safe_next_action: str,
        deadline_seconds: int = 900,
    ) -> dict[str, Any]:
        return self._notify(
            delegation_id,
            idempotency_key=idempotency_key,
            kind=OperationKind.BLOCKER,
            metadata={
                "summary": _bounded_text("summary", summary, max_length=1_000),
                "impact": _bounded_text("impact", impact, max_length=1_000),
                "safe_next_action": _bounded_text(
                    "safe_next_action", safe_next_action, max_length=1_000
                ),
            },
            deadline_seconds=deadline_seconds,
        )

    def check_input(self, operation_id: str) -> dict[str, Any]:
        return self._operation_for_current_session(operation_id).as_dict()

    def reply_to_input(
        self,
        operation_id: str,
        *,
        idempotency_key: str,
        answer: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(answer, Mapping):
            raise ValidationError("answer must be a mapping")
        record = self._operation_for_current_session(operation_id)
        session_id = self._active_session_id()
        participant_id = _participant_id(session_id)
        if participant_id not in record.scope.allowed_recipient_session_ids:
            raise LifecycleConflictError("only the canonical parent may answer child input")
        delegation = self._delegation(record.scope.delegation_id)
        if delegation.get("state") not in {"active", "input_needed"}:
            raise LifecycleConflictError("input reply cannot resume a terminal delegation")
        replied = self.store.reply(
            operation_id,
            responder_session_id=participant_id,
            idempotency_key=_operation_key("reply", record.scope.delegation_id, idempotency_key),
            answer=dict(answer),
        )
        current = delegation
        if current.get("state") == "input_needed":
            self.manager.resume_delegation(
                str(current["id"]),
                str(current["revision"]),
                resolution=_CANONICAL_INPUT_RESOLVED,
                idempotency_key=_canonical_key(operation_id, "resumed"),
            )
            current = self.manager.get_delegation(str(current["id"]))
        return {"operation": replied.as_dict(), "delegation": dict(current)}

    def acknowledge(self, operation_id: str, *, idempotency_key: str) -> dict[str, Any]:
        record = self._operation_for_current_session(operation_id)
        if record.kind in {OperationKind.GUIDANCE, OperationKind.CHECKPOINT}:
            raise LifecycleConflictError(
                "parent controls require the explicit control acknowledgement surface"
            )
        return self.store.ack(
            operation_id,
            acker_session_id=_participant_id(self._active_session_id()),
            idempotency_key=_operation_key("ack", record.scope.delegation_id, idempotency_key),
        ).as_dict()

    def acknowledge_control(self, operation_id: str, *, idempotency_key: str) -> dict[str, Any]:
        """Acknowledge a parent guidance or checkpoint exactly once."""

        record = self._operation_for_current_session(operation_id)
        if record.kind not in {OperationKind.GUIDANCE, OperationKind.CHECKPOINT}:
            raise LifecycleConflictError("operation is not a parent control")
        return self.store.ack(
            operation_id,
            acker_session_id=_participant_id(self._active_session_id()),
            idempotency_key=_operation_key(
                "control-ack", record.scope.delegation_id, idempotency_key
            ),
        ).as_dict()

    def inbox(self) -> tuple[dict[str, Any], ...]:
        session_id = self._active_session_id()
        return tuple(
            record.as_dict()
            for record in self.store.list_operations(
                requester_session_id=_participant_id(session_id)
            )
        )
