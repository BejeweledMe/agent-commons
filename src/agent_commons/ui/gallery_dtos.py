"""Immutable, browser-safe DTOs for the Design Gallery read surface."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, TypedDict

from agent_commons.services.design_gallery import (
    GalleryPackage,
    GalleryReadRefusal,
    GalleryScreen,
    GallerySnapshot,
)

GALLERY_SCHEMA = "agent_commons.gallery.v1"

GalleryState = Literal["loading", "empty", "ready", "stale", "error"]


class GalleryScreenPayload(TypedDict):
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
    preview_state: Literal["ready", "stale", "unavailable"]
    preview_reason: str | None
    preview_eligible: bool
    width: int | None
    height: int | None


class GalleryPackagePayload(TypedDict):
    design_package_id: str
    revision: str
    title: str
    producer_session_id: str
    recorded_at: str | None
    freshness: Literal["fresh", "stale"]
    screen_count: int
    screens: list[GalleryScreenPayload]


class GalleryErrorPayload(TypedDict):
    code: str
    message: str
    safe_next_actions: list[str]


class GalleryResponsePayload(TypedDict):
    schema: str
    state: GalleryState
    freshness: Literal["fresh", "stale"] | None
    read_at: str | None
    packages: list[GalleryPackagePayload]
    error: GalleryErrorPayload | None


@dataclass(frozen=True, slots=True)
class GalleryErrorDTO:
    code: str
    message: str
    safe_next_actions: tuple[str, ...]

    def to_wire(self) -> GalleryErrorPayload:
        return {
            "code": self.code,
            "message": self.message,
            "safe_next_actions": list(self.safe_next_actions),
        }


@dataclass(frozen=True, slots=True)
class GalleryScreenDTO:
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
    preview_state: Literal["ready", "stale", "unavailable"]
    preview_reason: str | None
    width: int | None
    height: int | None

    @classmethod
    def from_record(cls, screen: GalleryScreen) -> GalleryScreenDTO:
        return cls(
            screen_id=screen.screen_id,
            ordinal=screen.ordinal,
            title=screen.title,
            artifact_id=screen.artifact_id,
            artifact_revision=screen.artifact_revision,
            artifact_content_revision=screen.artifact_content_revision,
            producer_task_id=screen.producer_task_id,
            producer_task_revision=screen.producer_task_revision,
            producer_session_id=screen.producer_session_id,
            classification=screen.classification,
            media_type=screen.media_type,
            preview_state=screen.preview_state,
            preview_reason=screen.preview_reason,
            width=screen.width,
            height=screen.height,
        )

    def to_wire(self) -> GalleryScreenPayload:
        return {
            "screen_id": self.screen_id,
            "ordinal": self.ordinal,
            "title": self.title,
            "artifact_id": self.artifact_id,
            "artifact_revision": self.artifact_revision,
            "artifact_content_revision": self.artifact_content_revision,
            "producer_task_id": self.producer_task_id,
            "producer_task_revision": self.producer_task_revision,
            "producer_session_id": self.producer_session_id,
            "classification": self.classification,
            "media_type": self.media_type,
            "preview_state": self.preview_state,
            "preview_reason": self.preview_reason,
            "preview_eligible": self.preview_state == "ready",
            "width": self.width,
            "height": self.height,
        }


@dataclass(frozen=True, slots=True)
class GalleryPackageDTO:
    design_package_id: str
    revision: str
    title: str
    producer_session_id: str
    recorded_at: str | None
    freshness: Literal["fresh", "stale"]
    screens: tuple[GalleryScreenDTO, ...]

    @classmethod
    def from_record(cls, package: GalleryPackage) -> GalleryPackageDTO:
        return cls(
            design_package_id=package.design_package_id,
            revision=package.revision,
            title=package.title,
            producer_session_id=package.producer_session_id,
            recorded_at=package.recorded_at,
            freshness=package.freshness,
            screens=tuple(GalleryScreenDTO.from_record(screen) for screen in package.screens),
        )

    def to_wire(self) -> GalleryPackagePayload:
        return {
            "design_package_id": self.design_package_id,
            "revision": self.revision,
            "title": self.title,
            "producer_session_id": self.producer_session_id,
            "recorded_at": self.recorded_at,
            "freshness": self.freshness,
            "screen_count": len(self.screens),
            "screens": [screen.to_wire() for screen in self.screens],
        }


@dataclass(frozen=True, slots=True)
class GalleryResponseDTO:
    """Complete Gallery response, including honest non-success UI states."""

    state: GalleryState
    freshness: Literal["fresh", "stale"] | None
    read_at: str | None
    packages: tuple[GalleryPackageDTO, ...]
    error: GalleryErrorDTO | None = None

    @classmethod
    def from_snapshot(cls, snapshot: GallerySnapshot) -> GalleryResponseDTO:
        packages = tuple(GalleryPackageDTO.from_record(item) for item in snapshot.packages)
        state: GalleryState
        if not packages:
            state = "empty"
        elif snapshot.freshness == "stale":
            state = "stale"
        else:
            state = "ready"
        return cls(
            state=state,
            freshness=snapshot.freshness,
            read_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            packages=packages,
        )

    @classmethod
    def loading(cls) -> GalleryResponseDTO:
        """Give the React surface the same typed shape before its request resolves."""

        return cls(state="loading", freshness=None, read_at=None, packages=())

    @classmethod
    def from_refusal(cls, refusal: GalleryReadRefusal) -> GalleryResponseDTO:
        return cls(
            state="error",
            freshness=None,
            read_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            packages=(),
            error=GalleryErrorDTO(
                code=refusal.code.value,
                message=refusal.message,
                safe_next_actions=refusal.safe_next_actions,
            ),
        )

    def to_wire(self) -> GalleryResponsePayload:
        return {
            "schema": GALLERY_SCHEMA,
            "state": self.state,
            "freshness": self.freshness,
            "read_at": self.read_at,
            "packages": [package.to_wire() for package in self.packages],
            "error": self.error.to_wire() if self.error is not None else None,
        }
