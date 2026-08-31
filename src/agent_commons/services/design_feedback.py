"""Revision-bound Design Gallery feedback through ordinary canonical threads."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from typing import Any, Protocol

from agent_commons.core.bounded import truncate_utf8
from agent_commons.domain.design_packages import (
    DesignPackageRecord,
    DesignPackageRefusal,
    DesignPackageRefusalCode,
    ScreenBinding,
)
from agent_commons.errors import ValidationError

MAX_DESIGN_FEEDBACK_BYTES = 8_192


class DesignFeedbackManager(Protocol):
    def snapshot(self) -> Any: ...

    def _canonical_write_lock(self) -> AbstractContextManager[None]: ...

    def open_thread(
        self,
        *,
        thread_type: str,
        subject: str,
        desired_outcome: str,
        to: Sequence[str],
        related_refs: Sequence[Mapping[str, str]] = (),
        extensions: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]: ...

    def reply_thread(
        self,
        thread_id: str,
        expected_revision: str,
        *,
        body: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]: ...


def open_design_feedback(
    manager: DesignFeedbackManager,
    *,
    design_package_id: str,
    design_package_revision: str,
    screen_id: str,
    artifact_revision: str,
    producer_task_revision: str,
    body: str,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Open one discussion bound to the exact revision visible in the inspector."""

    message = body.strip()
    if not message:
        raise ValidationError("design feedback needs a non-empty message")
    if len(message.encode("utf-8")) > MAX_DESIGN_FEEDBACK_BYTES:
        raise ValidationError("design feedback exceeds the 8192-byte limit")

    # Revalidate and append both ordinary thread events under the manager's
    # re-entrant canonical lock. A concurrent revision cannot land between the
    # exact snapshot check and thread.opened. If the process dies after the
    # first append, retrying the same idempotency key reuses that thread and
    # completes its one initial reply rather than creating a duplicate.
    with manager._canonical_write_lock():
        snapshot = manager.snapshot()
        package, screen, producer = _resolve_exact_screen(
            snapshot,
            design_package_id=design_package_id,
            design_package_revision=design_package_revision,
            screen_id=screen_id,
            artifact_revision=artifact_revision,
            producer_task_revision=producer_task_revision,
        )
        recipients = ("operator",) if not producer else ("operator", str(producer))
        binding = {
            "design_package_id": package.design_package_id,
            "design_package_revision": package.revision,
            "screen_id": screen.screen_id,
            "artifact_id": screen.artifact_binding.identifier,
            "artifact_revision": screen.artifact_binding.revision,
            "artifact_content_revision": screen.artifact_content_revision,
            "producer_task_id": screen.producer_task_binding.identifier,
            "producer_task_revision": screen.producer_task_binding.revision,
        }
        opened = manager.open_thread(
            thread_type="review_discussion",
            subject=truncate_utf8(f"Design feedback: {package.draft.title} / {screen.title}", 300),
            desired_outcome="the exact reviewed design revision receives a deliberate response",
            to=recipients,
            related_refs=(
                {"kind": "design_package", "id": package.design_package_id},
                {"kind": "artifact", "id": screen.artifact_binding.identifier},
                {"kind": "task", "id": screen.producer_task_binding.identifier},
            ),
            extensions={"design_feedback": binding},
            idempotency_key=idempotency_key,
        )
        return manager.reply_thread(
            str(opened["entity_ref"]["id"]),
            str(opened["revision"]),
            body=message,
            idempotency_key=f"{opened['idempotency_key']}:body",
        )


def _resolve_exact_screen(
    snapshot: Any,
    *,
    design_package_id: str,
    design_package_revision: str,
    screen_id: str,
    artifact_revision: str,
    producer_task_revision: str,
) -> tuple[DesignPackageRecord, ScreenBinding, str]:
    package = snapshot.design_packages.get(design_package_id)
    if not isinstance(package, DesignPackageRecord):
        raise _refusal(
            DesignPackageRefusalCode.MISSING,
            "The selected Design Package is unavailable.",
            "Refresh the Gallery and select a published package.",
        )
    if package.revision != design_package_revision:
        raise _stale("The selected Design Package revision is no longer current.")
    screen = next((item for item in package.draft.screens if item.screen_id == screen_id), None)
    if screen is None:
        raise _refusal(
            DesignPackageRefusalCode.MISSING,
            "The selected screen is unavailable in this Design Package revision.",
            "Refresh the Gallery and select an existing screen.",
        )
    _assert_exact_screen(screen, artifact_revision, producer_task_revision)

    artifact = snapshot.artifacts.get(screen.artifact_binding.identifier)
    task = snapshot.tasks.get(screen.producer_task_binding.identifier)
    if not _has_exact_revision(artifact, screen.artifact_binding.revision):
        raise _stale("The selected screen artifact revision is no longer current.")
    if not _has_exact_revision(task, screen.producer_task_binding.revision):
        raise _stale("The selected screen producer task revision is no longer current.")
    if (
        artifact.get("content_revision") != screen.artifact_content_revision
        or artifact.get("classification") != screen.classification
        or artifact.get("manifest_ref") not in snapshot.known_manifest_ids
    ):
        raise _stale("The selected screen artifact provenance no longer matches.")
    artifact_producer = snapshot.entity_revision_actor(
        "artifact",
        screen.artifact_binding.identifier,
        screen.artifact_binding.revision,
    )
    task_producer = snapshot.entity_revision_actor(
        "task",
        screen.producer_task_binding.identifier,
        screen.producer_task_binding.revision,
    )
    if (
        not isinstance(artifact_producer, str)
        or not artifact_producer
        or not isinstance(task_producer, str)
        or not task_producer
        or artifact_producer != task_producer
    ):
        raise _stale("The selected screen producer provenance is unavailable or incoherent.")
    return package, screen, task_producer


def _assert_exact_screen(
    screen: ScreenBinding, artifact_revision: str, producer_task_revision: str
) -> None:
    if (
        screen.artifact_binding.revision != artifact_revision
        or screen.producer_task_binding.revision != producer_task_revision
    ):
        raise _refusal(
            DesignPackageRefusalCode.STALE,
            "The selected screen provenance no longer matches the inspector revision.",
            "Refresh the Gallery before sending feedback.",
        )


def _has_exact_revision(value: Mapping[str, Any] | None, expected: str) -> bool:
    if value is None:
        return False
    return str(value.get("effective_revision") or value.get("revision") or "") == expected


def _stale(message: str) -> DesignPackageRefusal:
    return _refusal(
        DesignPackageRefusalCode.STALE,
        message,
        "Refresh the Gallery before sending feedback.",
    )


def _refusal(
    code: DesignPackageRefusalCode, message: str, remediation: str
) -> DesignPackageRefusal:
    return DesignPackageRefusal(code, message, remediation)
