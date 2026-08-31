"""Bounded browser authoring over canonical Design Package commands.

The browser selects opaque candidates derived from current canonical artifact
and task revisions.  It never submits paths, hashes, classifications, media
types, producer identity, or arbitrary entity bindings.  The write side
rebuilds the candidate set immediately before calling ``DesignPackageCommands``
so a stale form fails without recording a partial package.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from agent_commons.core.bounded import truncate_utf8
from agent_commons.core.ids import stable_id
from agent_commons.domain.design_packages import (
    MAX_SCREENS,
    DesignPackageRefusal,
    DesignPackageRefusalCode,
    own_design_package_payload,
)
from agent_commons.domain.snapshot import ProjectSnapshot
from agent_commons.errors import ValidationError
from agent_commons.services.artifact_content import ArtifactPreviewReader, ArtifactPreviewRefusal

AUTHORING_SCHEMA = "agent_commons.gallery-authoring.v1"
AUTHORING_RESULT_SCHEMA = "agent_commons.gallery-authoring-result.v1"
MAX_AUTHORING_CANDIDATES = 256


class AuthoringManager(Protocol):
    workspace_id: str
    session_id: str | None
    design_package_writes_enabled: bool
    design_packages: Any

    def snapshot(self) -> ProjectSnapshot: ...


def _revision(record: Mapping[str, Any]) -> str:
    return str(record.get("effective_revision") or record.get("revision") or "")


def _candidate_id(
    artifact_id: str,
    artifact_revision: str,
    task_id: str,
    task_revision: str,
) -> str:
    return stable_id(
        "candidate",
        "\0".join((artifact_id, artifact_revision, task_id, task_revision)),
    )


def _candidate_title(value: object, fallback: str) -> str:
    printable = "".join(
        character
        for character in str(value or "")
        if ord(character) >= 32 and ord(character) != 127
    ).strip()
    bounded = truncate_utf8(printable, 1_024)[:256].strip()
    return bounded or fallback


def _error(code: str, message: str, *actions: str) -> dict[str, Any]:
    return {
        "schema": AUTHORING_SCHEMA,
        "state": "unavailable",
        "writes_enabled": False,
        "candidates": [],
        "error": {
            "code": code,
            "message": message,
            "safe_next_actions": list(actions),
        },
    }


def _eligible_candidates(
    manager: AuthoringManager,
    *,
    producer_session_id: str,
) -> tuple[dict[str, Any], ...]:
    snapshot = manager.snapshot()
    reader = ArtifactPreviewReader(manager)
    candidates: list[dict[str, Any]] = []
    for task_id in sorted(snapshot.tasks):
        task = snapshot.tasks[task_id]
        task_revision = _revision(task)
        if (
            not task_revision
            or snapshot.entity_revision_actor("task", task_id, task_revision) != producer_session_id
        ):
            continue
        bindings = task.get("artifact_bindings") or ()
        if not isinstance(bindings, (list, tuple)):
            continue
        for binding in bindings:
            if not isinstance(binding, Mapping) or set(binding) != {"ref", "revision"}:
                continue
            ref = binding.get("ref")
            if not isinstance(ref, Mapping) or ref.get("kind") != "artifact":
                continue
            artifact_id = str(ref.get("id") or "")
            artifact_revision = str(binding.get("revision") or "")
            artifact = snapshot.artifacts.get(artifact_id)
            if artifact is None or _revision(artifact) != artifact_revision:
                continue
            if (
                snapshot.entity_revision_actor("artifact", artifact_id, artifact_revision)
                != producer_session_id
            ):
                continue
            classification = str(artifact.get("classification") or "")
            content_revision = str(artifact.get("content_revision") or "")
            if classification not in {"public", "internal"}:
                continue
            if artifact.get("manifest_ref") not in snapshot.known_manifest_ids:
                continue
            try:
                preview = reader.read(artifact_id)
            except ArtifactPreviewRefusal:
                continue
            if preview.revision != content_revision or preview.media_type not in {
                "image/png",
                "image/jpeg",
            }:
                continue
            candidates.append(
                {
                    "candidate_id": _candidate_id(
                        artifact_id,
                        artifact_revision,
                        task_id,
                        task_revision,
                    ),
                    "artifact_id": artifact_id,
                    "artifact_revision": artifact_revision,
                    "artifact_content_revision": content_revision,
                    "producer_task_id": task_id,
                    "producer_task_revision": task_revision,
                    "producer_task_title": _candidate_title(task.get("title"), task_id),
                    "classification": classification,
                    "media_type": preview.media_type,
                    "width": preview.width,
                    "height": preview.height,
                }
            )
            if len(candidates) > MAX_AUTHORING_CANDIDATES:
                raise DesignPackageRefusal(
                    DesignPackageRefusalCode.OVERSIZED,
                    "The Gallery authoring candidate set exceeds its safe bound.",
                    "Narrow or archive producing tasks before opening the authoring surface.",
                )
    return tuple(candidates)


def build_authoring_snapshot(
    manager: AuthoringManager,
    *,
    producer_session_id: str | None,
) -> dict[str, Any]:
    """Return only current, exact and preview-qualified authoring candidates."""

    if producer_session_id is None:
        return _error(
            DesignPackageRefusalCode.UNAVAILABLE.value,
            "This Gallery was opened without an operator writing session.",
            "Restart the panel in operator mode to publish Design Packages.",
        )
    if not manager.design_package_writes_enabled:
        return _error(
            DesignPackageRefusalCode.UNAVAILABLE.value,
            "Design Package publishing is disabled by operator configuration.",
            "Enable Design Package writes explicitly before publishing.",
        )
    try:
        candidates = _eligible_candidates(
            manager,
            producer_session_id=producer_session_id,
        )
    except DesignPackageRefusal as exc:
        return _error(exc.code.value, exc.message, exc.remediation)
    return {
        "schema": AUTHORING_SCHEMA,
        "state": "ready" if candidates else "empty",
        "writes_enabled": True,
        "candidates": list(candidates),
        "error": None,
    }


def _strict_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DesignPackageRefusal(
            DesignPackageRefusalCode.INVALID,
            f"{field} must be non-empty text.",
            "Correct the authoring form and retry.",
        )
    return value.strip()


def _selection_intent(
    body: Mapping[str, object], *, revise: bool
) -> tuple[str, list[object], str, str | None]:
    try:
        owned = own_design_package_payload(body)
    except ValidationError as exc:
        raise DesignPackageRefusal(
            DesignPackageRefusalCode.OVERSIZED,
            "The Gallery authoring request exceeds its safe structural bound.",
            "Use at most 64 selected screens and retry.",
        ) from exc
    expected_fields = {"title", "screens", "idempotency_key"}
    if revise:
        expected_fields.add("expected_revision")
    if set(owned) != expected_fields:
        raise DesignPackageRefusal(
            DesignPackageRefusalCode.INVALID,
            "The Gallery authoring request contains unsupported or missing fields.",
            "Reload the Gallery and retry with its current form.",
        )
    title = _strict_text(owned.get("title"), "Package title")
    idempotency_key = _strict_text(owned.get("idempotency_key"), "Idempotency key")
    raw_screens = owned.get("screens")
    if not isinstance(raw_screens, list) or not 1 <= len(raw_screens) <= MAX_SCREENS:
        raise DesignPackageRefusal(
            DesignPackageRefusalCode.OVERSIZED,
            "A Design Package must contain between 1 and 64 screens.",
            "Select between 1 and 64 eligible PNG or JPEG artifacts.",
        )
    expected_revision = (
        _strict_text(owned.get("expected_revision"), "Expected revision") if revise else None
    )
    return title, raw_screens, idempotency_key, expected_revision


def _existing_event(manager: AuthoringManager, key: str) -> Mapping[str, Any] | None:
    normalized_key = manager._idempotency_key("design_package.authoring", key)
    session = manager._active_session()
    namespace = manager._namespace(session)
    reservation = manager.events.idempotency.lookup(namespace=namespace, key=normalized_key)
    record = (
        manager.events.get(reservation.event_id)
        if reservation is not None
        else manager._event_for_idempotency_identity(namespace, normalized_key)
    )
    return None if record is None else record.event


def _stored_selection(payload: Mapping[str, Any]) -> tuple[str, tuple[tuple[str, str], ...]]:
    screens = payload.get("screens")
    if not isinstance(screens, list):
        raise DesignPackageRefusal(
            DesignPackageRefusalCode.INVALID,
            "The saved Design Package revision cannot be replayed safely.",
            "Run workspace diagnostics before retrying this operation.",
        )
    selected: list[tuple[str, str]] = []
    for screen in screens:
        if not isinstance(screen, Mapping):
            raise DesignPackageRefusal(
                DesignPackageRefusalCode.INVALID,
                "The saved Design Package revision cannot be replayed safely.",
                "Run workspace diagnostics before retrying this operation.",
            )
        artifact = screen.get("artifact_binding")
        task = screen.get("producer_task_binding")
        if not isinstance(artifact, Mapping) or not isinstance(task, Mapping):
            raise DesignPackageRefusal(
                DesignPackageRefusalCode.INVALID,
                "The saved Design Package revision cannot be replayed safely.",
                "Run workspace diagnostics before retrying this operation.",
            )
        artifact_ref = artifact.get("ref")
        task_ref = task.get("ref")
        if not isinstance(artifact_ref, Mapping) or not isinstance(task_ref, Mapping):
            raise DesignPackageRefusal(
                DesignPackageRefusalCode.INVALID,
                "The saved Design Package revision cannot be replayed safely.",
                "Run workspace diagnostics before retrying this operation.",
            )
        selected.append(
            (
                _candidate_id(
                    str(artifact_ref.get("id") or ""),
                    str(artifact.get("revision") or ""),
                    str(task_ref.get("id") or ""),
                    str(task.get("revision") or ""),
                ),
                str(screen.get("title") or ""),
            )
        )
    return str(payload.get("title") or ""), tuple(selected)


def _replay_if_present(
    manager: AuthoringManager,
    *,
    event_type: str,
    design_package_id: str | None,
    title: str,
    raw_screens: list[object],
    key: str,
    expected_revision: str | None,
) -> Any | None:
    event = _existing_event(manager, key)
    if event is None:
        return None
    payload = event.get("payload")
    if event.get("event_type") != event_type or not isinstance(payload, Mapping):
        raise DesignPackageRefusal(
            DesignPackageRefusalCode.INVALID,
            "The idempotency key already belongs to a different operation.",
            "Use a new idempotency key for different content.",
        )
    requested: list[tuple[str, str]] = []
    for screen in raw_screens:
        if not isinstance(screen, dict) or set(screen) != {"candidate_id", "title"}:
            raise DesignPackageRefusal(
                DesignPackageRefusalCode.INVALID,
                "A selected screen contains unsupported or missing fields.",
                "Reload the Gallery and select current eligible artifacts.",
            )
        requested.append(
            (
                _strict_text(screen.get("candidate_id"), "Candidate"),
                _strict_text(screen.get("title"), "Screen title"),
            )
        )
    stored_title, stored_screens = _stored_selection(payload)
    same = stored_title == title and stored_screens == tuple(requested)
    if event_type == "design_package.revised":
        same = (
            same
            and payload.get("design_package_id") == design_package_id
            and payload.get("expected_revision") == expected_revision
        )
    if not same:
        raise DesignPackageRefusal(
            DesignPackageRefusalCode.INVALID,
            "The idempotency key already belongs to different Design Package content.",
            "Retry the original request unchanged or use a new idempotency key.",
        )
    draft = {"title": payload["title"], "screens": payload["screens"]}
    if event_type == "design_package.created":
        return manager.design_packages.publish(draft, idempotency_key=key)
    return manager.design_packages.revise(
        str(payload["design_package_id"]),
        str(payload["expected_revision"]),
        draft,
        idempotency_key=key,
    )


def _draft_from_selection(
    manager: AuthoringManager,
    *,
    title: str,
    raw_screens: list[object],
    idempotency_key: str,
    operation_identity: str,
) -> tuple[dict[str, object], str]:
    if manager.session_id is None:
        raise DesignPackageRefusal(
            DesignPackageRefusalCode.UNAVAILABLE,
            "The authoring operation has no active writer session.",
            "Restart the panel in operator mode and retry.",
        )
    available = {
        item["candidate_id"]: item
        for item in _eligible_candidates(manager, producer_session_id=manager.session_id)
    }
    screens: list[dict[str, object]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_screens, start=1):
        if not isinstance(raw, dict) or set(raw) != {"candidate_id", "title"}:
            raise DesignPackageRefusal(
                DesignPackageRefusalCode.INVALID,
                "A selected screen contains unsupported or missing fields.",
                "Reload the Gallery and select current eligible artifacts.",
            )
        candidate_id = _strict_text(raw.get("candidate_id"), "Candidate")
        if candidate_id in seen:
            raise DesignPackageRefusal(
                DesignPackageRefusalCode.INVALID,
                "A Design Package cannot contain the same candidate twice.",
                "Remove the duplicate screen and retry.",
            )
        seen.add(candidate_id)
        candidate = available.get(candidate_id)
        if candidate is None:
            raise DesignPackageRefusal(
                DesignPackageRefusalCode.STALE,
                "A selected Gallery candidate is no longer current or eligible.",
                "Reload the Gallery, review current provenance, and retry.",
            )
        screens.append(
            {
                "screen_id": stable_id(
                    "screen",
                    "\0".join((manager.workspace_id, operation_identity, str(index), candidate_id)),
                ),
                "ordinal": index,
                "title": _strict_text(raw.get("title"), "Screen title"),
                "artifact_binding": {
                    "ref": {"kind": "artifact", "id": candidate["artifact_id"]},
                    "revision": candidate["artifact_revision"],
                },
                "artifact_content_revision": candidate["artifact_content_revision"],
                "producer_task_binding": {
                    "ref": {"kind": "task", "id": candidate["producer_task_id"]},
                    "revision": candidate["producer_task_revision"],
                },
                "classification": candidate["classification"],
                "media_type": candidate["media_type"],
                "safe_preview_eligible": True,
            }
        )
    return {"title": title, "screens": screens}, idempotency_key


def publish_from_selection(manager: AuthoringManager, body: Mapping[str, object]) -> dict[str, Any]:
    """Publish through the existing canonical command after exact revalidation."""

    title, raw_screens, key, _expected = _selection_intent(body, revise=False)
    record = _replay_if_present(
        manager,
        event_type="design_package.created",
        design_package_id=None,
        title=title,
        raw_screens=raw_screens,
        key=key,
        expected_revision=None,
    )
    if record is None:
        draft, key = _draft_from_selection(
            manager,
            title=title,
            raw_screens=raw_screens,
            idempotency_key=key,
            operation_identity=f"publish\0{key}",
        )
        record = manager.design_packages.publish(draft, idempotency_key=key)
    return {
        "schema": AUTHORING_RESULT_SCHEMA,
        "state": "published",
        "design_package_id": record.design_package_id,
        "revision": record.revision,
    }


def revise_from_selection(
    manager: AuthoringManager,
    design_package_id: str,
    body: Mapping[str, object],
) -> dict[str, Any]:
    """Revise the exact current package through its existing CAS command."""

    title, raw_screens, key, expected_revision = _selection_intent(body, revise=True)
    assert expected_revision is not None
    record = _replay_if_present(
        manager,
        event_type="design_package.revised",
        design_package_id=design_package_id,
        title=title,
        raw_screens=raw_screens,
        key=key,
        expected_revision=expected_revision,
    )
    if record is None:
        draft, key = _draft_from_selection(
            manager,
            title=title,
            raw_screens=raw_screens,
            idempotency_key=key,
            operation_identity=f"revise\0{design_package_id}\0{key}",
        )
        record = manager.design_packages.revise(
            design_package_id,
            expected_revision,
            draft,
            idempotency_key=key,
        )
    return {
        "schema": AUTHORING_RESULT_SCHEMA,
        "state": "revised",
        "design_package_id": record.design_package_id,
        "revision": record.revision,
    }
