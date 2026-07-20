"""Domain-neutral immutable event store."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_commons.config import CommonsPaths
from agent_commons.core.canonical import (
    canonical_json_bytes,
    canonical_json_file_bytes,
    canonical_sha256,
    loads_json_strict,
    sha256_bytes,
)
from agent_commons.core.ids import is_typed_id
from agent_commons.core.schema_registry import SchemaRegistry
from agent_commons.errors import IdempotencyConflictError, IntegrityError, ValidationError
from agent_commons.storage.atomic import atomic_write_immutable
from agent_commons.storage.idempotency import IdempotencyStore

EventValidator = Callable[[Mapping[str, Any]], None]


def _parse_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValidationError(f"invalid event timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValidationError("event timestamp must include a timezone")
    return parsed.astimezone(UTC)


def semantic_event_body(event: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in event.items() if key not in {"event_id", "recorded_at"}}


@dataclass(frozen=True)
class EventRecord:
    event_id: str
    path: Path
    sha256: str
    event: Mapping[str, Any]
    created: bool
    repaired: bool


class EventStore:
    """Append-only JSON event storage with domain validators injected by callers."""

    def __init__(
        self,
        paths: CommonsPaths,
        schemas: SchemaRegistry,
        *,
        workspace_id: str | None = None,
        idempotency: IdempotencyStore | None = None,
        validators: Iterable[EventValidator] = (),
    ) -> None:
        self.paths = paths
        self.schemas = schemas
        self.idempotency = idempotency or IdempotencyStore(
            paths, schemas, workspace_id=workspace_id
        )
        self.validators = tuple(validators)

    def append(
        self,
        event: Mapping[str, Any],
        *,
        producer_namespace: str | None = None,
    ) -> EventRecord:
        candidate = dict(event)
        if "event_id" in candidate or "recorded_at" in candidate:
            raise ValidationError("event_id and recorded_at are assigned by EventStore")
        candidate.setdefault("schema", "commons.event.v1")
        workspace_id = candidate.get("workspace_id")
        if not isinstance(workspace_id, str) or not workspace_id:
            raise ValidationError("an explicit workspace ID is required")
        self.idempotency.bind_workspace(workspace_id)
        namespace = producer_namespace or candidate.get("idempotency_namespace")
        if not isinstance(namespace, str) or not namespace:
            raise ValidationError("an explicit idempotency namespace is required")
        if candidate.get("idempotency_namespace") not in (None, namespace):
            raise ValidationError("producer namespace conflicts with event metadata")
        candidate["idempotency_namespace"] = namespace

        provisional = {
            **candidate,
            "event_id": "evt.00000000000000000000000000",
            "recorded_at": "1970-01-01T00:00:00Z",
        }
        self._validate(provisional)
        semantic_sha256 = canonical_sha256(semantic_event_body(provisional))
        idempotency_key = str(candidate.get("idempotency_key") or "")

        reservation = self.idempotency.lookup(namespace=namespace, key=idempotency_key)
        existing_record: EventRecord | None = None
        if reservation is None:
            existing_record = self._find_existing(namespace, idempotency_key)
            if existing_record is not None:
                existing_semantic = canonical_sha256(semantic_event_body(existing_record.event))
                if existing_semantic != semantic_sha256:
                    raise IdempotencyConflictError(
                        "idempotency identity already belongs to different event content"
                    )
            reservation = self.idempotency.reserve(
                namespace=namespace,
                key=idempotency_key,
                semantic_sha256=semantic_sha256,
                event_id_factory=(
                    (lambda: existing_record.event_id) if existing_record is not None else None
                ),
                recorded_at=(
                    str(existing_record.event["recorded_at"])
                    if existing_record is not None
                    else None
                ),
            )
        else:
            if reservation.semantic_sha256 != semantic_sha256:
                raise IdempotencyConflictError(
                    "idempotency identity already belongs to different event content"
                )
            try:
                existing_record = self.get(reservation.event_id)
            except FileNotFoundError:
                existing_record = self._find_existing(namespace, idempotency_key)
                if existing_record is not None and existing_record.event_id != reservation.event_id:
                    raise IntegrityError(
                        "idempotency receipt conflicts with an existing canonical event"
                    ) from None
            if existing_record is not None:
                existing_semantic = canonical_sha256(semantic_event_body(existing_record.event))
                if existing_semantic != semantic_sha256:
                    raise IntegrityError(
                        "canonical event content conflicts with its idempotency receipt"
                    )

        candidate["event_id"] = reservation.event_id
        candidate["recorded_at"] = reservation.recorded_at
        self._validate(candidate)
        timestamp = _parse_datetime(candidate["recorded_at"])
        path = (
            self.paths.events
            / f"{timestamp.year:04d}"
            / f"{timestamp.month:02d}"
            / f"{timestamp.day:02d}"
            / f"{candidate['event_id']}.json"
        )
        existed_before = path.exists()
        result = atomic_write_immutable(path, canonical_json_file_bytes(candidate))
        return EventRecord(
            event_id=candidate["event_id"],
            path=path,
            sha256=sha256_bytes(canonical_json_bytes(candidate)),
            event=candidate,
            created=result.created,
            repaired=(not reservation.created and not existed_before and result.created),
        )

    def append_event(
        self,
        *,
        workspace_id: str,
        event_type: str,
        payload_schema: str,
        payload: Mapping[str, Any],
        actor: Mapping[str, Any],
        subject_refs: Iterable[Mapping[str, str]],
        idempotency_namespace: str,
        idempotency_key: str,
        provenance: Mapping[str, Any],
        occurred_at: str | None = None,
        correlation_id: str | None = None,
        causation_event_id: str | None = None,
        relations: Iterable[Mapping[str, Any]] = (),
        tags: Iterable[str] = (),
        extensions: Mapping[str, Any] | None = None,
    ) -> EventRecord:
        body: dict[str, Any] = {
            "schema": "commons.event.v1",
            "payload_schema": payload_schema,
            "workspace_id": workspace_id,
            "event_type": event_type,
            "actor": dict(actor),
            "subject_refs": [dict(item) for item in subject_refs],
            "idempotency_namespace": idempotency_namespace,
            "idempotency_key": idempotency_key,
            "provenance": dict(provenance),
            "payload": dict(payload),
            "relations": [dict(item) for item in relations],
            "tags": sorted(set(tags)),
        }
        for name, value in (
            ("occurred_at", occurred_at),
            ("correlation_id", correlation_id),
            ("causation_event_id", causation_event_id),
        ):
            if value is not None:
                body[name] = value
        if extensions:
            body["extensions"] = dict(extensions)
        return self.append(body)

    def get(self, event_id: str) -> EventRecord:
        if not is_typed_id(event_id, "evt"):
            raise FileNotFoundError(f"invalid event ID: {event_id}")
        matches = list(self.paths.events.glob(f"*/*/*/{event_id}.json"))
        if not matches:
            raise FileNotFoundError(f"event not found: {event_id}")
        if len(matches) != 1:
            raise IntegrityError(f"event ID appears at multiple paths: {event_id}")
        return self.read_path(matches[0])

    def iter_events(self) -> Iterable[EventRecord]:
        if not self.paths.events.exists():
            return
        for path in sorted(self.paths.events.glob("*/*/*/evt.*.json")):
            yield self.read_path(path)

    def read_path(self, path: str | Path) -> EventRecord:
        path = Path(path)
        if path.is_symlink():
            raise IntegrityError(f"canonical event path must not be a symlink: {path}")
        try:
            relative_parts = path.resolve().relative_to(self.paths.events.resolve()).parts
        except ValueError as exc:
            raise IntegrityError(f"event path is outside canonical storage: {path}") from exc
        if len(relative_parts) != 4:
            raise IntegrityError(f"event path has an invalid layout: {path}")
        raw = path.read_bytes()
        value = loads_json_strict(raw)
        if not isinstance(value, dict):
            raise IntegrityError(f"event is not an object: {path}")
        if raw != canonical_json_file_bytes(value):
            raise IntegrityError(f"event file is not canonical JSON: {path}")
        if path.stem != value.get("event_id"):
            raise IntegrityError(f"event filename does not match event_id: {path}")
        self._validate(value)
        timestamp = _parse_datetime(value["recorded_at"])
        expected_parts = (f"{timestamp.year:04d}", f"{timestamp.month:02d}", f"{timestamp.day:02d}")
        if tuple(relative_parts[:3]) != expected_parts:
            raise IntegrityError(f"event path does not match recorded_at: {path}")
        return EventRecord(
            event_id=value["event_id"],
            path=path,
            sha256=sha256_bytes(canonical_json_bytes(value)),
            event=value,
            created=False,
            repaired=False,
        )

    def _find_existing(self, namespace: str, key: str) -> EventRecord | None:
        matches = [
            record
            for record in self.iter_events()
            if record.event.get("idempotency_namespace") == namespace
            and record.event.get("idempotency_key") == key
        ]
        if len(matches) > 1:
            raise IntegrityError("multiple events share one idempotency identity")
        return matches[0] if matches else None

    def _validate(self, event: Mapping[str, Any]) -> None:
        self.schemas.validate_event(event)
        for validator in self.validators:
            validator(event)
