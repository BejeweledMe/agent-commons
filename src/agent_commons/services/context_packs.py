"""Narrow publish/revise/read/compile service for canonical Context Packs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from agent_commons.domain.context_pack import (
    ContextPackDraft,
    ContextPackRecord,
    ContextPackRefusal,
    ContextPackRefusalCode,
)
from agent_commons.domain.lifecycle import entity
from agent_commons.domain.snapshot import ProjectSnapshot
from agent_commons.errors import LifecycleConflictError, SecurityPolicyError, ValidationError

from .context_compiler import CompiledContext, ContextCompiler


class _ContextPackManager(Protocol):
    policy: Any
    events: Any
    context_compiler: ContextCompiler
    context_pack_writes_enabled: bool

    def snapshot(self) -> ProjectSnapshot: ...

    def _idempotency_key(self, event_type: str, value: str | None) -> str: ...

    def _active_session(self) -> Any: ...

    def _require_ledger_semantics(self, event_type: str) -> None: ...

    def _namespace(self, session: Any) -> str: ...

    def _event_for_idempotency_identity(self, namespace: str, key: str) -> Any: ...

    def _new_entity_id(self, kind: str, event_type: str, key: str) -> str: ...

    @staticmethod
    def _relation(
        subject: Mapping[str, str], predicate: str, object_ref: Mapping[str, str]
    ) -> dict[str, Any]: ...

    def record_event(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        idempotency_key: str,
        relations: Sequence[Mapping[str, Any]] = (),
        tags: Sequence[str] = (),
    ) -> dict[str, Any]: ...


class ContextPackCommands:
    """Canonical Context Pack collaborator owned by ``CommonsManager``."""

    def __init__(self, manager: _ContextPackManager) -> None:
        self._manager = manager

    def list(self) -> tuple[ContextPackRecord, ...]:
        snapshot = self._manager.snapshot()
        return tuple(snapshot.context_packs[key] for key in sorted(snapshot.context_packs))

    def get(self, context_pack_id: str, *, revision: str | None = None) -> ContextPackRecord:
        snapshot = self._manager.snapshot()
        current = snapshot.context_packs.get(context_pack_id)
        if current is None:
            raise ContextPackRefusal(
                ContextPackRefusalCode.MISSING,
                "The requested Context Pack does not exist.",
                "Publish a Context Pack or select fresh context.",
            )
        if revision is None:
            return current
        exact = snapshot.context_pack_revisions.get((context_pack_id, revision))
        if exact is None:
            raise ContextPackRefusal(
                ContextPackRefusalCode.STALE,
                "The requested Context Pack revision is not effective.",
                "Refresh available revisions or select fresh context.",
            )
        return exact

    def publish(
        self,
        draft: ContextPackDraft | Mapping[str, object],
        *,
        idempotency_key: str | None = None,
    ) -> ContextPackRecord:
        self._require_writes_enabled()
        normalized = self._normalize_draft(draft)
        key = self._manager._idempotency_key("context_pack.created", idempotency_key)
        context_pack_id = self._manager._new_entity_id("context_pack", "context_pack.created", key)
        payload = {"context_pack_id": context_pack_id, **normalized.to_payload()}
        result = self._record(
            "context_pack.created",
            payload,
            normalized,
            key=key,
        )
        return self.get(context_pack_id, revision=str(result["revision"]))

    def revise(
        self,
        context_pack_id: str,
        expected_revision: str,
        draft: ContextPackDraft | Mapping[str, object],
        *,
        idempotency_key: str | None = None,
    ) -> ContextPackRecord:
        self._require_writes_enabled()
        normalized = self._normalize_draft(draft)
        key = self._manager._idempotency_key("context_pack.revised", idempotency_key)
        payload = {
            "context_pack_id": context_pack_id,
            "expected_revision": expected_revision,
            **normalized.to_payload(),
        }
        if not self._has_idempotent_record(key):
            current = self.get(context_pack_id)
            if current.revision != expected_revision:
                raise ContextPackRefusal(
                    ContextPackRefusalCode.STALE,
                    "The Context Pack changed before this revision was published.",
                    "Reload the latest revision and retry with its exact revision.",
                )
        result = self._record(
            "context_pack.revised",
            payload,
            normalized,
            key=key,
        )
        return self.get(context_pack_id, revision=str(result["revision"]))

    def compile(self, context_pack_id: str, revision: str) -> CompiledContext:
        record = self.get(context_pack_id, revision=revision)
        self._validate_references(record.draft, self._manager.snapshot())
        return self._manager.context_compiler.compile(record)

    def _record(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        draft: ContextPackDraft,
        *,
        key: str,
    ) -> dict[str, Any]:
        if not self._has_idempotent_record(key):
            self._validate_references(draft, self._manager.snapshot())
        self._manager._require_ledger_semantics(event_type)
        subject = {"kind": "context_pack", "id": str(payload["context_pack_id"])}
        references = (*draft.source_refs, *draft.decision_refs)
        relations = tuple(
            self._manager._relation(
                subject,
                "depends_on",
                {"kind": ref.kind, "id": ref.identifier},
            )
            for ref in dict.fromkeys(references)
        )
        try:
            return self._manager.record_event(
                event_type,
                payload,
                idempotency_key=key,
                relations=relations,
                tags=("context_pack",),
            )
        except LifecycleConflictError as exc:
            refusal = self._post_lock_race_refusal(event_type, payload, draft)
            if refusal is not None:
                raise refusal from exc
            raise

    def _require_writes_enabled(self) -> None:
        if self._manager.context_pack_writes_enabled:
            return
        raise ContextPackRefusal(
            ContextPackRefusalCode.UNAVAILABLE,
            "Context Pack publishing is disabled by operator configuration.",
            "Enable Context Pack writes explicitly or continue with fresh context.",
        )

    def _post_lock_race_refusal(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        draft: ContextPackDraft,
    ) -> ContextPackRefusal | None:
        """Classify only state changes that can race the pre-lock checks."""

        snapshot = self._manager.snapshot()
        if event_type == "context_pack.revised":
            current = snapshot.context_packs.get(str(payload["context_pack_id"]))
            if current is None:
                return ContextPackRefusal(
                    ContextPackRefusalCode.MISSING,
                    "The Context Pack disappeared before the revision was recorded.",
                    "Refresh available Context Packs or select fresh context.",
                )
            if current.revision != payload.get("expected_revision"):
                return ContextPackRefusal(
                    ContextPackRefusalCode.STALE,
                    "The Context Pack changed before the revision was recorded.",
                    "Reload the latest revision and retry with its exact revision.",
                )
        for ref in (*draft.source_refs, *draft.decision_refs):
            try:
                current_source = entity(snapshot, ref.kind, ref.identifier)
            except ValidationError:
                return None
            if current_source is None:
                return ContextPackRefusal(
                    ContextPackRefusalCode.MISSING,
                    "A Context Pack source disappeared before publication.",
                    "Restore the source or publish a new draft without it.",
                )
            current_revision = str(
                current_source.get("effective_revision") or current_source.get("revision") or ""
            )
            if current_revision != ref.revision:
                return ContextPackRefusal(
                    ContextPackRefusalCode.STALE,
                    "A Context Pack source changed before publication.",
                    "Review the new source revision and retry with an updated draft.",
                )
            if ref.kind == "artifact" and current_source.get("classification") == "restricted":
                return ContextPackRefusal(
                    ContextPackRefusalCode.UNSAFE,
                    "A Context Pack source became restricted before publication.",
                    "Use an authorized public or internal source, or select fresh context.",
                )
        return None

    def _has_idempotent_record(self, key: str) -> bool:
        session = self._manager._active_session()
        namespace = self._manager._namespace(session)
        return (
            self._manager.events.idempotency.lookup(namespace=namespace, key=key) is not None
            or self._manager._event_for_idempotency_identity(namespace, key) is not None
        )

    def _normalize_draft(self, draft: ContextPackDraft | Mapping[str, object]) -> ContextPackDraft:
        try:
            if isinstance(draft, ContextPackDraft):
                raw = draft.to_payload()
            elif isinstance(draft, Mapping):
                raw = draft
            else:
                raise ValidationError("context pack draft must be an object")
            normalized = ContextPackDraft.from_payload(raw)
            self._manager.policy.assert_safe(
                normalized.to_payload(), context="Context Pack content"
            )
            return normalized
        except SecurityPolicyError as exc:
            raise ContextPackRefusal(
                ContextPackRefusalCode.UNSAFE,
                "The Context Pack contains data forbidden by workspace security policy.",
                "Remove secrets or restricted personal data and publish a new draft.",
            ) from exc
        except ValidationError as exc:
            message = str(exc)
            oversized = "at most" in message or "exceeds" in message
            raise ContextPackRefusal(
                (ContextPackRefusalCode.OVERSIZED if oversized else ContextPackRefusalCode.INVALID),
                (
                    "The Context Pack exceeds a configured bound."
                    if oversized
                    else "The Context Pack does not satisfy the canonical contract."
                ),
                "Reduce the draft to the documented bounds and retry."
                if oversized
                else "Fix the draft shape and exact references, then retry.",
            ) from exc

    @staticmethod
    def _validate_references(draft: ContextPackDraft, snapshot: ProjectSnapshot) -> None:
        for ref in (*draft.source_refs, *draft.decision_refs):
            try:
                current = entity(snapshot, ref.kind, ref.identifier)
            except ValidationError as exc:
                raise ContextPackRefusal(
                    ContextPackRefusalCode.INVALID,
                    "The Context Pack contains an unsupported canonical reference.",
                    "Use an allowed revision-bound source or decision reference.",
                ) from exc
            if current is None:
                raise ContextPackRefusal(
                    ContextPackRefusalCode.MISSING,
                    "A referenced Context Pack source does not exist.",
                    "Publish or restore the source, or remove it in a new pack revision.",
                )
            current_revision = str(
                current.get("effective_revision") or current.get("revision") or ""
            )
            if current_revision != ref.revision:
                raise ContextPackRefusal(
                    ContextPackRefusalCode.STALE,
                    "A referenced Context Pack source has changed.",
                    "Review the source and publish a new Context Pack revision.",
                )
            if ref.kind == "artifact" and current.get("classification") == "restricted":
                raise ContextPackRefusal(
                    ContextPackRefusalCode.UNSAFE,
                    "A restricted artifact cannot be used as Context Pack input.",
                    "Use an authorized public or internal source, or select fresh context.",
                )
            if (
                ref.kind == "artifact"
                and current.get("manifest_ref") not in snapshot.known_manifest_ids
            ):
                raise ContextPackRefusal(
                    ContextPackRefusalCode.MISSING,
                    "A referenced artifact has no effective authorized manifest.",
                    "Restore the artifact manifest or publish a pack without that source.",
                )
