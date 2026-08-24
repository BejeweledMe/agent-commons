"""Task commands mixed into the universal workspace manager."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agent_commons.core.refs import normalize_ref
from agent_commons.domain.acceptance import select_qualifying_review
from agent_commons.domain.lifecycle import entity
from agent_commons.domain.projection import SEMANTICS_SENSITIVE_EVENTS
from agent_commons.errors import LifecycleConflictError, ValidationError
from agent_commons.storage import EventRecord

from ._validation import _nonempty_list, _optional_list


class TaskCommands:
    """Commands for task lifecycle and independent acceptance."""

    def create_task(
        self,
        *,
        title: str,
        description: str,
        acceptance_criteria: Sequence[str],
        priority: str = "normal",
        dependencies: Sequence[str] = (),
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = self._idempotency_key("task.created", idempotency_key)
        task_id = self._new_entity_id("task", "task.created", key)
        dependency_ids = _optional_list(dependencies, "dependencies")
        subject = {"kind": "task", "id": task_id}
        relations = [
            self._relation(subject, "depends_on", {"kind": "task", "id": dependency})
            for dependency in dependency_ids
        ]
        return self.record_event(
            "task.created",
            {
                "task_id": task_id,
                "title": title,
                "description": description,
                "acceptance_criteria": _nonempty_list(acceptance_criteria, "acceptance_criteria"),
                "priority": priority,
                "dependencies": dependency_ids,
            },
            idempotency_key=key,
            relations=relations,
            tags=("task",),
        )

    def list_tasks(self, *, state: str | None = None) -> list[dict[str, Any]]:
        return self._list("task", state=state)

    def revise_task(
        self,
        task_id: str,
        expected_revision: str,
        *,
        changes: Mapping[str, Any],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Revise task wording and move its immutable revision boundary."""

        key = self._idempotency_key("task.revised", idempotency_key)
        return self.record_event(
            "task.revised",
            {
                "task_id": task_id,
                "expected_revision": expected_revision,
                "changes": dict(changes),
            },
            idempotency_key=key,
            tags=("task",),
        )

    def _task_transition(
        self,
        task_id: str,
        expected_revision: str,
        action: str,
        *,
        idempotency_key: str | None = None,
        relations: Sequence[Mapping[str, Any]] = (),
        **fields: Any,
    ) -> dict[str, Any]:
        event_type = f"task.{action}"
        key = self._idempotency_key(event_type, idempotency_key)
        return self.record_event(
            event_type,
            {"task_id": task_id, "expected_revision": expected_revision, **fields},
            idempotency_key=key,
            relations=relations,
            tags=("task",),
        )

    def take_task(
        self, task_id: str, expected_revision: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        return self._task_transition(
            task_id,
            expected_revision,
            "taken",
            idempotency_key=idempotency_key,
            owner_session_id=self._active_session().session_id,
        )

    def start_task(self, task_id: str, expected_revision: str, **kwargs: Any) -> dict[str, Any]:
        return self._task_transition(task_id, expected_revision, "started", **kwargs)

    def block_task(
        self, task_id: str, expected_revision: str, *, reason: str, **kwargs: Any
    ) -> dict[str, Any]:
        return self._task_transition(task_id, expected_revision, "blocked", reason=reason, **kwargs)

    def unblock_task(
        self, task_id: str, expected_revision: str, *, resolution: str, **kwargs: Any
    ) -> dict[str, Any]:
        return self._task_transition(
            task_id, expected_revision, "unblocked", resolution=resolution, **kwargs
        )

    def complete_task(
        self,
        task_id: str,
        expected_revision: str,
        *,
        summary: str,
        artifact_refs: Sequence[Mapping[str, str]] = (),
        **kwargs: Any,
    ) -> dict[str, Any]:
        bindings = self._bind_evidence_refs(artifact_refs)
        refs = [dict(binding["ref"]) for binding in bindings]
        subject = {"kind": "task", "id": task_id}
        return self._task_transition(
            task_id,
            expected_revision,
            "completed",
            summary=summary,
            artifact_refs=refs,
            artifact_bindings=bindings,
            relations=[self._relation(subject, "depends_on", ref) for ref in refs],
            **kwargs,
        )

    def submit_task(
        self,
        task_id: str,
        expected_revision: str,
        *,
        summary: str,
        artifact_refs: Sequence[Mapping[str, str]] = (),
        **kwargs: Any,
    ) -> dict[str, Any]:
        bindings = self._bind_evidence_refs(artifact_refs)
        refs = [dict(binding["ref"]) for binding in bindings]
        subject = {"kind": "task", "id": task_id}
        return self._task_transition(
            task_id,
            expected_revision,
            "submitted",
            summary=summary,
            artifact_refs=refs,
            artifact_bindings=bindings,
            relations=[self._relation(subject, "depends_on", ref) for ref in refs],
            **kwargs,
        )

    def _require_ledger_semantics(self, event_type: str) -> None:
        """Stamp the ledger before a write whose replay needs newer semantics.

        The stamp rises exactly when a write starts depending on the newer
        behaviour — never earlier, so an untouched workspace stays readable by
        old code.  From then on a reader older than the stamp is told to
        update instead of misjudging the history it cannot replay.
        """

        needed = SEMANTICS_SENSITIVE_EVENTS.get(event_type, 1)
        if needed <= 1 or self.snapshot().semantics_required >= needed:
            return
        try:
            self.record_event(
                "workspace.semantics_required",
                {
                    "workspace_id": self.workspace_id,
                    "semantics_version": needed,
                    "reason": f"replay of {event_type} depends on semantics version {needed}",
                },
                idempotency_key=f"semantics-v{needed}",
                tags=("workspace",),
            )
        except LifecycleConflictError:
            # Another session raised the floor first; that is the outcome the
            # stamp exists for, not a failure of this write.
            if self.snapshot().semantics_required < needed:
                raise

    def accept_task(
        self, task_id: str, expected_revision: str, *, summary: str, **kwargs: Any
    ) -> dict[str, Any]:
        idempotency_key = kwargs.pop("idempotency_key", None)
        if kwargs:
            raise ValidationError(
                "unsupported task acceptance fields: " + ", ".join(sorted(kwargs))
            )
        self._require_ledger_semantics("task.accepted")
        key = self._idempotency_key("task.accepted", idempotency_key)
        session = self._active_session()
        namespace = self._namespace(session)
        reservation = self.events.idempotency.lookup(namespace=namespace, key=key)
        existing: EventRecord | None = None
        if reservation is not None:
            try:
                existing = self.events.get(reservation.event_id)
            except FileNotFoundError:
                pass
        else:
            existing = self._event_for_idempotency_identity(namespace, key)
        if existing is not None and existing.event.get("event_type") == "task.accepted":
            stored_payload = existing.event.get("payload") or {}
            stored_binding = stored_payload.get("acceptance_review")
            if isinstance(stored_binding, Mapping):
                acceptance_review = dict(stored_binding)
                return self.record_event(
                    "task.accepted",
                    {
                        "task_id": task_id,
                        "expected_revision": expected_revision,
                        "summary": summary,
                        "acceptance_review": acceptance_review,
                    },
                    idempotency_key=key,
                    relations=(
                        self._relation(
                            {"kind": "task", "id": task_id},
                            "depends_on",
                            normalize_ref(acceptance_review["ref"]),
                        ),
                    ),
                    tags=("task", "truth"),
                )
        snapshot = self.snapshot()
        task = entity(snapshot, "task", task_id)
        if task is None:
            raise LifecycleConflictError(f"task does not exist: {task_id}")
        selected = select_qualifying_review(snapshot, task_id)
        if selected is None:
            raise LifecycleConflictError(
                "task acceptance requires a current approved independent review"
            )
        selected_ref = {"kind": "review", "id": str(selected["id"])}
        acceptance_review = {
            "ref": selected_ref,
            "revision": str(selected.get("effective_revision") or selected.get("revision")),
        }
        return self.record_event(
            "task.accepted",
            {
                "task_id": task_id,
                "expected_revision": expected_revision,
                "summary": summary,
                "acceptance_review": acceptance_review,
            },
            idempotency_key=key,
            relations=(
                self._relation(
                    {"kind": "task", "id": task_id},
                    "depends_on",
                    selected_ref,
                ),
            ),
            tags=("task", "truth"),
        )

    def cancel_task(
        self, task_id: str, expected_revision: str, *, reason: str, **kwargs: Any
    ) -> dict[str, Any]:
        return self._task_transition(
            task_id, expected_revision, "cancelled", reason=reason, **kwargs
        )

    def reopen_task(
        self, task_id: str, expected_revision: str, *, reason: str, **kwargs: Any
    ) -> dict[str, Any]:
        return self._task_transition(
            task_id, expected_revision, "reopened", reason=reason, **kwargs
        )
