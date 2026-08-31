"""Narrow publish/revise/read service for canonical Design Packages."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from agent_commons.domain.design_packages import (
    DesignPackageDraft,
    DesignPackageRecord,
    DesignPackageRefusal,
    DesignPackageRefusalCode,
    ScreenBinding,
)
from agent_commons.domain.lifecycle import entity
from agent_commons.domain.snapshot import ProjectSnapshot
from agent_commons.errors import (
    IntegrityError,
    LifecycleConflictError,
    SecurityPolicyError,
    ValidationError,
)


class _DesignPackageManager(Protocol):
    policy: Any
    events: Any
    design_package_writes_enabled: bool

    def snapshot(self) -> ProjectSnapshot: ...

    def _idempotency_key(self, event_type: str, value: str | None) -> str: ...

    def _active_session(self) -> Any: ...

    def _namespace(self, session: Any) -> str: ...

    def _event_for_idempotency_identity(self, namespace: str, key: str) -> Any: ...

    def _new_entity_id(self, kind: str, event_type: str, key: str) -> str: ...

    def get_artifact_bundle(self, artifact_id: str) -> dict[str, Any]: ...

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


class DesignPackageCommands:
    """Canonical Design Package collaborator owned by ``CommonsManager``."""

    def __init__(self, manager: _DesignPackageManager) -> None:
        self._manager = manager

    def list(self) -> tuple[DesignPackageRecord, ...]:
        snapshot = self._manager.snapshot()
        return tuple(snapshot.design_packages[key] for key in sorted(snapshot.design_packages))

    def get(self, design_package_id: str, *, revision: str | None = None) -> DesignPackageRecord:
        snapshot = self._manager.snapshot()
        current = snapshot.design_packages.get(design_package_id)
        if current is None:
            raise DesignPackageRefusal(
                DesignPackageRefusalCode.MISSING,
                "The requested Design Package does not exist.",
                "Publish a Design Package or refresh the available packages.",
            )
        if revision is None:
            return current
        exact = snapshot.design_package_revisions.get((design_package_id, revision))
        if exact is None:
            raise DesignPackageRefusal(
                DesignPackageRefusalCode.STALE,
                "The requested Design Package revision is not effective.",
                "Refresh available revisions and select an exact current revision.",
            )
        return exact

    def publish(
        self,
        draft: DesignPackageDraft | Mapping[str, object],
        *,
        idempotency_key: str | None = None,
    ) -> DesignPackageRecord:
        self._require_writes_enabled()
        normalized = self._normalize_draft(draft)
        key = self._manager._idempotency_key("design_package.created", idempotency_key)
        package_id = self._manager._new_entity_id("design_package", "design_package.created", key)
        result = self._record(
            "design_package.created",
            {"design_package_id": package_id, **normalized.to_payload()},
            normalized,
            key=key,
        )
        return self.get(package_id, revision=str(result["revision"]))

    def revise(
        self,
        design_package_id: str,
        expected_revision: str,
        draft: DesignPackageDraft | Mapping[str, object],
        *,
        idempotency_key: str | None = None,
    ) -> DesignPackageRecord:
        self._require_writes_enabled()
        normalized = self._normalize_draft(draft)
        key = self._manager._idempotency_key("design_package.revised", idempotency_key)
        payload = {
            "design_package_id": design_package_id,
            "expected_revision": expected_revision,
            **normalized.to_payload(),
        }
        if not self._has_idempotent_record(key):
            current = self.get(design_package_id)
            if current.revision != expected_revision:
                raise DesignPackageRefusal(
                    DesignPackageRefusalCode.STALE,
                    "The Design Package changed before this revision was published.",
                    "Reload the latest revision and retry with its exact revision.",
                )
        result = self._record("design_package.revised", payload, normalized, key=key)
        return self.get(design_package_id, revision=str(result["revision"]))

    def _record(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        draft: DesignPackageDraft,
        *,
        key: str,
    ) -> dict[str, Any]:
        if not self._has_idempotent_record(key):
            self._validate_bindings(draft, self._manager.snapshot())
        subject = {"kind": "design_package", "id": str(payload["design_package_id"])}
        relation_targets: list[tuple[str, str]] = []
        for screen in draft.screens:
            relation_targets.extend(
                (
                    ("artifact", screen.artifact_binding.identifier),
                    ("task", screen.producer_task_binding.identifier),
                )
            )
        relations = tuple(
            self._manager._relation(
                subject,
                "depends_on",
                {"kind": kind, "id": identifier},
            )
            for kind, identifier in dict.fromkeys(relation_targets)
        )
        try:
            return self._manager.record_event(
                event_type,
                payload,
                idempotency_key=key,
                relations=relations,
                tags=("design_package",),
            )
        except LifecycleConflictError as exc:
            refusal = self._post_lock_race_refusal(event_type, payload, draft)
            if refusal is not None:
                raise refusal from exc
            raise

    def _require_writes_enabled(self) -> None:
        if self._manager.design_package_writes_enabled:
            return
        raise DesignPackageRefusal(
            DesignPackageRefusalCode.UNAVAILABLE,
            "Design Package publishing is disabled by operator configuration.",
            "Enable Design Package writes explicitly to publish or revise packages.",
        )

    def _has_idempotent_record(self, key: str) -> bool:
        session = self._manager._active_session()
        namespace = self._manager._namespace(session)
        return (
            self._manager.events.idempotency.lookup(namespace=namespace, key=key) is not None
            or self._manager._event_for_idempotency_identity(namespace, key) is not None
        )

    def _normalize_draft(
        self, draft: DesignPackageDraft | Mapping[str, object]
    ) -> DesignPackageDraft:
        try:
            raw = draft.to_payload() if isinstance(draft, DesignPackageDraft) else draft
            if not isinstance(raw, Mapping):
                raise ValidationError("design package draft must be an object")
            normalized = DesignPackageDraft.from_payload(raw)
            self._manager.policy.assert_safe(
                normalized.to_payload(), context="Design Package content"
            )
            return normalized
        except SecurityPolicyError as exc:
            raise DesignPackageRefusal(
                DesignPackageRefusalCode.UNSAFE,
                "The Design Package contains data forbidden by workspace security policy.",
                "Remove secrets or restricted personal data and publish a new draft.",
            ) from exc
        except ValidationError as exc:
            message = str(exc)
            oversized = "at most" in message or "exceeds" in message or "1 to 64" in message
            raise DesignPackageRefusal(
                (
                    DesignPackageRefusalCode.OVERSIZED
                    if oversized
                    else DesignPackageRefusalCode.INVALID
                ),
                (
                    "The Design Package exceeds a configured bound."
                    if oversized
                    else "The Design Package does not satisfy the canonical contract."
                ),
                (
                    "Reduce the package to the documented bounds and retry."
                    if oversized
                    else "Fix its ordered screens and exact bindings, then retry."
                ),
            ) from exc

    def _validate_bindings(self, draft: DesignPackageDraft, snapshot: ProjectSnapshot) -> None:
        producer_session_id = str(self._manager._active_session().session_id)
        for screen in draft.screens:
            self._validate_screen(screen, snapshot, producer_session_id)

    def _validate_screen(
        self,
        screen: ScreenBinding,
        snapshot: ProjectSnapshot,
        producer_session_id: str,
    ) -> None:
        artifact = self._exact_entity(
            snapshot,
            screen.artifact_binding.kind,
            screen.artifact_binding.identifier,
            screen.artifact_binding.revision,
            label="artifact",
        )
        task = self._exact_entity(
            snapshot,
            screen.producer_task_binding.kind,
            screen.producer_task_binding.identifier,
            screen.producer_task_binding.revision,
            label="producer task",
        )
        if artifact.get("content_revision") != screen.artifact_content_revision:
            raise DesignPackageRefusal(
                DesignPackageRefusalCode.STALE,
                "A screen artifact content revision has changed.",
                "Re-register the screen and publish a package with the exact current hash.",
            )
        if artifact.get("classification") != screen.classification:
            raise DesignPackageRefusal(
                DesignPackageRefusalCode.UNSAFE,
                "A screen artifact classification does not match its package binding.",
                "Review the artifact classification and publish a new exact binding.",
            )
        if screen.classification not in {"public", "internal"}:
            raise DesignPackageRefusal(
                DesignPackageRefusalCode.UNSAFE,
                "A screen artifact is not eligible for Gallery preview.",
                "Use a public or internal PNG/JPEG artifact.",
            )
        if artifact.get("manifest_ref") not in snapshot.known_manifest_ids:
            raise DesignPackageRefusal(
                DesignPackageRefusalCode.MISSING,
                "A screen artifact has no effective authorized manifest.",
                "Restore the manifest or register a new exact artifact revision.",
            )
        artifact_actor = snapshot.entity_revision_actor(
            "artifact",
            screen.artifact_binding.identifier,
            screen.artifact_binding.revision,
        )
        task_actor = snapshot.entity_revision_actor(
            "task",
            screen.producer_task_binding.identifier,
            screen.producer_task_binding.revision,
        )
        if producer_session_id != artifact_actor or producer_session_id != task_actor:
            raise DesignPackageRefusal(
                DesignPackageRefusalCode.UNAUTHORIZED,
                "The current session is not the actor of both exact bound revisions.",
                "Ask the exact artifact and task revision producer to publish a coherent package.",
            )
        expected_artifact_binding = screen.artifact_binding.to_payload()
        if expected_artifact_binding not in tuple(task.get("artifact_bindings") or ()):
            raise DesignPackageRefusal(
                DesignPackageRefusalCode.UNAUTHORIZED,
                "The producing task does not bind the exact screen artifact revision.",
                "Complete the task with that artifact revision before publishing the package.",
            )
        self._verify_preview(screen)

    @staticmethod
    def _exact_entity(
        snapshot: ProjectSnapshot,
        kind: str,
        identifier: str,
        revision: str,
        *,
        label: str,
    ) -> Mapping[str, Any]:
        try:
            current = entity(snapshot, kind, identifier)
        except ValidationError as exc:
            raise DesignPackageRefusal(
                DesignPackageRefusalCode.INVALID,
                f"The {label} binding uses an unsupported canonical kind.",
                "Use the documented artifact and task binding kinds.",
            ) from exc
        if current is None:
            raise DesignPackageRefusal(
                DesignPackageRefusalCode.MISSING,
                f"The bound {label} does not exist.",
                "Restore it or publish a package without this screen.",
            )
        current_revision = str(current.get("effective_revision") or current.get("revision") or "")
        if current_revision != revision:
            raise DesignPackageRefusal(
                DesignPackageRefusalCode.STALE,
                f"The bound {label} revision has changed.",
                "Review the current revision and publish a new exact binding.",
            )
        return current

    def _verify_preview(self, screen: ScreenBinding) -> None:
        # Delayed import avoids making the manager/artifact-reader composition
        # edge cyclic at module import time.  The reader remains the one source
        # of filesystem, MIME, byte, pixel and no-follow preview policy.
        from .artifact_content import ArtifactPreviewReader, ArtifactPreviewRefusal

        try:
            preview = ArtifactPreviewReader(self._manager).read(screen.artifact_binding.identifier)
        except ArtifactPreviewRefusal as exc:
            stale_codes = {
                "artifact_preview_not_found",
                "artifact_preview_missing_source",
                "artifact_preview_stale_source",
            }
            code = (
                DesignPackageRefusalCode.STALE
                if exc.code in stale_codes
                else DesignPackageRefusalCode.UNSAFE
            )
            raise DesignPackageRefusal(
                code,
                "The screen artifact cannot pass the verified preview policy.",
                "Repair or re-register the PNG/JPEG artifact, then publish a new binding.",
            ) from exc
        except (IntegrityError, LifecycleConflictError, ValidationError) as exc:
            raise DesignPackageRefusal(
                DesignPackageRefusalCode.UNSAFE,
                "The screen artifact cannot be verified safely.",
                "Repair or re-register the artifact, then publish a new binding.",
            ) from exc
        if preview.revision != screen.artifact_content_revision:
            raise DesignPackageRefusal(
                DesignPackageRefusalCode.STALE,
                "The verified preview hash does not match the package binding.",
                "Publish a new binding for the current verified artifact revision.",
            )
        if preview.media_type != screen.media_type:
            raise DesignPackageRefusal(
                DesignPackageRefusalCode.UNSAFE,
                "The verified preview type does not match the package binding.",
                "Use the detected PNG/JPEG media type in a new package revision.",
            )

    def _post_lock_race_refusal(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        draft: DesignPackageDraft,
    ) -> DesignPackageRefusal | None:
        snapshot = self._manager.snapshot()
        if event_type == "design_package.revised":
            current = snapshot.design_packages.get(str(payload["design_package_id"]))
            if current is None:
                return DesignPackageRefusal(
                    DesignPackageRefusalCode.MISSING,
                    "The Design Package disappeared before revision.",
                    "Refresh available packages and retry.",
                )
            if current.revision != payload.get("expected_revision"):
                return DesignPackageRefusal(
                    DesignPackageRefusalCode.STALE,
                    "The Design Package changed before revision.",
                    "Reload the latest revision and retry with its exact revision.",
                )
        try:
            self._validate_bindings(draft, snapshot)
        except DesignPackageRefusal as refusal:
            return refusal
        return None
