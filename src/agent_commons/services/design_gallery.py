"""Bounded, read-only Gallery projection over canonical Design Packages.

The canonical ledger remains the only source of package, screen, artifact and
task identity.  This collaborator owns only a disposable read: it compares
each exact screen binding with the current projection and asks the existing
``ArtifactPreviewReader`` to re-verify the source bytes.  Filesystem paths and
preview refusal messages never cross this boundary.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, Protocol

from agent_commons.core.ids import is_typed_id
from agent_commons.domain.design_packages import DesignPackageRecord, ScreenBinding
from agent_commons.domain.snapshot import ProjectSnapshot
from agent_commons.services.artifact_content import (
    ArtifactPreview,
    ArtifactPreviewReader,
    ArtifactPreviewRefusal,
)

MAX_GALLERY_PACKAGES = 64
MAX_GALLERY_SCREENS = 64

GalleryFreshness = Literal["fresh", "stale"]
GalleryPreviewState = Literal["ready", "stale", "unavailable"]


class GalleryRefusalCode(StrEnum):
    """Closed browser-safe failure vocabulary for Gallery reads."""

    INVALID_ID = "gallery_invalid_id"
    NOT_FOUND = "gallery_package_not_found"
    PROJECTION_UNAVAILABLE = "gallery_projection_unavailable"
    BOUNDS_EXCEEDED = "gallery_bounds_exceeded"


class GalleryReadRefusal(Exception):
    """A typed refusal containing only maintainer-authored remediation."""

    def __init__(
        self,
        code: GalleryRefusalCode,
        status_code: int,
        message: str,
        safe_next_actions: tuple[str, ...],
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.message = message
        self.safe_next_actions = safe_next_actions


@dataclass(frozen=True, slots=True)
class GalleryScreen:
    """One ordered screen with exact canonical provenance and preview state."""

    screen_id: str
    ordinal: int
    title: str
    artifact_id: str
    artifact_revision: str
    artifact_content_revision: str
    producer_task_id: str
    producer_task_revision: str
    producer_session_id: str
    classification: str
    media_type: str
    preview_state: GalleryPreviewState
    preview_reason: str | None
    width: int | None
    height: int | None


@dataclass(frozen=True, slots=True)
class GalleryPackage:
    """Current published package revision rendered for the Gallery."""

    design_package_id: str
    revision: str
    title: str
    producer_session_id: str
    recorded_at: str | None
    freshness: GalleryFreshness
    screens: tuple[GalleryScreen, ...]


@dataclass(frozen=True, slots=True)
class GallerySnapshot:
    """A bounded Gallery result derived from one canonical project snapshot."""

    freshness: GalleryFreshness
    packages: tuple[GalleryPackage, ...]


class GalleryManager(Protocol):
    """The only manager capability needed by the Gallery read service."""

    def snapshot(self) -> ProjectSnapshot: ...


PreviewReaderFactory = Callable[[GalleryManager], ArtifactPreviewReader]


_STALE_PREVIEW_CODES = frozenset(
    {
        "artifact_preview_not_found",
        "artifact_preview_missing_source",
        "artifact_preview_stale_source",
        "artifact_preview_manifest_invalid",
    }
)


class DesignGalleryReads:
    """Build Gallery views without adding canonical or operational state."""

    def __init__(
        self,
        manager: GalleryManager,
        *,
        preview_reader_factory: PreviewReaderFactory = ArtifactPreviewReader,
        max_packages: int = MAX_GALLERY_PACKAGES,
        max_screens: int = MAX_GALLERY_SCREENS,
    ) -> None:
        if max_packages < 1 or max_screens < 1:
            raise ValueError("Gallery read limits must be positive")
        self._manager = manager
        self._preview_reader_factory = preview_reader_factory
        self._max_packages = max_packages
        self._max_screens = max_screens

    def list(self) -> GallerySnapshot:
        """Return all current packages when the bounded view can be complete."""

        snapshot = self._snapshot()
        package_ids = sorted(snapshot.design_packages)
        if len(package_ids) > self._max_packages:
            raise GalleryReadRefusal(
                GalleryRefusalCode.BOUNDS_EXCEEDED,
                409,
                "The Gallery contains more packages than this bounded view can display.",
                ("narrow the Gallery selection", "archive superseded packages"),
            )
        reader = self._reader()
        remaining = self._max_screens
        packages: list[GalleryPackage] = []
        for package_id in package_ids:
            record = snapshot.design_packages[package_id]
            remaining -= len(record.draft.screens)
            if remaining < 0:
                raise GalleryReadRefusal(
                    GalleryRefusalCode.BOUNDS_EXCEEDED,
                    409,
                    "The Gallery contains more screens than this bounded view can display.",
                    ("open one Design Package", "split the Gallery into smaller packages"),
                )
            packages.append(self._package(snapshot, record, reader))
        freshness: GalleryFreshness = (
            "stale" if any(package.freshness == "stale" for package in packages) else "fresh"
        )
        return GallerySnapshot(freshness=freshness, packages=tuple(packages))

    def get(self, design_package_id: str) -> GallerySnapshot:
        """Return one current package by typed id, preserving the list wire shape."""

        if not is_typed_id(design_package_id, "design_package"):
            raise GalleryReadRefusal(
                GalleryRefusalCode.INVALID_ID,
                400,
                "The Design Package identifier is malformed.",
                ("refresh the Gallery and select a published package",),
            )
        snapshot = self._snapshot()
        record = snapshot.design_packages.get(design_package_id)
        if record is None:
            raise GalleryReadRefusal(
                GalleryRefusalCode.NOT_FOUND,
                404,
                "The requested Design Package is not published.",
                ("refresh the Gallery", "publish a new Design Package revision"),
            )
        if len(record.draft.screens) > self._max_screens:
            raise GalleryReadRefusal(
                GalleryRefusalCode.BOUNDS_EXCEEDED,
                409,
                "The Design Package exceeds this bounded Gallery view.",
                ("publish a smaller Design Package revision",),
            )
        package = self._package(snapshot, record, self._reader())
        return GallerySnapshot(freshness=package.freshness, packages=(package,))

    def _snapshot(self) -> ProjectSnapshot:
        try:
            snapshot = self._manager.snapshot()
            if not isinstance(snapshot, ProjectSnapshot):
                raise ValueError("unexpected projection type")
            if any(issue.severity == "error" for issue in snapshot.issues):
                raise ValueError("projection contains errors")
            return snapshot
        except Exception:
            # Derived-state readers are a secrecy boundary: a damaged cache or
            # hostile manager double must not send its exception text to UI.
            raise GalleryReadRefusal(
                GalleryRefusalCode.PROJECTION_UNAVAILABLE,
                409,
                "The canonical Gallery projection is not currently available.",
                ("run workspace diagnostics", "rebuild the derived projection and retry"),
            ) from None

    def _reader(self) -> ArtifactPreviewReader:
        try:
            return self._preview_reader_factory(self._manager)
        except Exception:
            raise GalleryReadRefusal(
                GalleryRefusalCode.PROJECTION_UNAVAILABLE,
                409,
                "The verified preview reader is not currently available.",
                ("run workspace diagnostics", "retry after the workspace is repaired"),
            ) from None

    def _package(
        self,
        snapshot: ProjectSnapshot,
        record: DesignPackageRecord,
        reader: ArtifactPreviewReader,
    ) -> GalleryPackage:
        screens = tuple(self._screen(snapshot, screen, reader) for screen in record.draft.screens)
        freshness: GalleryFreshness = (
            "stale" if any(screen.preview_state != "ready" for screen in screens) else "fresh"
        )
        return GalleryPackage(
            design_package_id=str(record.design_package_id),
            revision=str(record.revision),
            title=str(record.draft.title),
            producer_session_id=str(record.producer_session_id),
            recorded_at=str(record.recorded_at) if record.recorded_at is not None else None,
            freshness=freshness,
            screens=screens,
        )

    def _screen(
        self,
        snapshot: ProjectSnapshot,
        screen: ScreenBinding,
        reader: ArtifactPreviewReader,
    ) -> GalleryScreen:
        preview_state: GalleryPreviewState = "ready"
        preview_reason: str | None = None
        preview: ArtifactPreview | None = None
        artifact = snapshot.artifacts.get(screen.artifact_binding.identifier)
        task = snapshot.tasks.get(screen.producer_task_binding.identifier)

        if not self._exact_revision(artifact, screen.artifact_binding.revision):
            preview_state, preview_reason = "stale", "artifact_revision_changed"
        elif not self._exact_revision(task, screen.producer_task_binding.revision):
            preview_state, preview_reason = "stale", "producer_task_revision_changed"
        elif not self._artifact_binding_matches(snapshot, artifact, screen):
            preview_state, preview_reason = "stale", "artifact_binding_changed"
        else:
            try:
                preview = reader.read(screen.artifact_binding.identifier)
                if (
                    preview.revision != screen.artifact_content_revision
                    or preview.media_type != screen.media_type
                ):
                    preview_state, preview_reason = "stale", "verified_preview_changed"
                    preview = None
            except ArtifactPreviewRefusal as exc:
                preview_state = "stale" if exc.code in _STALE_PREVIEW_CODES else "unavailable"
                preview_reason = str(exc.code)
            except Exception:
                # A provider/filesystem exception is neither canonical data nor
                # browser copy.  Collapse it to one closed state without its text.
                preview_state, preview_reason = "unavailable", "artifact_preview_unavailable"

        producer = snapshot.entity_revision_actor(
            "task",
            screen.producer_task_binding.identifier,
            screen.producer_task_binding.revision,
        )
        if not isinstance(producer, str) or not producer:
            producer = "unknown"
            if preview_state == "ready":
                preview_state, preview_reason = "stale", "producer_provenance_missing"
                preview = None

        return GalleryScreen(
            screen_id=str(screen.screen_id),
            ordinal=int(screen.ordinal),
            title=str(screen.title),
            artifact_id=str(screen.artifact_binding.identifier),
            artifact_revision=str(screen.artifact_binding.revision),
            artifact_content_revision=str(screen.artifact_content_revision),
            producer_task_id=str(screen.producer_task_binding.identifier),
            producer_task_revision=str(screen.producer_task_binding.revision),
            producer_session_id=producer,
            classification=str(screen.classification),
            media_type=str(screen.media_type),
            preview_state=preview_state,
            preview_reason=preview_reason,
            width=preview.width if preview is not None else None,
            height=preview.height if preview is not None else None,
        )

    @staticmethod
    def _exact_revision(value: Mapping[str, Any] | None, expected: str) -> bool:
        if value is None:
            return False
        return str(value.get("effective_revision") or value.get("revision") or "") == expected

    @staticmethod
    def _artifact_binding_matches(
        snapshot: ProjectSnapshot,
        artifact: Mapping[str, Any] | None,
        screen: ScreenBinding,
    ) -> bool:
        if artifact is None:
            return False
        return (
            artifact.get("content_revision") == screen.artifact_content_revision
            and artifact.get("classification") == screen.classification
            and artifact.get("manifest_ref") in snapshot.known_manifest_ids
        )
