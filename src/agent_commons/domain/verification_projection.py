"""Frozen verification projection records and their single-event reducer.

The canonical ledger continues to store the ``verification.recorded`` payload
unchanged.  This module is one step inside that boundary: it turns the already
validated typed envelope into an immutable projected record while retaining the
existing mapping-shaped read contract for callers that have not moved to typed
records yet.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import NotRequired, cast

from .envelopes import FrozenJsonObject, JsonValue, TypedRef, freeze_json_object, thaw_json_object
from .snapshot import ProjectSnapshot
from .task_review_envelopes import RevisionBoundRef
from .truth_evidence_envelopes import VerificationEnvelope, VerificationPayload


class VerificationRecordPayload(VerificationPayload):
    """The historical verification read shape, represented from a typed record."""

    id: str
    state: str
    revision: str
    effective_revision: str
    recorded_at: str
    actor: dict[str, JsonValue]
    author_session_ids: list[str]
    stale: bool
    method: NotRequired[str]
    outcome: NotRequired[str]
    extensions: NotRequired[dict[str, JsonValue]]


@dataclass(frozen=True)
class VerificationRecord(Mapping[str, object]):
    """One immutable verification fact as projected from its only event."""

    verification_id: str
    target_ref: TypedRef
    target_revision: str
    claim: str
    evidence_refs: tuple[RevisionBoundRef, ...]
    method: str | None
    outcome: str | None
    extensions: FrozenJsonObject | None
    revision: str
    effective_revision: str
    recorded_at: str
    actor: FrozenJsonObject
    author_session_ids: tuple[str, ...]
    payload: FrozenJsonObject
    stale: bool = False

    @classmethod
    def from_envelope(
        cls, envelope: VerificationEnvelope, event: Mapping[str, object]
    ) -> VerificationRecord:
        """Build the record after event validation has accepted its envelope."""

        actor = event.get("actor")
        if not isinstance(actor, Mapping):
            raise TypeError("verified projection event actor must be an object")
        event_id = str(event["event_id"])
        actor_session_id = str(actor.get("session_id", ""))
        return cls(
            verification_id=envelope.verification_id,
            target_ref=envelope.target_ref,
            target_revision=envelope.target_revision,
            claim=envelope.claim,
            evidence_refs=envelope.evidence_refs,
            method=envelope.method,
            outcome=envelope.outcome,
            extensions=envelope.extensions,
            revision=event_id,
            effective_revision=str(event.get("_effective_correction_id") or event_id),
            recorded_at=str(event.get("recorded_at", "")),
            actor=freeze_json_object(actor),
            author_session_ids=(actor_session_id,) if actor_session_id else (),
            payload=freeze_json_object(envelope.to_payload()),
        )

    def with_stale(self, stale: bool) -> VerificationRecord:
        """Return the same projected fact with its derived staleness refreshed."""

        return replace(self, stale=stale)

    def to_dict(self) -> VerificationRecordPayload:
        """Return the pre-existing JSON-shaped read contract without exposing internals."""

        payload = cast(VerificationRecordPayload, thaw_json_object(self.payload))
        payload.update(
            {
                "id": self.verification_id,
                "state": "recorded",
                "revision": self.revision,
                "effective_revision": self.effective_revision,
                "recorded_at": self.recorded_at,
                "actor": thaw_json_object(self.actor),
                "author_session_ids": list(self.author_session_ids),
                "stale": self.stale,
            }
        )
        return payload

    def __getitem__(self, key: str) -> object:
        return self.to_dict()[key]

    def __iter__(self):
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())


def apply_verification_record(
    collection: dict[str, VerificationRecord],
    envelope: VerificationEnvelope,
    event: Mapping[str, object],
) -> None:
    """Apply the terminal verification event without retaining a mutable record."""

    collection[envelope.verification_id] = VerificationRecord.from_envelope(envelope, event)


def refresh_verification_staleness(
    snapshot: ProjectSnapshot,
    *,
    current_evidence_revision: Callable[[ProjectSnapshot, Mapping[str, object]], str | None],
    has_stale_evidence: Callable[[Mapping[str, object]], bool],
) -> None:
    """Replace each verification record when its derived evidence status changes."""

    for identifier, verification in snapshot.verifications.items():
        target_ref = verification.target_ref.to_payload()
        target_kind = verification.target_ref.kind
        target_id = verification.target_ref.identifier
        current = current_evidence_revision(snapshot, target_ref)
        if target_kind == "task":
            task = snapshot.tasks.get(target_id)
            if task and task.get("state") == "accepted":
                accepted_subject_revision = task.get("accepted_subject_revision")
                if isinstance(accepted_subject_revision, str):
                    current = accepted_subject_revision
        stale = (
            current is None
            or verification.target_revision != current
            or has_stale_evidence(verification)
            or (
                target_kind == "task"
                and bool((snapshot.tasks.get(target_id) or {}).get("artifact_stale"))
            )
        )
        snapshot.verifications[identifier] = verification.with_stale(stale)
        if stale:
            snapshot.warnings.append(
                f"verification {identifier} is stale for current target revision"
            )
