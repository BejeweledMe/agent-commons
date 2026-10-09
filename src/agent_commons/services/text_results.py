"""Builder-only retained text results and explicit, revision-bound handoff phases."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from typing import Any

from agent_commons.core.canonical import canonical_sha256
from agent_commons.core.ids import is_typed_id
from agent_commons.domain.snapshot import ProjectSnapshot
from agent_commons.domain.states import LIVE_WORKER_DELEGATION_STATES
from agent_commons.errors import IdempotencyConflictError, LifecycleConflictError, ValidationError
from agent_commons.services.generated_outputs import TEXT_KIND, generated_series
from agent_commons.services.output_content import OutputContentStore

TEXT_MEDIA = "text/plain; charset=utf-8"
MAX_TEXT_BYTES = 64 * 1024
REFUSAL = "Text result handoff is unavailable for this worker and task."


def validate_text(value: Any, maximum: int, *, multiline: bool = True) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.encode("utf-8")) > maximum
        or any(ord(c) < 32 and (not multiline or c not in "\n\t") or ord(c) == 127 for c in value)
    ):
        raise ValidationError("Text result input exceeds its safe bounds.")
    return value


def validate_report(title: Any, summary: Any, checks: Any) -> None:
    validate_text(title, 256, multiline=False)
    validate_text(summary, 4096)
    if not isinstance(checks, list) or len(checks) > 32:
        raise ValidationError("Text result checks exceed their bounds.")
    for check in checks:
        validate_text(check, 512)


class WorkerTextResults:
    def __init__(self, manager: Any, binding: Mapping[str, Any] | None):
        self.manager = manager
        self.bound = dict(binding or {})
        self.bound["target_ref"] = dict(self.bound.get("target_ref") or {})

    def _context(self, *, terminal_retry: bool = False):
        manager = self.manager
        if manager.read_only:
            raise LifecycleConflictError(REFUSAL)
        session = manager.sessions.require_active(manager.session_id)
        snapshot = manager.snapshot()
        if (
            not isinstance(snapshot, ProjectSnapshot)
            or snapshot.workspace_id != manager.workspace_id
        ):
            raise LifecycleConflictError(REFUSAL)
        current = snapshot.delegations.get(self.bound.get("id"))
        if (
            current is None
            or current.get("child_session_id") != session.session_id
            or current.get("purpose") != "implementation"
            or current.get("agent_id") not in snapshot.agents
            or any(
                current.get(f) != self.bound.get(f)
                for f in ("id", "agent_id", "target_ref", "target_revision", "purpose")
            )
            or (
                current.get("state") not in LIVE_WORKER_DELEGATION_STATES
                and not (terminal_retry and current.get("state") == "succeeded")
            )
        ):
            raise LifecycleConflictError(REFUSAL)
        target = current.get("target_ref") or {}
        task = snapshot.tasks.get(target.get("id")) if target.get("kind") == "task" else None
        if task is None:
            raise LifecycleConflictError(REFUSAL)
        return snapshot, current, task

    def _key(self, phase: str, key: str) -> str:
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{7,79}", key):
            raise ValidationError("Text result operation identity is invalid.")
        return (
            "text-result-"
            + phase
            + "-"
            + hashlib.sha256(f"{self.bound.get('id')}:{key}".encode()).hexdigest()
        )

    def _existing(self, key: str):
        manager = self.manager
        return manager._event_for_idempotency_identity(
            manager._namespace(manager._active_session()), key
        )

    def publish(
        self,
        *,
        title: str,
        summary: str,
        checks: list[str],
        content: str,
        expected_task_revision: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        validate_report(title, summary, checks)
        validate_text(content, MAX_TEXT_BYTES)
        manager = self.manager
        manager.policy.assert_safe(
            {"title": title, "summary": summary, "checks": checks, "content": content},
            context="text result",
        )
        key = self._key("publish", idempotency_key)
        raw = content.encode("utf-8")
        retained = {"revision": "sha256:" + hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}
        with manager._canonical_write_lock():
            snapshot, current, task = self._context()
            artifact_id = manager._new_entity_id("artifact", "artifact.registered", key)
            source = f"results/{artifact_id}.txt"
            metadata = {
                "output_kind": TEXT_KIND,
                "output_task": {"id": task["id"], "revision": expected_task_revision},
                "output_delegation": {
                    "id": current["id"],
                    "revision": snapshot.entity_revision("delegation", current["id"]),
                },
                "series_id": generated_series(current["id"], source),
                "title": title,
                "summary": summary,
                "checks": list(checks),
                "retained_content": retained,
            }
            existing = snapshot.artifacts.get(artifact_id)
            if existing is not None:
                old = manager.manifests.get(existing["manifest_ref"]).manifest
                original = old.get("metadata") or {}
                metadata["output_delegation"] = original.get("output_delegation")
                if (
                    original != metadata
                    or old.get("revision") != retained["revision"]
                    or snapshot.entity_revision_actor(
                        "artifact", artifact_id, snapshot.entity_revision("artifact", artifact_id)
                    )
                    != manager.session_id
                ):
                    raise IdempotencyConflictError(
                        "Text result retry differs from its publication."
                    )
            elif (
                expected_task_revision != current["target_revision"]
                or snapshot.entity_revision("task", task["id"]) != expected_task_revision
                or task.get("state") != "active"
            ):
                raise LifecycleConflictError(REFUSAL)
            manifest = {
                "schema": "commons.manifest.artifact.v1",
                "kind": "artifact",
                "artifact_id": artifact_id,
                "revision": retained["revision"],
                "source": {"path": source},
                "media_type": TEXT_MEDIA,
                "size_bytes": len(raw),
                "classification": "internal",
                "captured": False,
                "metadata": metadata,
            }
            manager.schemas.validate_manifest(manifest)
            manifest_id = "mft.artifact.sha256." + canonical_sha256(manifest)
            OutputContentStore(manager).put(raw)
            # Always replay the identical canonical operation, including after an event
            # append succeeded but its receipt/final response was interrupted.
            result = manager.record_event(
                "artifact.registered",
                {
                    "artifact_id": artifact_id,
                    "manifest_ref": manifest_id,
                    "revision": retained["revision"],
                    "classification": "internal",
                },
                idempotency_key=key,
                relations=(
                    manager._relation(
                        {"kind": "artifact", "id": artifact_id},
                        "uses",
                        {"kind": "manifest", "id": manifest_id},
                    ),
                ),
                tags=("artifact",),
                _manifest=manifest,
            )
            return {
                "schema": "agent_commons.text-result.v1",
                "kind": "text_result",
                "artifact_id": artifact_id,
                "artifact_revision": result["revision"],
                "content_revision": retained["revision"],
                "task_id": task["id"],
                "task_revision": expected_task_revision,
                "producer_agent_id": current["agent_id"],
                "producer_delegation_id": current["id"],
                "delegation_revision": metadata["output_delegation"]["revision"],
                "content_copied": True,
            }

    def transition(
        self,
        phase: str,
        *,
        expected_task_revision: str,
        summary: str,
        artifact_ids: list[str],
        idempotency_key: str,
    ) -> dict[str, Any]:
        if phase not in {"complete", "submit"}:
            raise ValidationError("Unsupported text result phase.")
        validate_text(summary, 4096)
        if (
            not isinstance(artifact_ids, list)
            or not 1 <= len(artifact_ids) <= 64
            or any(not is_typed_id(identifier, "artifact") for identifier in artifact_ids)
            or len(set(artifact_ids)) != len(artifact_ids)
        ):
            raise ValidationError("Text result evidence is invalid.")
        manager = self.manager
        key = self._key(phase, idempotency_key)
        event_type = "task.completed" if phase == "complete" else "task.submitted"
        with manager._canonical_write_lock():
            snapshot, current, task = self._context()
            refs = [{"kind": "artifact", "id": identifier} for identifier in artifact_ids]
            previous = self._existing(key)
            if previous is not None:
                payload = previous.event["payload"]
                if (
                    previous.event["event_type"] != event_type
                    or payload.get("task_id") != task["id"]
                    or payload.get("expected_revision") != expected_task_revision
                    or payload.get("summary") != summary
                    or payload.get("artifact_refs") != refs
                ):
                    raise IdempotencyConflictError("Text result retry differs from its handoff.")
            else:
                if snapshot.entity_revision("task", task["id"]) != expected_task_revision:
                    raise LifecycleConflictError(REFUSAL)
                if phase == "complete":
                    if expected_task_revision != current["target_revision"]:
                        raise LifecycleConflictError(REFUSAL)
                elif (
                    task.get("state") != "completed"
                    or snapshot.entity_revision_actor("task", task["id"], expected_task_revision)
                    != manager.session_id
                    or task.get("expected_revision") != current["target_revision"]
                ):
                    raise LifecycleConflictError(REFUSAL)
                bindings = []
                for identifier in artifact_ids:
                    artifact = snapshot.artifacts.get(identifier)
                    revision = snapshot.entity_revision("artifact", identifier)
                    if (
                        artifact is None
                        or snapshot.entity_revision_actor("artifact", identifier, revision)
                        != manager.session_id
                    ):
                        raise LifecycleConflictError(REFUSAL)
                    metadata = (
                        manager.manifests.get(artifact["manifest_ref"]).manifest.get("metadata")
                        or {}
                    )
                    if (
                        metadata.get("output_task")
                        != {"id": task["id"], "revision": current["target_revision"]}
                        or (metadata.get("output_delegation") or {}).get("id") != current["id"]
                    ):
                        raise LifecycleConflictError(REFUSAL)
                    bindings.append(
                        {"ref": {"kind": "artifact", "id": identifier}, "revision": revision}
                    )
                if phase == "submit" and bindings != task.get("artifact_bindings"):
                    raise LifecycleConflictError(REFUSAL)
                payload = {
                    "task_id": task["id"],
                    "expected_revision": expected_task_revision,
                    "summary": summary,
                    "artifact_refs": refs,
                    "artifact_bindings": bindings,
                }
            return manager.record_event(
                event_type,
                payload,
                idempotency_key=key,
                relations=tuple(
                    manager._relation({"kind": "task", "id": task["id"]}, "depends_on", ref)
                    for ref in refs
                ),
                tags=("task",),
            )

    def finalize(
        self, *, expected_task_revision: str, summary: str, idempotency_key: str
    ) -> dict[str, Any]:
        validate_text(summary, 4096)
        manager = self.manager
        key = self._key("finalize", idempotency_key)
        with manager._canonical_write_lock():
            previous = self._existing(key)
            snapshot, current, task = self._context(terminal_retry=previous is not None)
            refs = [
                {"kind": "task", "id": task["id"]},
                {"kind": "event", "id": expected_task_revision},
            ]
            if previous is not None:
                payload = previous.event["payload"]
                if (
                    previous.event["event_type"] != "delegation.succeeded"
                    or payload.get("summary") != summary
                    or payload.get("result_refs") != refs
                    or payload.get("delegation_id") != current["id"]
                ):
                    raise IdempotencyConflictError(
                        "Text result retry differs from its finalization."
                    )
            else:
                if (
                    snapshot.entity_revision("task", task["id"]) != expected_task_revision
                    or task.get("state") != "review"
                    or snapshot.entity_revision_actor("task", task["id"], expected_task_revision)
                    != manager.session_id
                ):
                    raise LifecycleConflictError(REFUSAL)
                # The submitted revision must follow this child's completion of
                # the original delegated task, not an unrelated later task cycle.
                completed = manager.events.get(task["expected_revision"]).event
                if (
                    completed["event_type"] != "task.completed"
                    or completed["actor"]["session_id"] != manager.session_id
                    or completed["payload"].get("expected_revision") != current["target_revision"]
                ):
                    raise LifecycleConflictError(REFUSAL)
                payload = {
                    "delegation_id": current["id"],
                    "expected_revision": snapshot.entity_revision("delegation", current["id"]),
                    "summary": summary,
                    "result_refs": refs,
                }
            return manager.record_event(
                "delegation.succeeded",
                payload,
                idempotency_key=key,
                relations=tuple(
                    manager._relation({"kind": "delegation", "id": current["id"]}, "produced", ref)
                    for ref in refs
                ),
                tags=("delegation",),
            )


def read_retained_text(manager: Any, manifest: Mapping[str, Any]) -> str:
    """Only immutable, typed retained reports; never fall back to a source path."""
    metadata = manifest.get("metadata") or {}
    size = manifest.get("size_bytes")
    if (
        metadata.get("output_kind") != TEXT_KIND
        or manifest.get("media_type") != TEXT_MEDIA
        or manifest.get("classification") not in {"internal", "public"}
        or type(size) is not int
        or not 1 <= size <= MAX_TEXT_BYTES
        or metadata.get("retained_content")
        != {"revision": manifest.get("revision"), "size_bytes": size}
    ):
        raise LifecycleConflictError(REFUSAL)
    content = OutputContentStore(manager).read(manifest["revision"], size).decode("utf-8")
    validate_text(content, MAX_TEXT_BYTES)
    manager.policy.assert_safe(content, context="retained text result")
    return content


def has_terminal_result_receipt(
    manager: Any, snapshot: ProjectSnapshot, delegation: Mapping[str, Any]
) -> bool:
    """Authorize a receipt-only MCP reconnect, never a new completed-worker run."""
    if (
        delegation.get("state") != "succeeded"
        or delegation.get("purpose") != "implementation"
        or delegation.get("child_session_id") != manager.session_id
    ):
        return False
    session = manager.sessions.require_active(manager.session_id)
    target = delegation.get("target_ref") or {}
    if target.get("kind") != "task":
        return False
    revision = snapshot.entity_revision("delegation", delegation["id"])
    if (
        snapshot.entity_revision_actor("delegation", delegation["id"], revision)
        != session.session_id
    ):
        return False
    try:
        event = manager.events.get(revision).event
        refs = event["payload"].get("result_refs") or []
        if (
            event["event_type"] != "delegation.succeeded"
            or event["actor"]["session_id"] != session.session_id
            or event.get("idempotency_namespace") != manager._namespace(session)
            or re.fullmatch(r"text-result-finalize-[a-f0-9]{64}", event.get("idempotency_key", ""))
            is None
            or len(refs) != 2
            or refs[0] != target
            or refs[1].get("kind") != "event"
            or snapshot.entity_revision_actor("task", target["id"], refs[1]["id"])
            != session.session_id
        ):
            return False
        submitted = manager.events.get(refs[1]["id"]).event
        completed = manager.events.get(submitted["payload"]["expected_revision"]).event
        return (
            submitted["event_type"] == "task.submitted"
            and submitted["actor"]["session_id"] == session.session_id
            and submitted["payload"].get("task_id") == target["id"]
            and completed["event_type"] == "task.completed"
            and completed["actor"]["session_id"] == session.session_id
            and completed["payload"].get("task_id") == target["id"]
            and completed["payload"].get("expected_revision") == delegation.get("target_revision")
        )
    except (KeyError, TypeError, ValueError, FileNotFoundError):
        return False
