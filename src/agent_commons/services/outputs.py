"""Producer-scoped image outputs from one canonical snapshot.

Summary reads inspect metadata only. Detail reads verify current image bytes;
historical or stale bindings never fall back to an older ready image. Neither
operation launches a preview server, writes state, or exposes source paths.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Literal, Protocol, get_args

from agent_commons.core.ids import is_typed_id
from agent_commons.domain.design_packages import DesignPackageRecord, ScreenBinding
from agent_commons.domain.snapshot import ProjectSnapshot
from agent_commons.runtime.live_previews import LivePreview, LivePreviewRegistry
from agent_commons.services.artifact_content import (
    ArtifactPreview,
    ArtifactPreviewReader,
    ArtifactPreviewRefusal,
    PreviewRefusalCode,
)
from agent_commons.services.generated_outputs import producer_for_task, validated_generated_metadata

ScopeKind = Literal["task", "agent"]
OutputState = Literal["unchecked", "ready", "stale", "unavailable"]
VersionSelection = Literal["latest", "all"]
MAX_OUTPUTS = 64
_SAFE_CLASSIFICATIONS = frozenset({"public", "internal"})
_SAFE_MEDIA = frozenset({"image/png", "image/jpeg"})
_STALE_PREVIEW_CODES = frozenset(
    {
        "artifact_preview_not_found",
        "artifact_preview_missing_source",
        "artifact_preview_stale_source",
        "artifact_preview_manifest_invalid",
    }
)


class OutputReadRefusal(Exception):
    """A closed refusal containing no source/manager exception text."""

    def __init__(self, code: str, status_code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.message = message


def unavailable() -> OutputReadRefusal:
    return OutputReadRefusal(
        "outputs_unavailable", 409, "The canonical output projection is unavailable."
    )


class OutputManager(Protocol):
    def snapshot(self) -> ProjectSnapshot: ...


class PreviewReader(Protocol):
    def read(self, artifact_id: str) -> ArtifactPreview: ...


@dataclass(frozen=True, slots=True)
class OutputScope:
    kind: ScopeKind
    identifier: str


@dataclass(frozen=True, slots=True)
class ImageOutput:
    output_id: str
    series_id: str
    title: str
    package_id: str | None
    package_revision: str | None
    screen_id: str | None
    artifact_id: str
    artifact_revision: str
    content_revision: str
    task_id: str
    task_revision: str
    producer_session_id: str | None
    producer_agent_id: str | None
    producer_delegation_id: str | None
    recorded_at: str | None
    media_type: str
    classification: str
    state: OutputState
    reason: str | None
    latest: bool
    version_count: int
    width: int | None = None
    height: int | None = None
    kind: Literal["design_image", "artifact_image"] = "design_image"
    delegation_revision: str | None = None
    historical_preview_verified: bool = False


@dataclass(frozen=True, slots=True)
class OutputSnapshot:
    scope: OutputScope
    versions: VersionSelection
    items: tuple[ImageOutput | LivePreview, ...]


@dataclass(frozen=True, slots=True)
class OutputSummary:
    scope: OutputScope
    total: int
    unchecked: int
    stale: int
    unavailable: int


class OutputReads:
    """Scope before verifying bytes; reject oversized results instead of truncating."""

    def __init__(
        self,
        manager: OutputManager,
        *,
        preview_reader_factory: Callable[[Any], PreviewReader] = ArtifactPreviewReader,
        max_outputs: int = MAX_OUTPUTS,
        live_previews: LivePreviewRegistry | None = None,
    ) -> None:
        if max_outputs < 1:
            raise ValueError("output limit must be positive")
        self._manager = manager
        self._reader_factory = preview_reader_factory
        self._max_outputs = max_outputs
        self._live_previews = live_previews

    def list(self, scope_kind: str, scope_id: str, *, versions: str = "latest") -> OutputSnapshot:
        scope, selected, items = self._metadata(scope_kind, scope_id, versions)
        if len(items) > self._max_outputs:
            raise OutputReadRefusal(
                "outputs_bounds_exceeded", 409, "This output selection exceeds the display limit."
            )
        # Generated task history may be viewed after exact byte verification.
        # This is separate from freshness; formal design-package policy is unchanged.
        reader = None
        verified: list[ImageOutput | LivePreview] = []
        for item in items:
            historical = (
                isinstance(item, ImageOutput)
                and item.kind == "artifact_image"
                and item.state == "stale"
                and item.reason == "producer_task_revision_changed"
            )
            if isinstance(item, ImageOutput) and (item.state == "unchecked" or historical):
                try:
                    if reader is None:
                        reader = self._reader_factory(self._manager)
                    checked = self._verify(item, reader)
                    item = (
                        replace(item, historical_preview_verified=True)
                        if historical and checked.state == "ready"
                        else checked
                    )
                except Exception:
                    item = replace(item, state="unavailable", reason="output_preview_unavailable")
            verified.append(item)
        return OutputSnapshot(scope, selected, tuple(verified))

    def summary(self, scope_kind: str, scope_id: str) -> OutputSummary:
        """Counts are metadata currentness, explicitly not verified preview readiness."""

        scope, _, items = self._metadata(scope_kind, scope_id, "latest")
        counts = Counter(
            {"reported_ready": "unchecked", "starting": "unchecked", "expired": "unavailable"}.get(
                item.state, item.state
            )
            for item in items
        )
        return OutputSummary(
            scope, len(items), counts["unchecked"], counts["stale"], counts["unavailable"]
        )

    def _metadata(
        self, scope_kind: str, scope_id: str, versions: str
    ) -> tuple[OutputScope, VersionSelection, list[ImageOutput | LivePreview]]:
        if scope_kind not in {"task", "agent"} or not is_typed_id(scope_id, scope_kind):
            raise OutputReadRefusal("outputs_invalid_scope", 400, "Output scope is invalid.")
        if versions not in {"latest", "all"}:
            raise OutputReadRefusal(
                "outputs_invalid_versions", 400, "Output version selection is invalid."
            )
        scope = OutputScope("task" if scope_kind == "task" else "agent", scope_id)
        selected: VersionSelection = "latest" if versions == "latest" else "all"
        try:
            snapshot = self._manager.snapshot()
            if not isinstance(snapshot, ProjectSnapshot) or any(
                issue.severity == "error" for issue in snapshot.issues
            ):
                raise ValueError("invalid projection")
        except Exception:
            raise unavailable() from None
        collection = snapshot.tasks if scope.kind == "task" else snapshot.agents
        if scope_id not in collection:
            raise OutputReadRefusal("outputs_scope_not_found", 404, "Output scope was not found.")
        try:
            # Build this index once; never infer ownership from task assignees,
            # task actors, package publishers, session names, or role labels.
            delegations: dict[str, list[tuple[str, Mapping[str, Any]]]] = defaultdict(list)
            for identifier, delegation in snapshot.delegations.items():
                child = delegation.get("child_session_id")
                if isinstance(child, str):
                    delegations[child].append((identifier, delegation))
            records = {
                (record.design_package_id, record.revision): record
                for record in snapshot.design_package_revisions.values()
            }
            records.update(
                ((record.design_package_id, record.revision), record)
                for record in snapshot.design_packages.values()
            )
            # Only effective, still-published package families are accessible.
            records = {
                key: record
                for key, record in records.items()
                if record.design_package_id in snapshot.design_packages
            }
            items: list[ImageOutput] = []
            for record in records.values():
                latest = (
                    snapshot.design_packages[record.design_package_id].revision == record.revision
                )
                for screen in record.draft.screens:
                    if scope.kind == "task" and screen.producer_task_binding.identifier != scope_id:
                        continue
                    item = self._item(snapshot, record, screen, delegations, latest)
                    if item is None:
                        continue
                    if scope.kind == "agent" and item.producer_agent_id != scope_id:
                        continue
                    items.append(item)
            items.extend(self._generated_items(snapshot, scope, delegations))
            versions_by_series = Counter(item.series_id for item in items)
            # Scope first, then choose the last relevant revision per series.
            # A former producer's last output remains discoverable even when a
            # different producer replaces it. Superseded state is retained;
            # selection never searches for a ready alternative.
            items.sort(
                key=lambda item: (item.latest, item.recorded_at or "", item.output_id),
                reverse=True,
            )
            latest_by_series: dict[str, str] = {}
            for item in items:
                latest_by_series.setdefault(item.series_id, item.output_id)
            items = [
                replace(
                    item,
                    version_count=versions_by_series[item.series_id],
                    latest=item.output_id == latest_by_series[item.series_id],
                )
                for item in items
                if selected == "all" or item.output_id == latest_by_series[item.series_id]
            ]
            items.sort(key=lambda item: (item.recorded_at or "", item.output_id), reverse=True)
            combined: list[ImageOutput | LivePreview] = list(items)
            if self._live_previews is not None:
                combined.extend(
                    self._live_previews.list(
                        snapshot, scope.kind, scope.identifier, versions=selected
                    )
                )
            return scope, selected, combined
        except Exception:
            raise unavailable() from None

    def _generated_items(
        self,
        snapshot: ProjectSnapshot,
        scope: OutputScope,
        delegations: Mapping[str, list[tuple[str, Mapping[str, Any]]]],
    ) -> list[ImageOutput]:
        items = []
        for artifact_id, artifact in snapshot.artifacts.items():
            if artifact.get("classification") not in _SAFE_CLASSIFICATIONS:
                continue
            manifest_ref = artifact.get("manifest_ref")
            if manifest_ref not in snapshot.known_manifest_ids:
                continue
            try:
                # Manifests are bounded canonical metadata; never open source bytes here.
                manifest = self._manager.manifests.get(manifest_ref).manifest
                metadata = validated_generated_metadata(snapshot, artifact, manifest, delegations)
            except Exception:
                continue
            if metadata is None:
                continue
            task = metadata["output_task"]
            producer = metadata["output_delegation"]
            if (scope.kind == "task" and task["id"] != scope.identifier) or (
                scope.kind == "agent" and metadata["producer_agent_id"] != scope.identifier
            ):
                continue
            revision = snapshot.entity_revision("artifact", artifact_id)
            state: OutputState = "unchecked"
            reason = None
            if not _exact(snapshot.tasks.get(task["id"]), task["revision"]):
                state, reason = "stale", "producer_task_revision_changed"
            elif manifest.get("media_type") not in _SAFE_MEDIA:
                state, reason = "unavailable", "output_preview_unsupported"
            items.append(
                ImageOutput(
                    output_id=f"{artifact_id}@{revision}",
                    series_id=metadata["series_id"],
                    title=metadata["title"],
                    package_id=None,
                    package_revision=None,
                    screen_id=None,
                    artifact_id=artifact_id,
                    artifact_revision=revision,
                    content_revision=manifest["revision"],
                    task_id=task["id"],
                    task_revision=task["revision"],
                    producer_session_id=metadata["producer_session_id"],
                    producer_agent_id=metadata["producer_agent_id"],
                    producer_delegation_id=producer["id"],
                    recorded_at=artifact.get("recorded_at"),
                    media_type=manifest["media_type"],
                    classification=manifest["classification"],
                    state=state,
                    reason=reason,
                    latest=True,
                    version_count=1,
                    kind="artifact_image",
                    delegation_revision=producer["revision"],
                )
            )
        return items

    @staticmethod
    def _item(
        snapshot: ProjectSnapshot,
        package: DesignPackageRecord,
        screen: ScreenBinding,
        delegations: Mapping[str, list[tuple[str, Mapping[str, Any]]]],
        latest: bool,
    ) -> ImageOutput | None:
        artifact_id = screen.artifact_binding.identifier
        artifact_revision = screen.artifact_binding.revision
        artifact = snapshot.artifacts.get(artifact_id)
        # Current reclassification revokes even historical title/metadata access.
        if screen.classification not in _SAFE_CLASSIFICATIONS or (
            artifact is not None and artifact.get("classification") not in _SAFE_CLASSIFICATIONS
        ):
            return None
        session = snapshot.entity_revision_actor("artifact", artifact_id, artifact_revision)
        producer_agent = None
        producer_delegation = None
        producer = producer_for_task(
            snapshot, session, screen.producer_task_binding.identifier, delegations
        )
        if producer is not None:
            producer_delegation, producer_agent = producer
        state: OutputState = "unchecked"
        reason = None
        task = snapshot.tasks.get(screen.producer_task_binding.identifier)
        if not latest:
            state, reason = "stale", "package_revision_superseded"
        elif not _exact(artifact, artifact_revision):
            state, reason = "stale", "artifact_revision_changed"
        elif not _exact(task, screen.producer_task_binding.revision):
            state, reason = "stale", "producer_task_revision_changed"
        elif artifact is None or (
            artifact.get("content_revision") != screen.artifact_content_revision
            or artifact.get("classification") != screen.classification
            or artifact.get("manifest_ref") not in snapshot.known_manifest_ids
        ):
            state, reason = "stale", "artifact_binding_changed"
        elif session is None:
            state, reason = "stale", "producer_provenance_missing"
        elif not screen.safe_preview_eligible or screen.media_type not in _SAFE_MEDIA:
            state, reason = "unavailable", "output_preview_unsupported"
        series = f"{package.design_package_id}:{screen.screen_id}"
        return ImageOutput(
            output_id=f"{package.design_package_id}@{package.revision}:{screen.screen_id}",
            series_id=series,
            title=screen.title,
            package_id=package.design_package_id,
            package_revision=package.revision,
            screen_id=screen.screen_id,
            artifact_id=artifact_id,
            artifact_revision=artifact_revision,
            content_revision=screen.artifact_content_revision,
            task_id=screen.producer_task_binding.identifier,
            task_revision=screen.producer_task_binding.revision,
            producer_session_id=session,
            producer_agent_id=producer_agent,
            producer_delegation_id=producer_delegation,
            recorded_at=package.recorded_at,
            media_type=screen.media_type,
            classification=screen.classification,
            state=state,
            reason=reason,
            latest=latest,
            version_count=1,
        )

    @staticmethod
    def _verify(item: ImageOutput, reader: PreviewReader) -> ImageOutput:
        try:
            preview = reader.read(item.artifact_id)
        except ArtifactPreviewRefusal as exc:
            state: OutputState = "stale" if exc.code in _STALE_PREVIEW_CODES else "unavailable"
            # Runtime allowlist as well as a Literal annotation: an unexpected
            # reader error must not smuggle source text through its code field.
            reason = (
                exc.code
                if exc.code in get_args(PreviewRefusalCode)
                else "output_preview_unavailable"
            )
            return replace(item, state=state, reason=reason)
        if preview.revision != item.content_revision or preview.media_type != item.media_type:
            return replace(item, state="stale", reason="verified_preview_changed")
        return replace(item, state="ready", width=preview.width, height=preview.height)


def _exact(record: Mapping[str, Any] | None, revision: str) -> bool:
    return record is not None and (record.get("effective_revision") or record.get("revision")) == (
        revision
    )
