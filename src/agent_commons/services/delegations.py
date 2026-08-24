"""Delegation commands mixed into the universal workspace manager."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agent_commons.domain.lifecycle import entity
from agent_commons.errors import LifecycleConflictError, ValidationError


class DelegationCommands:
    """Commands for bounded worker delegations and their terminal outcomes."""

    def list_delegations(self, *, state: str | None = None) -> list[dict[str, Any]]:
        return self._list("delegation", state=state)

    def get_delegation(self, delegation_id: str) -> dict[str, Any]:
        current = entity(self.snapshot(), "delegation", delegation_id)
        if current is None:
            raise LifecycleConflictError(f"delegation does not exist: {delegation_id}")
        return dict(current)

    def create_delegation(
        self,
        *,
        target_ref: Mapping[str, str],
        target_revision: str,
        target_profile: str,
        purpose: str,
        limits: Mapping[str, Any],
        parent_delegation_id: str | None = None,
        on_behalf_of_agent_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        max_depth = limits.get("max_depth")
        if isinstance(max_depth, int) and not isinstance(max_depth, bool) and max_depth > 0:
            raise ValidationError(
                "max_depth > 0 is not supported: delegated workers cannot create or run child "
                "delegations in the current release; set max_depth to 0 for one bounded leaf"
            )
        key = self._idempotency_key("delegation.requested", idempotency_key)
        delegation_id = self._new_entity_id("delegation", "delegation.requested", key)
        session = self._active_session()
        snapshot = self.snapshot()
        target = self._assert_refs_exist((target_ref,), snapshot)[0]
        parent_id = str(parent_delegation_id or "")
        if parent_id:
            parent = entity(snapshot, "delegation", parent_id)
            if parent is None:
                raise LifecycleConflictError(f"delegation does not exist: {parent_id}")
            root_delegation_id = str(parent.get("root_delegation_id", ""))
            depth = int(parent.get("depth", -1)) + 1
        else:
            root_delegation_id = delegation_id
            depth = 0
        subject = {"kind": "delegation", "id": delegation_id}
        relations = [self._relation(subject, "targets", target)]
        if parent_id:
            relations.append(
                self._relation(
                    subject,
                    "spawned_by",
                    {"kind": "delegation", "id": parent_id},
                )
            )
        if on_behalf_of_agent_id:
            # Which standing role this run acts for.  Carried as a relation
            # rather than a payload field so the delegation schema -- and every
            # delegation event already in every workspace -- stays readable by
            # the previous binary.  Whether this caller *may* name that role is
            # decided by the lifecycle, on the same path replay takes.
            relations.append(
                self._relation(
                    subject,
                    "on_behalf_of",
                    {"kind": "agent", "id": on_behalf_of_agent_id},
                )
            )
        payload: dict[str, Any] = {
            "delegation_id": delegation_id,
            "target_ref": target,
            "target_revision": target_revision,
            "target_profile": target_profile,
            "purpose": purpose,
            "parent_session_id": session.session_id,
            "root_delegation_id": root_delegation_id,
            "depth": depth,
            "limits": dict(limits),
        }
        if parent_id:
            payload["parent_delegation_id"] = parent_id
        return self.record_event(
            "delegation.requested",
            payload,
            idempotency_key=key,
            relations=relations,
            tags=("delegation", purpose),
        )

    def _delegation_transition(
        self,
        delegation_id: str,
        expected_revision: str,
        action: str,
        *,
        idempotency_key: str | None = None,
        **fields: Any,
    ) -> dict[str, Any]:
        event_type = f"delegation.{action}"
        key = self._idempotency_key(event_type, idempotency_key)
        return self.record_event(
            event_type,
            {
                "delegation_id": delegation_id,
                "expected_revision": expected_revision,
                **fields,
            },
            idempotency_key=key,
            tags=("delegation",),
        )

    def start_delegation(
        self,
        delegation_id: str,
        expected_revision: str,
        *,
        child_session_id: str,
        attempt: int = 1,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        self.sessions.require_active(child_session_id)
        return self._delegation_transition(
            delegation_id,
            expected_revision,
            "started",
            child_session_id=child_session_id,
            attempt=attempt,
            idempotency_key=idempotency_key,
        )

    def mark_delegation_input_needed(
        self,
        delegation_id: str,
        expected_revision: str,
        *,
        summary: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self._delegation_transition(
            delegation_id,
            expected_revision,
            "input_needed",
            summary=summary,
            idempotency_key=idempotency_key,
        )

    def resume_delegation(
        self,
        delegation_id: str,
        expected_revision: str,
        *,
        resolution: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self._delegation_transition(
            delegation_id,
            expected_revision,
            "resumed",
            resolution=resolution,
            idempotency_key=idempotency_key,
        )

    def succeed_delegation(
        self,
        delegation_id: str,
        expected_revision: str,
        *,
        summary: str,
        result_refs: Sequence[Mapping[str, str]],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        refs = self._assert_refs_exist(result_refs)
        if not refs:
            raise ValidationError("result_refs must contain at least one reference")
        subject = {"kind": "delegation", "id": delegation_id}
        return self.record_event(
            "delegation.succeeded",
            {
                "delegation_id": delegation_id,
                "expected_revision": expected_revision,
                "summary": summary,
                "result_refs": refs,
            },
            idempotency_key=self._idempotency_key("delegation.succeeded", idempotency_key),
            relations=tuple(self._relation(subject, "produced", ref) for ref in refs),
            tags=("delegation",),
        )

    def fail_delegation(
        self,
        delegation_id: str,
        expected_revision: str,
        *,
        reason_code: str,
        summary: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self._delegation_transition(
            delegation_id,
            expected_revision,
            "failed",
            reason_code=reason_code,
            summary=summary,
            idempotency_key=idempotency_key,
        )

    def cancel_delegation(
        self,
        delegation_id: str,
        expected_revision: str,
        *,
        reason: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self._delegation_transition(
            delegation_id,
            expected_revision,
            "cancelled",
            reason=reason,
            idempotency_key=idempotency_key,
        )

    def recover_delegation(
        self,
        delegation_id: str,
        expected_revision: str,
        *,
        reason: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Terminalize requested work whose original requester is unavailable."""

        return self._delegation_transition(
            delegation_id,
            expected_revision,
            "recovered",
            reason=reason,
            idempotency_key=idempotency_key,
        )

    def time_out_delegation(
        self,
        delegation_id: str,
        expected_revision: str,
        *,
        summary: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self._delegation_transition(
            delegation_id,
            expected_revision,
            "timed_out",
            summary=summary,
            idempotency_key=idempotency_key,
        )

    def mark_delegation_needs_operator(
        self,
        delegation_id: str,
        expected_revision: str,
        *,
        reason_code: str,
        summary: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self._delegation_transition(
            delegation_id,
            expected_revision,
            "needs_operator",
            reason_code=reason_code,
            summary=summary,
            idempotency_key=idempotency_key,
        )
