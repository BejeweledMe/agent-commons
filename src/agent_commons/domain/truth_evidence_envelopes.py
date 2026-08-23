"""Typed truth and evidence payloads after schema and domain validation.

This module owns the A5.4 vertical slice for artifacts, verifications,
findings, and decisions. Storage continues to receive canonical JSON-shaped
payloads; these immutable records are used only after JSON Schema and
``validate_payload`` have accepted those payloads.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, NotRequired, TypeAlias, TypedDict, cast

from .envelopes import EventEnvelope, FrozenJsonObject, JsonValue, TypedRef, TypedRefPayload
from .task_review_envelopes import RevisionBoundRef, RevisionBoundRefPayload


class ArtifactPayload(TypedDict):
    artifact_id: str
    manifest_ref: str
    revision: str
    classification: str
    expected_revision: NotRequired[str]
    extensions: NotRequired[dict[str, JsonValue]]


class VerificationPayload(TypedDict):
    verification_id: str
    target_ref: TypedRefPayload
    target_revision: str
    claim: str
    evidence_refs: list[RevisionBoundRefPayload]
    method: NotRequired[str]
    outcome: NotRequired[str]
    extensions: NotRequired[dict[str, JsonValue]]


class FindingPayload(TypedDict):
    finding_id: str
    summary: NotRequired[str]
    severity: NotRequired[str]
    evidence_refs: NotRequired[list[RevisionBoundRefPayload]]
    expected_revision: NotRequired[str]
    reason: NotRequired[str]
    resolution: NotRequired[str]
    extensions: NotRequired[dict[str, JsonValue]]


class DecisionPayload(TypedDict):
    decision_id: str
    scope: NotRequired[str]
    proposal: NotRequired[str]
    alternatives: NotRequired[list[str]]
    expected_revision: NotRequired[str]
    rationale: NotRequired[str]
    evidence_refs: NotRequired[list[RevisionBoundRefPayload]]
    dissent: NotRequired[list[str]]
    reason: NotRequired[str]
    replacement_decision_id: NotRequired[str]
    extensions: NotRequired[dict[str, JsonValue]]


ArtifactEventType: TypeAlias = Literal["artifact.registered", "artifact.revised"]
FindingEventType: TypeAlias = Literal[
    "finding.reported",
    "finding.promoted",
    "finding.contested",
    "finding.resolved",
]
DecisionEventType: TypeAlias = Literal[
    "decision.proposed",
    "decision.accepted",
    "decision.rejected",
    "decision.deferred",
    "decision.superseded",
]

_ARTIFACT_EVENT_TYPES = frozenset({"artifact.registered", "artifact.revised"})
_FINDING_EVENT_TYPES = frozenset(
    {
        "finding.reported",
        "finding.promoted",
        "finding.contested",
        "finding.resolved",
    }
)
_DECISION_EVENT_TYPES = frozenset(
    {
        "decision.proposed",
        "decision.accepted",
        "decision.rejected",
        "decision.deferred",
        "decision.superseded",
    }
)


@dataclass(frozen=True)
class ArtifactEnvelope(EventEnvelope):
    event_type: ArtifactEventType
    artifact_id: str
    expected_revision: str | None
    manifest_ref: str
    content_revision: str
    classification: str
    extensions: FrozenJsonObject | None

    def to_payload(self) -> ArtifactPayload:
        from .envelopes import thaw_json_object

        payload: ArtifactPayload = {
            "artifact_id": self.artifact_id,
            "manifest_ref": self.manifest_ref,
            "revision": self.content_revision,
            "classification": self.classification,
        }
        if self.expected_revision is not None:
            payload["expected_revision"] = self.expected_revision
        if self.extensions is not None:
            payload["extensions"] = thaw_json_object(self.extensions)
        return payload


@dataclass(frozen=True)
class VerificationEnvelope(EventEnvelope):
    event_type: Literal["verification.recorded"]
    verification_id: str
    target_ref: TypedRef
    target_revision: str
    claim: str
    evidence_refs: tuple[RevisionBoundRef, ...]
    method: str | None
    outcome: str | None
    extensions: FrozenJsonObject | None

    def to_payload(self) -> VerificationPayload:
        from .envelopes import thaw_json_object

        payload: VerificationPayload = {
            "verification_id": self.verification_id,
            "target_ref": self.target_ref.to_payload(),
            "target_revision": self.target_revision,
            "claim": self.claim,
            "evidence_refs": [item.to_payload() for item in self.evidence_refs],
        }
        if self.method is not None:
            payload["method"] = self.method
        if self.outcome is not None:
            payload["outcome"] = self.outcome
        if self.extensions is not None:
            payload["extensions"] = thaw_json_object(self.extensions)
        return payload


@dataclass(frozen=True)
class FindingEnvelope(EventEnvelope):
    event_type: FindingEventType
    finding_id: str
    summary: str | None
    severity: str | None
    evidence_refs: tuple[RevisionBoundRef, ...] | None
    expected_revision: str | None
    reason: str | None
    resolution: str | None
    extensions: FrozenJsonObject | None

    def to_payload(self) -> FindingPayload:
        from .envelopes import thaw_json_object

        payload: FindingPayload = {"finding_id": self.finding_id}
        if self.summary is not None:
            payload["summary"] = self.summary
        if self.severity is not None:
            payload["severity"] = self.severity
        if self.evidence_refs is not None:
            payload["evidence_refs"] = [item.to_payload() for item in self.evidence_refs]
        if self.expected_revision is not None:
            payload["expected_revision"] = self.expected_revision
        if self.reason is not None:
            payload["reason"] = self.reason
        if self.resolution is not None:
            payload["resolution"] = self.resolution
        if self.extensions is not None:
            payload["extensions"] = thaw_json_object(self.extensions)
        return payload


@dataclass(frozen=True)
class DecisionEnvelope(EventEnvelope):
    event_type: DecisionEventType
    decision_id: str
    scope: str | None
    proposal: str | None
    alternatives: tuple[str, ...] | None
    expected_revision: str | None
    rationale: str | None
    evidence_refs: tuple[RevisionBoundRef, ...] | None
    dissent: tuple[str, ...] | None
    reason: str | None
    replacement_decision_id: str | None
    extensions: FrozenJsonObject | None

    def to_payload(self) -> DecisionPayload:
        from .envelopes import thaw_json_object

        payload: DecisionPayload = {"decision_id": self.decision_id}
        if self.scope is not None:
            payload["scope"] = self.scope
        if self.proposal is not None:
            payload["proposal"] = self.proposal
        if self.alternatives is not None:
            payload["alternatives"] = list(self.alternatives)
        if self.expected_revision is not None:
            payload["expected_revision"] = self.expected_revision
        if self.rationale is not None:
            payload["rationale"] = self.rationale
        if self.evidence_refs is not None:
            payload["evidence_refs"] = [item.to_payload() for item in self.evidence_refs]
        if self.dissent is not None:
            payload["dissent"] = list(self.dissent)
        if self.reason is not None:
            payload["reason"] = self.reason
        if self.replacement_decision_id is not None:
            payload["replacement_decision_id"] = self.replacement_decision_id
        if self.extensions is not None:
            payload["extensions"] = thaw_json_object(self.extensions)
        return payload


TruthEvidenceEnvelope: TypeAlias = (
    ArtifactEnvelope | VerificationEnvelope | FindingEnvelope | DecisionEnvelope
)


def parse_truth_evidence_envelope(
    event_type: str, payload: Mapping[str, object]
) -> TruthEvidenceEnvelope | None:
    """Parse a truth or evidence payload that has already passed validation."""

    if event_type in _ARTIFACT_EVENT_TYPES:
        return ArtifactEnvelope(
            event_type=cast(ArtifactEventType, event_type),
            artifact_id=_required_string(payload, "artifact_id"),
            expected_revision=_optional_string(payload, "expected_revision"),
            manifest_ref=_required_string(payload, "manifest_ref"),
            content_revision=_required_string(payload, "revision"),
            classification=_required_string(payload, "classification"),
            extensions=_optional_frozen_object(payload, "extensions"),
        )
    if event_type == "verification.recorded":
        return VerificationEnvelope(
            event_type="verification.recorded",
            verification_id=_required_string(payload, "verification_id"),
            target_ref=TypedRef.from_payload(_required_mapping(payload, "target_ref")),
            target_revision=_required_string(payload, "target_revision"),
            claim=_required_string(payload, "claim"),
            evidence_refs=_required_bound_ref_tuple(payload, "evidence_refs"),
            method=_optional_string(payload, "method"),
            outcome=_optional_string(payload, "outcome"),
            extensions=_optional_frozen_object(payload, "extensions"),
        )
    if event_type in _FINDING_EVENT_TYPES:
        return FindingEnvelope(
            event_type=cast(FindingEventType, event_type),
            finding_id=_required_string(payload, "finding_id"),
            summary=_optional_string(payload, "summary"),
            severity=_optional_string(payload, "severity"),
            evidence_refs=_optional_bound_ref_tuple(payload, "evidence_refs"),
            expected_revision=_optional_string(payload, "expected_revision"),
            reason=_optional_string(payload, "reason"),
            resolution=_optional_string(payload, "resolution"),
            extensions=_optional_frozen_object(payload, "extensions"),
        )
    if event_type in _DECISION_EVENT_TYPES:
        return DecisionEnvelope(
            event_type=cast(DecisionEventType, event_type),
            decision_id=_required_string(payload, "decision_id"),
            scope=_optional_string(payload, "scope"),
            proposal=_optional_string(payload, "proposal"),
            alternatives=_optional_string_tuple(payload, "alternatives"),
            expected_revision=_optional_string(payload, "expected_revision"),
            rationale=_optional_string(payload, "rationale"),
            evidence_refs=_optional_bound_ref_tuple(payload, "evidence_refs"),
            dissent=_optional_string_tuple(payload, "dissent"),
            reason=_optional_string(payload, "reason"),
            replacement_decision_id=_optional_string(payload, "replacement_decision_id"),
            extensions=_optional_frozen_object(payload, "extensions"),
        )
    return None


def _required_string(payload: Mapping[str, object], field: str) -> str:
    return cast(str, payload[field])


def _optional_string(payload: Mapping[str, object], field: str) -> str | None:
    return cast(str | None, payload.get(field))


def _required_mapping(payload: Mapping[str, object], field: str) -> Mapping[str, object]:
    return cast(Mapping[str, object], payload[field])


def _optional_string_tuple(payload: Mapping[str, object], field: str) -> tuple[str, ...] | None:
    if field not in payload:
        return None
    return tuple(cast(list[str], payload[field]))


def _required_bound_ref_tuple(
    payload: Mapping[str, object], field: str
) -> tuple[RevisionBoundRef, ...]:
    return tuple(RevisionBoundRef.from_payload(item) for item in _mapping_list(payload, field))


def _optional_bound_ref_tuple(
    payload: Mapping[str, object], field: str
) -> tuple[RevisionBoundRef, ...] | None:
    if field not in payload:
        return None
    return _required_bound_ref_tuple(payload, field)


def _optional_frozen_object(payload: Mapping[str, object], field: str) -> FrozenJsonObject | None:
    if field not in payload:
        return None
    from .envelopes import freeze_json_object

    return freeze_json_object(_required_mapping(payload, field))


def _mapping_list(payload: Mapping[str, object], field: str) -> list[Mapping[str, object]]:
    return cast(list[Mapping[str, object]], payload[field])
