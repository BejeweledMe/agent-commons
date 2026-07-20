"""Universal application service for one Agent Commons workspace."""

from __future__ import annotations

import fcntl
import hashlib
import os
import uuid
from collections.abc import Iterable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml

from agent_commons import __version__
from agent_commons.config import CommonsPaths
from agent_commons.coordination import ClaimService, Session, SessionRegistry, SourceProducer
from agent_commons.core.canonical import canonical_sha256
from agent_commons.core.ids import is_typed_id, stable_id
from agent_commons.core.refs import normalize_ref
from agent_commons.core.schema_registry import SchemaRegistry
from agent_commons.domain.invalidations import derive_invalidation_state
from agent_commons.domain.lifecycle import entity, validate_transition
from agent_commons.domain.projection import ProjectSnapshot, project_events
from agent_commons.domain.revisions import resolve_revision, structural_correction_changes
from agent_commons.domain.validation import validate_payload
from agent_commons.errors import (
    ConfigurationError,
    IdempotencyConflictError,
    IntegrityError,
    LifecycleConflictError,
    ValidationError,
)
from agent_commons.index import SQLiteIndex
from agent_commons.integrations import initialize_workspace
from agent_commons.security import SecurityPolicy
from agent_commons.storage import EventRecord, EventStore, ManifestStore, ReceiptRecovery
from agent_commons.storage.events import semantic_event_body
from agent_commons.views import orientation, render_views

PAYLOAD_SCHEMAS = {
    "objective": "commons.payload.objective.v1",
    "task": "commons.payload.task.v1",
    "thread": "commons.payload.thread.v1",
    "artifact": "commons.payload.artifact.v1",
    "review": "commons.payload.review.v1",
    "verification": "commons.payload.verification.v1",
    "finding": "commons.payload.finding.v1",
    "decision": "commons.payload.decision.v1",
    "handoff": "commons.payload.handoff.v1",
    "delegation": "commons.payload.delegation.v1",
    "event": "commons.payload.maintenance.v1",
}

_COLLECTIONS = {
    "objective": "objectives",
    "task": "tasks",
    "thread": "threads",
    "artifact": "artifacts",
    "review": "reviews",
    "verification": "verifications",
    "finding": "findings",
    "decision": "decisions",
    "handoff": "handoffs",
    "delegation": "delegations",
}


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigurationError(f"{label} must be a mapping")
    return dict(value)


def _nonempty_list(values: Sequence[str], label: str) -> list[str]:
    result = [str(value).strip() for value in values]
    if not result or any(not value for value in result):
        raise ValidationError(f"{label} must contain non-empty values")
    return result


def _optional_list(values: Sequence[str], label: str) -> list[str]:
    result = [str(value).strip() for value in values]
    if any(not value for value in result):
        raise ValidationError(f"{label} must contain non-empty values")
    return result


def _public_session(session: Session, *, include_nonce: bool = False) -> dict[str, Any]:
    value = asdict(session)
    value["capabilities"] = list(session.capabilities)
    if not include_nonce:
        value.pop("nonce", None)
    return value


def _public_claim(value: Any, *, include_nonce: bool = False) -> dict[str, Any]:
    result = asdict(value)
    result["resources"] = list(value.resources)
    if not include_nonce:
        result.pop("nonce", None)
    return result


class CommonsManager:
    """One strict service boundary for canonical and coordination workflows."""

    def __init__(
        self,
        repo_root: str | Path,
        *,
        session_id: str | None = None,
        state_root: str | Path | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).expanduser().resolve()
        self.paths = CommonsPaths.for_workspace(self.repo_root, state_root=state_root)
        self.workspace_config = self._load_workspace_config()
        self.workspace_id = str(self.workspace_config["workspace_id"])
        security_config = self.workspace_config.get("security", {})
        if not isinstance(security_config, Mapping):
            raise ConfigurationError("workspace security configuration must be a mapping")
        try:
            self.policy = SecurityPolicy.from_mapping(security_config)
        except (TypeError, ValueError) as exc:
            raise ConfigurationError("workspace security configuration is invalid") from exc
        self.policy.assert_safe(self.workspace_config, context="workspace configuration")
        self.paths.ensure_layout()
        self.schemas = SchemaRegistry()
        self.sessions = SessionRegistry(
            self.repo_root,
            state_root=self.paths.state_root,
            policy=self.policy,
        )
        self.claims = ClaimService(
            self.repo_root,
            sessions=self.sessions,
            state_root=self.paths.state_root,
            policy=self.policy,
        )
        self.events = EventStore(
            self.paths,
            self.schemas,
            workspace_id=self.workspace_id,
            validators=(self._validate_stored_event,),
        )
        self.receipt_recovery = ReceiptRecovery(
            self.paths,
            self.schemas,
            self.events.idempotency,
            workspace_id=self.workspace_id,
        )
        self.manifests = ManifestStore(
            self.paths,
            self.schemas,
            validators=(self._validate_stored_manifest,),
        )
        self.session_id = session_id

    @staticmethod
    def initialize(
        repo_root: str | Path,
        *,
        integrations: Iterable[str] | str = ("codex", "claude"),
        workspace_name: str | None = None,
        replace_onboarding: bool = False,
        replace_skills: bool = False,
    ) -> dict[str, Any]:
        report = initialize_workspace(
            repo_root,
            integrations=integrations,
            workspace_name=workspace_name,
            replace_onboarding=replace_onboarding,
            replace_skills=replace_skills,
        )
        return {
            "workspace": report.workspace,
            "workspace_id": report.workspace_id,
            "integrations": list(report.integrations),
            "changes": [asdict(change) for change in report.changes],
            "changed": report.changed,
        }

    def _load_workspace_config(self) -> dict[str, Any]:
        path = self.paths.commons_root / "workspace.yaml"
        if not path.is_file() or path.is_symlink():
            raise ConfigurationError("workspace is not initialized safely")
        try:
            value = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            raise ConfigurationError("workspace configuration is unreadable") from exc
        config = _require_mapping(value, "workspace configuration")
        if config.get("schema") != "agent-commons.workspace.v1":
            raise ConfigurationError("unsupported workspace configuration schema")
        if not is_typed_id(config.get("workspace_id"), "workspace"):
            raise ConfigurationError("workspace configuration has an invalid workspace_id")
        workspace = config.get("workspace")
        if not isinstance(workspace, Mapping) or not str(workspace.get("name", "")).strip():
            raise ConfigurationError("workspace configuration has no workspace name")
        policy = config.get("policy", {})
        if not isinstance(policy, Mapping):
            raise ConfigurationError("workspace policy configuration must be a mapping")
        unsupported_policy = sorted(policy)
        if unsupported_policy:
            raise ConfigurationError(
                "unsupported workspace policy keys: " + ", ".join(unsupported_policy)
            )
        return config

    def _validate_stored_event(self, event: Mapping[str, Any]) -> None:
        validate_payload(str(event.get("event_type", "")), event.get("payload") or {})
        self.policy.assert_safe(event, context="canonical event")

    def _validate_stored_manifest(self, manifest: Mapping[str, Any]) -> None:
        self.policy.assert_safe(manifest, context="canonical manifest")

    def _active_session(self) -> Session:
        return self.sessions.require_active(self.session_id)

    def _actor(self) -> dict[str, Any]:
        actor = self.sessions.assert_can_write(self.session_id)
        self.policy.assert_safe(actor, context="canonical event actor")
        return actor

    def start_session(
        self,
        *,
        stable_instance_id: str,
        principal: str,
        client: str,
        software: str,
        role: str,
        capabilities: Sequence[str] = (),
        model_family: str | None = None,
        model: str | None = None,
        source_producer: SourceProducer | Mapping[str, Any] | None = None,
        ttl_seconds: int = 8 * 3600,
    ) -> dict[str, Any]:
        session = self.sessions.open_session(
            stable_instance_id=stable_instance_id,
            principal=principal,
            client=client,
            software=software,
            role=role,
            capabilities=capabilities,
            model_family=model_family,
            model=model,
            source_producer=source_producer,
            ttl_seconds=ttl_seconds,
        )
        return _public_session(session, include_nonce=True)

    def show_session(self, session_id: str | None = None) -> dict[str, Any] | list[dict[str, Any]]:
        selected = session_id or self.session_id
        sessions = self.sessions.list_sessions(active_only=False)
        if selected is None:
            return [_public_session(item) for item in sessions if item.status == "active"]
        for item in sessions:
            if item.session_id == selected:
                return _public_session(item)
        raise ValidationError(f"session does not exist: {selected}")

    def end_session(self, *, nonce: str) -> dict[str, Any]:
        session = self._active_session()
        closed = self.sessions.close(session.session_id, nonce=nonce)
        return _public_session(closed)

    def heartbeat_session(self, *, nonce: str, ttl_seconds: int = 8 * 3600) -> dict[str, Any]:
        session = self._active_session()
        renewed = self.sessions.heartbeat(
            session.session_id,
            nonce=nonce,
            ttl_seconds=ttl_seconds,
        )
        return _public_session(renewed, include_nonce=True)

    def _records_and_snapshot(self) -> tuple[list[EventRecord], ProjectSnapshot]:
        records = list(self.events.iter_events())
        for record in records:
            if record.event.get("workspace_id") != self.workspace_id:
                raise IntegrityError("canonical event belongs to a different workspace")
        manifests = list(self.manifests.iter_manifests())
        return records, project_events(
            (record.event for record in records),
            known_manifest_ids=(record.manifest_id for record in manifests),
        )

    def snapshot(self) -> ProjectSnapshot:
        return self._records_and_snapshot()[1]

    def _receipt_issues(
        self,
        records: Sequence[EventRecord],
    ) -> list[str]:
        report = self.receipt_recovery.status(records)
        for abandonment in self.events.idempotency.iter_abandonments():
            self.policy.assert_safe(
                abandonment,
                context="stored idempotency abandonment",
            )
        return list(report["issues"])

    def _guard_integrity(
        self,
        *,
        allow_missing_receipt: tuple[str, str] | None = None,
        actor: Mapping[str, Any] | None = None,
        allowed_projection_warnings: frozenset[str] = frozenset(),
        recovery_invalidation_payload: Mapping[str, Any] | None = None,
    ) -> ProjectSnapshot:
        records, snapshot = self._records_and_snapshot()
        self.receipt_recovery.prepare_for_write(
            records,
            actor=actor or self._actor(),
            requested_identity=allow_missing_receipt,
        )
        manifest_issues, _ = self._manifest_reference_issues(records, snapshot)
        if manifest_issues:
            raise IntegrityError(manifest_issues[0])
        allowed_warnings = set(allowed_projection_warnings)
        if recovery_invalidation_payload is not None:
            allowed_warnings.update(
                self._warnings_resolved_by_invalidation(
                    records,
                    snapshot,
                    recovery_invalidation_payload,
                )
            )
        hard_warnings = [
            warning
            for warning in self._hard_projection_warnings(snapshot.warnings)
            if warning not in allowed_warnings
        ]
        if hard_warnings:
            raise IntegrityError(hard_warnings[0])
        return snapshot

    def _manifest_reference_issues(
        self,
        records: Sequence[EventRecord],
        snapshot: ProjectSnapshot,
    ) -> tuple[list[str], list[str]]:
        manifests = {record.manifest_id: record for record in self.manifests.iter_manifests()}
        issues: list[str] = []
        referenced: set[str] = set()
        for record in records:
            if record.event_id not in snapshot.effective_event_revisions:
                continue
            if record.event.get("event_type") not in {
                "artifact.registered",
                "artifact.revised",
            }:
                continue
            payload = record.event.get("payload") or {}
            manifest_ref = str(payload.get("manifest_ref", ""))
            referenced.add(manifest_ref)
            manifest_record = manifests.get(manifest_ref)
            if manifest_record is None:
                issues.append(
                    f"artifact event {record.event_id} references missing manifest {manifest_ref}"
                )
                continue
            manifest = manifest_record.manifest
            expected = {
                "artifact_id": payload.get("artifact_id"),
                "revision": payload.get("revision"),
                "classification": payload.get("classification"),
            }
            mismatched = sorted(
                field for field, value in expected.items() if manifest.get(field) != value
            )
            if mismatched:
                issues.append(
                    f"artifact event {record.event_id} manifest metadata mismatch: "
                    + ", ".join(mismatched)
                )
        warnings = [
            f"orphan manifest {manifest_id} has no effective artifact event"
            for manifest_id in sorted(set(manifests) - referenced)
        ]
        return issues, warnings

    def _warnings_resolved_by_invalidation(
        self,
        records: Sequence[EventRecord],
        snapshot: ProjectSnapshot,
        payload: Mapping[str, Any],
    ) -> frozenset[str]:
        """Allow only an invalidation that strictly improves existing hard warnings."""

        current_hard = set(self._hard_projection_warnings(snapshot.warnings))
        if not current_hard:
            return frozenset()
        target = payload.get("target_ref")
        if not isinstance(target, Mapping) or target.get("kind") != "event":
            return frozenset()
        target_event_id = str(target.get("id", ""))
        if not any(record.event_id == target_event_id for record in records):
            return frozenset()

        synthetic_event_id = stable_id(
            "evt",
            "\0".join(
                (
                    "invalidation-integrity-preflight",
                    self.workspace_id,
                    canonical_sha256(payload),
                )
            ),
        )
        simulated = project_events(
            [
                *(record.event for record in records),
                {
                    "event_id": synthetic_event_id,
                    "recorded_at": "9999-12-31T23:59:59Z",
                    "workspace_id": self.workspace_id,
                    "event_type": "event.invalidated",
                    "actor": {"session_id": self.session_id or "integrity-preflight"},
                    "payload": dict(payload),
                    "relations": [],
                },
            ]
        )
        simulated_hard = set(self._hard_projection_warnings(simulated.warnings))
        current_measure = self._hard_warning_measure(current_hard)
        simulated_measure = self._hard_warning_measure(simulated_hard)
        if set(simulated_measure) - set(current_measure):
            return frozenset()
        if any(simulated_measure.get(key, 0) > weight for key, weight in current_measure.items()):
            return frozenset()
        if not any(
            simulated_measure.get(key, 0) < weight for key, weight in current_measure.items()
        ):
            return frozenset()
        # Existing unrelated warnings may remain while conflicts with more than
        # two heads are healed one event at a time. The preflight above ensures
        # that every warning locus is non-worsening and at least one improves.
        return frozenset(current_hard)

    @staticmethod
    def _hard_warning_measure(warnings: Iterable[str]) -> dict[str, int]:
        """Measure dynamic conflict warnings by locus and number of participants."""

        measure: dict[str, int] = {}
        dynamic_prefixes = (
            "conflicting accepted decisions for scope ",
            "conflicting concurrent ",
        )
        for warning in warnings:
            signature = warning
            weight = 1
            if warning.startswith(dynamic_prefixes):
                locus, separator, participants = warning.partition(": ")
                if separator:
                    signature = locus
                    weight = max(
                        1,
                        len(
                            [participant for participant in participants.split(", ") if participant]
                        ),
                    )
            measure[signature] = measure.get(signature, 0) + weight
        return measure

    @staticmethod
    def _hard_projection_warnings(warnings: Sequence[str]) -> list[str]:
        markers = ("conflict", "rejected by lifecycle", "rejected by domain validation")
        return [
            warning for warning in warnings if any(marker in warning.lower() for marker in markers)
        ]

    def _sync_index(self) -> dict[str, int]:
        with SQLiteIndex(self.paths, self.events, self.manifests) as index:
            return asdict(index.sync())

    @contextmanager
    def _canonical_write_lock(self) -> Iterable[None]:
        """Serialize lifecycle CAS and append across processes and worktrees."""

        self.paths.state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_path = self.paths.state_root / "canonical-write.lock"
        with lock_path.open("a+b") as handle:
            try:
                os.fchmod(handle.fileno(), 0o600)
            except OSError:
                pass
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _idempotency_key(self, event_type: str, value: str | None) -> str:
        key = value or f"{event_type}:{uuid.uuid4().hex}"
        if not key.strip() or len(key) > 512:
            raise ValidationError("idempotency_key must contain 1 to 512 characters")
        self.policy.assert_safe(key, context="idempotency key")
        return key

    def _namespace(self, session: Session) -> str:
        return f"commons:{self.workspace_id}:{session.session_id}"

    def _event_for_idempotency_identity(self, namespace: str, key: str) -> EventRecord | None:
        matches = [
            record
            for record in self.events.iter_events()
            if record.event.get("idempotency_namespace") == namespace
            and record.event.get("idempotency_key") == key
        ]
        if len(matches) > 1:
            raise IntegrityError("multiple canonical events share an idempotency identity")
        return matches[0] if matches else None

    def _new_entity_id(self, kind: str, event_type: str, key: str) -> str:
        session = self._active_session()
        seed = "\0".join((self.workspace_id, session.session_id, event_type, key))
        return stable_id(kind, seed)

    @staticmethod
    def _relation(
        subject: Mapping[str, str], predicate: str, object_ref: Mapping[str, str]
    ) -> dict[str, Any]:
        return {
            "subject": normalize_ref(subject),
            "predicate": predicate,
            "object": normalize_ref(object_ref),
        }

    def _assert_refs_exist(
        self, refs: Sequence[Mapping[str, str]], snapshot: ProjectSnapshot | None = None
    ) -> list[dict[str, str]]:
        normalized = [normalize_ref(value) for value in refs]
        current = snapshot or self.snapshot()
        for ref in normalized:
            kind, identifier = ref["kind"], ref["id"]
            if kind == "event":
                self.events.get(identifier)
            elif kind == "manifest":
                self.manifests.get(identifier)
            elif kind in _COLLECTIONS:
                if entity(current, kind, identifier) is None:
                    raise ValidationError(f"referenced {kind} does not exist: {identifier}")
            else:
                raise ValidationError(f"unsupported canonical reference kind: {kind}")
        return normalized

    def _bind_evidence_refs(
        self,
        refs: Sequence[Mapping[str, str]],
        snapshot: ProjectSnapshot | None = None,
    ) -> list[dict[str, Any]]:
        current = snapshot or self.snapshot()
        normalized = self._assert_refs_exist(refs, current)
        bound: list[dict[str, Any]] = []
        for ref in normalized:
            kind, identifier = ref["kind"], ref["id"]
            if kind == "event":
                if (
                    identifier in current.invalid_event_ids
                    or (
                        "event",
                        identifier,
                    )
                    in current.stale_refs
                ):
                    raise ValidationError(
                        f"evidence event is not currently effective: {identifier}"
                    )
                record = self.events.get(identifier)
                revision = current.effective_event_revisions.get(identifier)
                if revision is None:
                    if not str(record.event.get("event_type", "")).startswith("event."):
                        raise ValidationError(
                            f"evidence event is not currently projected: {identifier}"
                        )
                    revision = identifier
            elif kind == "manifest":
                revision = identifier
            else:
                current_entity = entity(current, kind, identifier)
                if current_entity is None:  # pragma: no cover - asserted immediately above
                    raise ValidationError(f"referenced {kind} does not exist: {identifier}")
                revision = str(
                    current_entity.get("effective_revision") or current_entity.get("revision")
                )
            bound.append({"ref": ref, "revision": revision})
        return bound

    @staticmethod
    def _maintenance_subject(event_type: str, payload: Mapping[str, Any]) -> dict[str, str]:
        if event_type == "event.corrected":
            return {"kind": "event", "id": str(payload["target_event_id"])}
        if event_type == "event.invalidated":
            return normalize_ref(payload["target_ref"])
        if event_type == "event.invalidation_revoked":
            return {"kind": "event", "id": str(payload["invalidation_event_id"])}
        raise ValidationError(f"{event_type} has no canonical subject identity")

    def _enforce_workspace_policy(
        self,
        snapshot: ProjectSnapshot,
        event_type: str,
        payload: Mapping[str, Any],
        session: Session,
    ) -> None:
        if event_type == "handoff.acknowledged":
            handoff = entity(snapshot, "handoff", str(payload["handoff_id"])) or {}
            recipients = set(map(str, handoff.get("to") or []))
            if not recipients.intersection({"*", session.session_id, session.role}):
                raise LifecycleConflictError(
                    "handoff can only be acknowledged by its session or role recipient"
                )

        if event_type == "delegation.started":
            self.sessions.require_active(str(payload["child_session_id"]))

        if event_type in {
            "event.corrected",
            "event.invalidated",
            "event.invalidation_revoked",
        }:
            self._enforce_maintenance_policy(snapshot, event_type, payload)

    def _enforce_maintenance_policy(
        self,
        snapshot: ProjectSnapshot,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        if event_type == "event.corrected":
            target = self.events.get(str(payload["target_event_id"]))
            if target.event.get("event_type", "").startswith("event."):
                raise LifecycleConflictError("maintenance events cannot be corrected")
            if target.event_id in snapshot.invalid_event_ids:
                raise LifecycleConflictError("an invalidated event cannot be corrected")
            if target.sha256 != payload["expected_target_sha256"]:
                raise LifecycleConflictError(
                    "expected_target_sha256 does not match the immutable target event"
                )
            replacement = dict(payload["replacement_payload"])
            target_event_type = str(target.event["event_type"])
            target_spec = validate_payload(target_event_type, replacement)
            self.schemas.validate(str(target.event["payload_schema"]), replacement)
            original_payload = target.event.get("payload") or {}
            structural_changes = structural_correction_changes(original_payload, replacement)
            if structural_changes:
                raise LifecycleConflictError(
                    "a correction cannot change reference or causal fields: "
                    + ", ".join(structural_changes)
                )
            if target_spec.entity_id_field is not None:
                identity_field = target_spec.entity_id_field
                original_identity = original_payload.get(identity_field)
                if replacement.get(identity_field) != original_identity:
                    raise LifecycleConflictError(
                        f"a correction cannot change subject identity {identity_field}"
                    )
            corrections = [
                record.event
                for record in self.events.iter_events()
                if record.event.get("event_type") == "event.corrected"
                and (record.event.get("payload") or {}).get("target_event_id") == target.event_id
            ]
            current = resolve_revision(target.event, corrections)
            if current.conflict and set(current.issues) != {
                "corrections have multiple active heads"
            }:
                raise IntegrityError(
                    f"target event {target.event_id} already has conflicting corrections"
                )
            supplied = set(map(str, payload.get("superseded_correction_event_ids") or []))
            if supplied != set(current.active_heads):
                raise LifecycleConflictError(
                    "superseded_correction_event_ids must name every active correction head"
                )
            return

        if event_type == "event.invalidated":
            target_id = str((payload.get("target_ref") or {})["id"])
            target = self.events.get(target_id)
            if target.event.get("event_type", "").startswith("event."):
                raise LifecycleConflictError(
                    "maintenance events cannot be invalidated; revoke an invalidation instead"
                )
            if target_id in snapshot.invalid_event_ids:
                raise LifecycleConflictError("target event is already invalidated")
            return

        invalidation_id = str(payload["invalidation_event_id"])
        invalidation = self.events.get(invalidation_id)
        if invalidation.event.get("event_type") != "event.invalidated":
            raise LifecycleConflictError(
                "invalidation_event_id must identify an event.invalidated record"
            )
        records = [record.event for record in self.events.iter_events()]
        relations = [relation for event in records for relation in (event.get("relations") or [])]
        state = derive_invalidation_state(records, relations)
        if invalidation_id not in state.active_invalidation_ids:
            raise LifecycleConflictError("invalidation is not active")

    def record_event(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        idempotency_key: str,
        relations: Sequence[Mapping[str, Any]] = (),
        tags: Sequence[str] = (),
        _manifest: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload_value = dict(payload)
        spec = validate_payload(event_type, payload_value)
        family = event_type.split(".", 1)[0]
        try:
            payload_schema = PAYLOAD_SCHEMAS[family]
        except KeyError as exc:
            raise ValidationError(f"no canonical payload schema for {event_type}") from exc
        self.schemas.validate(payload_schema, payload_value)
        if spec.entity_kind is None:
            raise ValidationError(f"{event_type} has no canonical subject identity")
        subject = (
            {
                "kind": spec.entity_kind,
                "id": str(payload_value.get(spec.entity_id_field, "")),
            }
            if spec.entity_id_field is not None
            else self._maintenance_subject(event_type, payload_value)
        )
        subject = normalize_ref(subject)
        normalized_relations = [
            {
                "subject": normalize_ref(value["subject"]),
                "predicate": str(value["predicate"]),
                "object": normalize_ref(value["object"]),
            }
            for value in relations
        ]
        key = self._idempotency_key(event_type, idempotency_key)
        with self._canonical_write_lock():
            # Identity is rechecked inside the same critical section as the
            # lifecycle compare-and-swap and append.
            session = self._active_session()
            actor = self._actor()
            namespace = self._namespace(session)
            pending = {
                "event_type": event_type,
                "payload": payload_value,
                "actor": actor,
                "subject_refs": [subject],
                "relations": normalized_relations,
                "tags": list(tags),
            }
            self.policy.assert_safe(pending, context="canonical write request")
            allowed_projection_warnings: frozenset[str] = frozenset()
            if event_type == "event.corrected" and payload_value.get(
                "superseded_correction_event_ids"
            ):
                target_event_id = str(payload_value["target_event_id"])
                allowed_projection_warnings = frozenset(
                    {
                        "corrections have multiple active heads",
                        f"event {target_event_id} has conflicting corrections",
                    }
                )
            snapshot = self._guard_integrity(
                allow_missing_receipt=(namespace, key),
                actor=actor,
                allowed_projection_warnings=allowed_projection_warnings,
                recovery_invalidation_payload=(
                    payload_value if event_type == "event.invalidated" else None
                ),
            )
            reservation = self.events.idempotency.lookup(namespace=namespace, key=key)
            repair_candidate = (
                self._event_for_idempotency_identity(namespace, key)
                if reservation is None
                else None
            )
            if reservation is None and repair_candidate is None:
                validate_transition(
                    snapshot,
                    event_type,
                    payload_value,
                    actor_session_id=session.session_id,
                )
                self._enforce_workspace_policy(
                    snapshot,
                    event_type,
                    payload_value,
                    session,
                )
            semantic_candidate = {
                "schema": "commons.event.v1",
                "payload_schema": payload_schema,
                "workspace_id": self.workspace_id,
                "event_type": event_type,
                "actor": actor,
                "subject_refs": [subject],
                "idempotency_namespace": namespace,
                "idempotency_key": key,
                "provenance": {
                    "writer": "agent-commons",
                    "writer_version": __version__,
                    "source_kind": "manual",
                    "source_refs": [],
                },
                "payload": payload_value,
                "relations": normalized_relations,
                "tags": sorted(set(tags)),
            }
            semantic_hash = canonical_sha256(semantic_candidate)
            if reservation is not None and reservation.semantic_sha256 != semantic_hash:
                raise IdempotencyConflictError(
                    "idempotency identity already belongs to different event content"
                )
            if (
                repair_candidate is not None
                and canonical_sha256(semantic_event_body(repair_candidate.event)) != semantic_hash
            ):
                raise IdempotencyConflictError(
                    "idempotency identity already belongs to different event content"
                )
            if _manifest is not None:
                manifest = dict(_manifest)
                self.policy.assert_safe(manifest, context="artifact metadata")
                self.schemas.validate_manifest(manifest)
                manifest_record = self.manifests.put(manifest)
                if payload_value.get("manifest_ref") != manifest_record.manifest_id:
                    raise IntegrityError("artifact event does not bind its immutable manifest")
            record = self.events.append_event(
                workspace_id=self.workspace_id,
                event_type=event_type,
                payload_schema=payload_schema,
                payload=payload_value,
                actor=actor,
                subject_refs=(subject,),
                idempotency_namespace=namespace,
                idempotency_key=key,
                provenance={
                    "writer": "agent-commons",
                    "writer_version": __version__,
                    "source_kind": "manual",
                    "source_refs": [],
                },
                relations=normalized_relations,
                tags=tags,
            )
            self.receipt_recovery.reconcile(
                list(self.events.iter_events()),
                actor=actor,
            )
            index_result = self._sync_index()
        return {
            "event_id": record.event_id,
            "event_type": event_type,
            "entity_ref": subject,
            "revision": record.event_id,
            "idempotency_key": key,
            "created": record.created,
            "repaired": record.repaired,
            "index": index_result,
        }

    def orient(self, *, max_items: int = 20) -> dict[str, Any]:
        session = self._active_session()
        claims = [_public_claim(value) for value in self.claims.list_claims(active_only=True)]
        return orientation(
            self.snapshot(),
            session=session.actor_context(),
            claims=claims,
            max_items=max_items,
        )

    def inbox(self, *, max_items: int = 20) -> dict[str, Any]:
        brief = self.orient(max_items=max_items)
        return {"threads": brief["inbox"], "handoffs": brief["handoffs"]}

    def _list(self, kind: str, *, state: str | None = None) -> list[dict[str, Any]]:
        snapshot = self.snapshot()
        collection = getattr(snapshot, _COLLECTIONS[kind])
        values = sorted(collection.values(), key=lambda item: str(item.get("id", "")))
        return [dict(item) for item in values if state is None or item.get("state") == state]

    def list_threads(self, *, state: str | None = None) -> list[dict[str, Any]]:
        return self._list("thread", state=state)

    def list_artifacts(self) -> list[dict[str, Any]]:
        return self._list("artifact")

    def get_artifact_bundle(self, artifact_id: str) -> dict[str, Any]:
        """Return one projected artifact with its integrity-checked manifest metadata."""

        current = entity(self.snapshot(), "artifact", artifact_id)
        if current is None:
            raise LifecycleConflictError(f"artifact does not exist: {artifact_id}")
        manifest_ref = str(current.get("manifest_ref", ""))
        try:
            manifest = self.manifests.get(manifest_ref)
        except FileNotFoundError as exc:
            raise IntegrityError(f"artifact {artifact_id} references a missing manifest") from exc
        if manifest.manifest.get("artifact_id") != artifact_id:
            raise IntegrityError("artifact manifest identity does not match its projection")
        return {"artifact": dict(current), "manifest": dict(manifest.manifest)}

    def list_reviews(self, *, state: str | None = None) -> list[dict[str, Any]]:
        return self._list("review", state=state)

    def list_verifications(self) -> list[dict[str, Any]]:
        return self._list("verification")

    def list_findings(self, *, state: str | None = None) -> list[dict[str, Any]]:
        return self._list("finding", state=state)

    def list_decisions(self, *, state: str | None = None) -> list[dict[str, Any]]:
        return self._list("decision", state=state)

    def list_handoffs(self, *, state: str | None = None) -> list[dict[str, Any]]:
        return self._list("handoff", state=state)

    def list_delegations(self, *, state: str | None = None) -> list[dict[str, Any]]:
        return self._list("delegation", state=state)

    def get_delegation(self, delegation_id: str) -> dict[str, Any]:
        current = entity(self.snapshot(), "delegation", delegation_id)
        if current is None:
            raise LifecycleConflictError(f"delegation does not exist: {delegation_id}")
        return dict(current)

    def create_objective(
        self,
        *,
        title: str,
        description: str,
        acceptance_criteria: Sequence[str],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = self._idempotency_key("objective.created", idempotency_key)
        objective_id = self._new_entity_id("objective", "objective.created", key)
        return self.record_event(
            "objective.created",
            {
                "objective_id": objective_id,
                "title": title,
                "description": description,
                "acceptance_criteria": _nonempty_list(acceptance_criteria, "acceptance_criteria"),
            },
            idempotency_key=key,
            tags=("objective",),
        )

    def list_objectives(self) -> list[dict[str, Any]]:
        return self._list("objective")

    def revise_objective(
        self,
        objective_id: str,
        expected_revision: str,
        *,
        changes: Mapping[str, Any],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = self._idempotency_key("objective.revised", idempotency_key)
        return self.record_event(
            "objective.revised",
            {
                "objective_id": objective_id,
                "expected_revision": expected_revision,
                "changes": dict(changes),
            },
            idempotency_key=key,
            tags=("objective",),
        )

    def close_objective(
        self,
        objective_id: str,
        expected_revision: str,
        *,
        reason: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = self._idempotency_key("objective.closed", idempotency_key)
        return self.record_event(
            "objective.closed",
            {
                "objective_id": objective_id,
                "expected_revision": expected_revision,
                "reason": reason,
            },
            idempotency_key=key,
            tags=("objective",),
        )

    def create_task(
        self,
        *,
        title: str,
        description: str,
        acceptance_criteria: Sequence[str],
        priority: str = "normal",
        dependencies: Sequence[str] = (),
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = self._idempotency_key("task.created", idempotency_key)
        task_id = self._new_entity_id("task", "task.created", key)
        dependency_ids = _optional_list(dependencies, "dependencies")
        subject = {"kind": "task", "id": task_id}
        relations = [
            self._relation(subject, "depends_on", {"kind": "task", "id": dependency})
            for dependency in dependency_ids
        ]
        return self.record_event(
            "task.created",
            {
                "task_id": task_id,
                "title": title,
                "description": description,
                "acceptance_criteria": _nonempty_list(acceptance_criteria, "acceptance_criteria"),
                "priority": priority,
                "dependencies": dependency_ids,
            },
            idempotency_key=key,
            relations=relations,
            tags=("task",),
        )

    def list_tasks(self, *, state: str | None = None) -> list[dict[str, Any]]:
        return self._list("task", state=state)

    def _task_transition(
        self,
        task_id: str,
        expected_revision: str,
        action: str,
        *,
        idempotency_key: str | None = None,
        **fields: Any,
    ) -> dict[str, Any]:
        event_type = f"task.{action}"
        key = self._idempotency_key(event_type, idempotency_key)
        return self.record_event(
            event_type,
            {"task_id": task_id, "expected_revision": expected_revision, **fields},
            idempotency_key=key,
            tags=("task",),
        )

    def take_task(
        self, task_id: str, expected_revision: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        return self._task_transition(
            task_id,
            expected_revision,
            "taken",
            idempotency_key=idempotency_key,
            owner_session_id=self._active_session().session_id,
        )

    def start_task(self, task_id: str, expected_revision: str, **kwargs: Any) -> dict[str, Any]:
        return self._task_transition(task_id, expected_revision, "started", **kwargs)

    def block_task(
        self, task_id: str, expected_revision: str, *, reason: str, **kwargs: Any
    ) -> dict[str, Any]:
        return self._task_transition(task_id, expected_revision, "blocked", reason=reason, **kwargs)

    def unblock_task(
        self, task_id: str, expected_revision: str, *, resolution: str, **kwargs: Any
    ) -> dict[str, Any]:
        return self._task_transition(
            task_id, expected_revision, "unblocked", resolution=resolution, **kwargs
        )

    def complete_task(
        self,
        task_id: str,
        expected_revision: str,
        *,
        summary: str,
        artifact_refs: Sequence[Mapping[str, str]] = (),
        **kwargs: Any,
    ) -> dict[str, Any]:
        refs = self._assert_refs_exist(artifact_refs)
        return self._task_transition(
            task_id,
            expected_revision,
            "completed",
            summary=summary,
            artifact_refs=refs,
            **kwargs,
        )

    def submit_task(
        self,
        task_id: str,
        expected_revision: str,
        *,
        summary: str,
        artifact_refs: Sequence[Mapping[str, str]] = (),
        **kwargs: Any,
    ) -> dict[str, Any]:
        refs = self._assert_refs_exist(artifact_refs)
        return self._task_transition(
            task_id,
            expected_revision,
            "submitted",
            summary=summary,
            artifact_refs=refs,
            **kwargs,
        )

    def accept_task(
        self, task_id: str, expected_revision: str, *, summary: str, **kwargs: Any
    ) -> dict[str, Any]:
        idempotency_key = kwargs.pop("idempotency_key", None)
        if kwargs:
            raise ValidationError(
                "unsupported task acceptance fields: " + ", ".join(sorted(kwargs))
            )
        key = self._idempotency_key("task.accepted", idempotency_key)
        session = self._active_session()
        namespace = self._namespace(session)
        reservation = self.events.idempotency.lookup(namespace=namespace, key=key)
        existing: EventRecord | None = None
        if reservation is not None:
            try:
                existing = self.events.get(reservation.event_id)
            except FileNotFoundError:
                pass
        else:
            existing = self._event_for_idempotency_identity(namespace, key)
        if existing is not None and existing.event.get("event_type") == "task.accepted":
            stored_payload = existing.event.get("payload") or {}
            stored_binding = stored_payload.get("acceptance_review")
            if isinstance(stored_binding, Mapping):
                acceptance_review = dict(stored_binding)
                return self.record_event(
                    "task.accepted",
                    {
                        "task_id": task_id,
                        "expected_revision": expected_revision,
                        "summary": summary,
                        "acceptance_review": acceptance_review,
                    },
                    idempotency_key=key,
                    relations=(
                        self._relation(
                            {"kind": "task", "id": task_id},
                            "depends_on",
                            normalize_ref(acceptance_review["ref"]),
                        ),
                    ),
                    tags=("task", "truth"),
                )
        snapshot = self.snapshot()
        task = entity(snapshot, "task", task_id)
        if task is None:
            raise LifecycleConflictError(f"task does not exist: {task_id}")
        target_revision = str(task.get("effective_revision") or task.get("revision"))
        work_author_sessions = {
            str(session_id)
            for session_id in task.get("work_author_session_ids", [])
            if str(session_id)
        }
        qualifying = sorted(
            (
                review
                for review in snapshot.reviews.values()
                if review.get("state") == "approved"
                and review.get("independent") is True
                and review.get("stale") is False
                and review.get("target_ref") == {"kind": "task", "id": task_id}
                and review.get("target_revision") == target_revision
                and str((review.get("actor") or {}).get("session_id", ""))
                not in work_author_sessions
            ),
            key=lambda review: (
                str(review.get("recorded_at", "")),
                str(review.get("id", "")),
            ),
        )
        if not qualifying:
            raise LifecycleConflictError(
                "task acceptance requires a current approved independent review"
            )
        selected = qualifying[-1]
        selected_ref = {"kind": "review", "id": str(selected["id"])}
        acceptance_review = {
            "ref": selected_ref,
            "revision": str(selected.get("effective_revision") or selected.get("revision")),
        }
        return self.record_event(
            "task.accepted",
            {
                "task_id": task_id,
                "expected_revision": expected_revision,
                "summary": summary,
                "acceptance_review": acceptance_review,
            },
            idempotency_key=key,
            relations=(
                self._relation(
                    {"kind": "task", "id": task_id},
                    "depends_on",
                    selected_ref,
                ),
            ),
            tags=("task", "truth"),
        )

    def cancel_task(
        self, task_id: str, expected_revision: str, *, reason: str, **kwargs: Any
    ) -> dict[str, Any]:
        return self._task_transition(
            task_id, expected_revision, "cancelled", reason=reason, **kwargs
        )

    def reopen_task(
        self, task_id: str, expected_revision: str, *, reason: str, **kwargs: Any
    ) -> dict[str, Any]:
        return self._task_transition(
            task_id, expected_revision, "reopened", reason=reason, **kwargs
        )

    def create_delegation(
        self,
        *,
        target_ref: Mapping[str, str],
        target_revision: str,
        target_profile: str,
        purpose: str,
        limits: Mapping[str, Any],
        parent_delegation_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = self._idempotency_key("delegation.requested", idempotency_key)
        delegation_id = self._new_entity_id("delegation", "delegation.requested", key)
        session = self._active_session()
        snapshot = self.snapshot()
        target = self._assert_refs_exist((target_ref,), snapshot)[0]
        parent_id = str(parent_delegation_id or "")
        if parent_id:
            parent = entity(snapshot, "delegation", parent_id)
            if parent is None:
                raise LifecycleConflictError(f"delegation does not exist: {parent_id}")
            root_delegation_id = str(parent.get("root_delegation_id", ""))
            depth = int(parent.get("depth", -1)) + 1
        else:
            root_delegation_id = delegation_id
            depth = 0
        subject = {"kind": "delegation", "id": delegation_id}
        relations = [self._relation(subject, "targets", target)]
        if parent_id:
            relations.append(
                self._relation(
                    subject,
                    "spawned_by",
                    {"kind": "delegation", "id": parent_id},
                )
            )
        payload: dict[str, Any] = {
            "delegation_id": delegation_id,
            "target_ref": target,
            "target_revision": target_revision,
            "target_profile": target_profile,
            "purpose": purpose,
            "parent_session_id": session.session_id,
            "root_delegation_id": root_delegation_id,
            "depth": depth,
            "limits": dict(limits),
        }
        if parent_id:
            payload["parent_delegation_id"] = parent_id
        return self.record_event(
            "delegation.requested",
            payload,
            idempotency_key=key,
            relations=relations,
            tags=("delegation", purpose),
        )

    def _delegation_transition(
        self,
        delegation_id: str,
        expected_revision: str,
        action: str,
        *,
        idempotency_key: str | None = None,
        **fields: Any,
    ) -> dict[str, Any]:
        event_type = f"delegation.{action}"
        key = self._idempotency_key(event_type, idempotency_key)
        return self.record_event(
            event_type,
            {
                "delegation_id": delegation_id,
                "expected_revision": expected_revision,
                **fields,
            },
            idempotency_key=key,
            tags=("delegation",),
        )

    def start_delegation(
        self,
        delegation_id: str,
        expected_revision: str,
        *,
        child_session_id: str,
        attempt: int = 1,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        self.sessions.require_active(child_session_id)
        return self._delegation_transition(
            delegation_id,
            expected_revision,
            "started",
            child_session_id=child_session_id,
            attempt=attempt,
            idempotency_key=idempotency_key,
        )

    def mark_delegation_input_needed(
        self,
        delegation_id: str,
        expected_revision: str,
        *,
        summary: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self._delegation_transition(
            delegation_id,
            expected_revision,
            "input_needed",
            summary=summary,
            idempotency_key=idempotency_key,
        )

    def resume_delegation(
        self,
        delegation_id: str,
        expected_revision: str,
        *,
        resolution: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self._delegation_transition(
            delegation_id,
            expected_revision,
            "resumed",
            resolution=resolution,
            idempotency_key=idempotency_key,
        )

    def succeed_delegation(
        self,
        delegation_id: str,
        expected_revision: str,
        *,
        summary: str,
        result_refs: Sequence[Mapping[str, str]],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        refs = self._assert_refs_exist(result_refs)
        if not refs:
            raise ValidationError("result_refs must contain at least one reference")
        subject = {"kind": "delegation", "id": delegation_id}
        return self.record_event(
            "delegation.succeeded",
            {
                "delegation_id": delegation_id,
                "expected_revision": expected_revision,
                "summary": summary,
                "result_refs": refs,
            },
            idempotency_key=self._idempotency_key("delegation.succeeded", idempotency_key),
            relations=tuple(self._relation(subject, "produced", ref) for ref in refs),
            tags=("delegation",),
        )

    def fail_delegation(
        self,
        delegation_id: str,
        expected_revision: str,
        *,
        reason_code: str,
        summary: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self._delegation_transition(
            delegation_id,
            expected_revision,
            "failed",
            reason_code=reason_code,
            summary=summary,
            idempotency_key=idempotency_key,
        )

    def cancel_delegation(
        self,
        delegation_id: str,
        expected_revision: str,
        *,
        reason: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self._delegation_transition(
            delegation_id,
            expected_revision,
            "cancelled",
            reason=reason,
            idempotency_key=idempotency_key,
        )

    def time_out_delegation(
        self,
        delegation_id: str,
        expected_revision: str,
        *,
        summary: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self._delegation_transition(
            delegation_id,
            expected_revision,
            "timed_out",
            summary=summary,
            idempotency_key=idempotency_key,
        )

    def mark_delegation_needs_operator(
        self,
        delegation_id: str,
        expected_revision: str,
        *,
        reason_code: str,
        summary: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self._delegation_transition(
            delegation_id,
            expected_revision,
            "needs_operator",
            reason_code=reason_code,
            summary=summary,
            idempotency_key=idempotency_key,
        )

    def open_thread(
        self,
        *,
        thread_type: str,
        subject: str,
        desired_outcome: str,
        to: Sequence[str],
        related_refs: Sequence[Mapping[str, str]] = (),
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = self._idempotency_key("thread.opened", idempotency_key)
        thread_id = self._new_entity_id("thread", "thread.opened", key)
        refs = self._assert_refs_exist(related_refs)
        return self.record_event(
            "thread.opened",
            {
                "thread_id": thread_id,
                "thread_type": thread_type,
                "subject": subject,
                "desired_outcome": desired_outcome,
                "to": sorted(set(_nonempty_list(to, "to"))),
                "related_refs": refs,
            },
            idempotency_key=key,
            tags=("thread", thread_type),
        )

    def reply_thread(
        self,
        thread_id: str,
        expected_revision: str,
        *,
        body: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = self._idempotency_key("thread.replied", idempotency_key)
        message_id = self._new_entity_id("message", "thread.replied", key)
        return self.record_event(
            "thread.replied",
            {
                "thread_id": thread_id,
                "message_id": message_id,
                "body": body,
                "expected_revision": expected_revision,
            },
            idempotency_key=key,
            tags=("thread",),
        )

    def resolve_thread(
        self,
        thread_id: str,
        expected_revision: str,
        *,
        resolution: str,
        summary: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = self._idempotency_key("thread.resolved", idempotency_key)
        return self.record_event(
            "thread.resolved",
            {
                "thread_id": thread_id,
                "expected_revision": expected_revision,
                "resolution": resolution,
                "summary": summary,
            },
            idempotency_key=key,
            tags=("thread",),
        )

    def _hash_artifact(self, source: str | Path) -> tuple[str, int, str]:
        raw = Path(source).expanduser()
        candidate = raw if raw.is_absolute() else self.repo_root / raw
        if candidate.is_symlink():
            raise ValidationError("artifact source must not be a symlink")
        resolved = candidate.resolve()
        try:
            relative = resolved.relative_to(self.repo_root).as_posix()
        except ValueError as exc:
            raise ValidationError("artifact source must be inside the project") from exc
        if not resolved.is_file():
            raise ValidationError("artifact source must be a regular file")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(resolved, flags)
        digest = hashlib.sha256()
        try:
            before = os.fstat(descriptor)
            before_identity = (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            )
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            after = os.fstat(descriptor)
            after_identity = (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
            if before_identity != after_identity:
                raise IntegrityError("artifact source changed while it was being hashed")
            try:
                path_after = os.stat(resolved, follow_symlinks=False)
            except FileNotFoundError as exc:
                raise IntegrityError(
                    "artifact source path changed while it was being hashed"
                ) from exc
            path_identity = (
                path_after.st_dev,
                path_after.st_ino,
                path_after.st_size,
                path_after.st_mtime_ns,
                path_after.st_ctime_ns,
            )
            if path_identity != after_identity:
                raise IntegrityError("artifact source path changed while it was being hashed")
            return digest.hexdigest(), int(after.st_size), relative
        finally:
            os.close(descriptor)

    def _artifact_manifest(
        self,
        artifact_id: str,
        source: str | Path,
        *,
        media_type: str,
        classification: str,
        metadata: Mapping[str, Any] | None,
    ) -> tuple[dict[str, Any], str]:
        digest, size_bytes, relative = self._hash_artifact(source)
        revision = f"sha256:{digest}"
        manifest = {
            "schema": "commons.manifest.artifact.v1",
            "kind": "artifact",
            "artifact_id": artifact_id,
            "revision": revision,
            "source": {"path": relative},
            "media_type": media_type,
            "size_bytes": size_bytes,
            "classification": classification,
            "captured": False,
            "metadata": dict(metadata or {}),
        }
        self.policy.assert_safe(manifest, context="artifact metadata")
        self.schemas.validate_manifest(manifest)
        return manifest, revision

    def register_artifact(
        self,
        source: str | Path,
        *,
        media_type: str = "application/octet-stream",
        classification: str = "internal",
        metadata: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = self._idempotency_key("artifact.registered", idempotency_key)
        artifact_id = self._new_entity_id("artifact", "artifact.registered", key)
        manifest, revision = self._artifact_manifest(
            artifact_id,
            source,
            media_type=media_type,
            classification=classification,
            metadata=metadata,
        )
        manifest_id = f"mft.artifact.sha256.{canonical_sha256(manifest)}"
        subject = {"kind": "artifact", "id": artifact_id}
        result = self.record_event(
            "artifact.registered",
            {
                "artifact_id": artifact_id,
                "manifest_ref": manifest_id,
                "revision": revision,
                "classification": classification,
            },
            idempotency_key=key,
            relations=(
                self._relation(
                    subject,
                    "uses",
                    {"kind": "manifest", "id": manifest_id},
                ),
            ),
            tags=("artifact",),
            _manifest=manifest,
        )
        return {**result, "manifest_id": manifest_id, "content_copied": False}

    def revise_artifact(
        self,
        artifact_id: str,
        expected_revision: str,
        source: str | Path,
        *,
        media_type: str = "application/octet-stream",
        classification: str = "internal",
        metadata: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = self._idempotency_key("artifact.revised", idempotency_key)
        self._active_session()
        manifest, revision = self._artifact_manifest(
            artifact_id,
            source,
            media_type=media_type,
            classification=classification,
            metadata=metadata,
        )
        manifest_id = f"mft.artifact.sha256.{canonical_sha256(manifest)}"
        subject = {"kind": "artifact", "id": artifact_id}
        result = self.record_event(
            "artifact.revised",
            {
                "artifact_id": artifact_id,
                "expected_revision": expected_revision,
                "manifest_ref": manifest_id,
                "revision": revision,
                "classification": classification,
            },
            idempotency_key=key,
            relations=(
                self._relation(
                    subject,
                    "uses",
                    {"kind": "manifest", "id": manifest_id},
                ),
            ),
            tags=("artifact",),
            _manifest=manifest,
        )
        return {**result, "manifest_id": manifest_id, "content_copied": False}

    def request_review(
        self,
        *,
        target_ref: Mapping[str, str],
        target_revision: str,
        criteria: Sequence[str],
        independent: bool = True,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = self._idempotency_key("review.requested", idempotency_key)
        review_id = self._new_entity_id("review", "review.requested", key)
        snapshot = self.snapshot()
        target_binding = self._bind_evidence_refs((target_ref,), snapshot)[0]
        target = target_binding["ref"]
        if target_binding["revision"] != target_revision:
            raise LifecycleConflictError(
                "target_revision is not the current effective target revision"
            )
        subject = {"kind": "review", "id": review_id}
        return self.record_event(
            "review.requested",
            {
                "review_id": review_id,
                "target_ref": target,
                "target_revision": target_revision,
                "criteria": _nonempty_list(criteria, "criteria"),
                "independent": bool(independent),
            },
            idempotency_key=key,
            relations=(self._relation(subject, "reviews", target),),
            tags=("review",),
        )

    def complete_review(
        self,
        review_id: str,
        expected_revision: str,
        *,
        target_revision: str,
        verdict: str,
        summary: str,
        evidence_refs: Sequence[Mapping[str, str]] = (),
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        refs = self._bind_evidence_refs(evidence_refs)
        key = self._idempotency_key("review.completed", idempotency_key)
        return self.record_event(
            "review.completed",
            {
                "review_id": review_id,
                "expected_revision": expected_revision,
                "target_revision": target_revision,
                "verdict": verdict,
                "summary": summary,
                "evidence_refs": refs,
            },
            idempotency_key=key,
            tags=("review",),
        )

    def record_verification(
        self,
        *,
        target_ref: Mapping[str, str],
        target_revision: str,
        claim: str,
        evidence_refs: Sequence[Mapping[str, str]],
        method: str,
        outcome: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = self._idempotency_key("verification.recorded", idempotency_key)
        verification_id = self._new_entity_id("verification", "verification.recorded", key)
        snapshot = self.snapshot()
        target_binding = self._bind_evidence_refs((target_ref,), snapshot)[0]
        target = target_binding["ref"]
        if target_binding["revision"] != target_revision:
            raise LifecycleConflictError(
                "target_revision is not the current effective target revision"
            )
        evidence = self._bind_evidence_refs(evidence_refs, snapshot)
        if not evidence:
            raise ValidationError("verification requires at least one evidence reference")
        subject = {"kind": "verification", "id": verification_id}
        relations = [self._relation(subject, "verifies", target)] + [
            self._relation(subject, "depends_on", value["ref"]) for value in evidence
        ]
        return self.record_event(
            "verification.recorded",
            {
                "verification_id": verification_id,
                "target_ref": target,
                "target_revision": target_revision,
                "claim": claim,
                "evidence_refs": evidence,
                "method": method,
                "outcome": outcome,
            },
            idempotency_key=key,
            relations=relations,
            tags=("verification",),
        )

    def report_finding(
        self,
        *,
        summary: str,
        severity: str,
        evidence_refs: Sequence[Mapping[str, str]] = (),
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = self._idempotency_key("finding.reported", idempotency_key)
        finding_id = self._new_entity_id("finding", "finding.reported", key)
        evidence = self._bind_evidence_refs(evidence_refs)
        subject = {"kind": "finding", "id": finding_id}
        relations = [self._relation(subject, "derived_from", value["ref"]) for value in evidence]
        return self.record_event(
            "finding.reported",
            {
                "finding_id": finding_id,
                "summary": summary,
                "severity": severity,
                "evidence_refs": evidence,
            },
            idempotency_key=key,
            relations=relations,
            tags=("finding", severity),
        )

    def promote_finding(
        self,
        finding_id: str,
        expected_revision: str,
        *,
        summary: str,
        evidence_refs: Sequence[Mapping[str, str]],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        evidence = self._bind_evidence_refs(evidence_refs)
        if not evidence:
            raise ValidationError("promoting a finding requires evidence")
        key = self._idempotency_key("finding.promoted", idempotency_key)
        return self.record_event(
            "finding.promoted",
            {
                "finding_id": finding_id,
                "expected_revision": expected_revision,
                "evidence_refs": evidence,
                "summary": summary,
            },
            idempotency_key=key,
            tags=("finding", "truth"),
        )

    def contest_finding(
        self, finding_id: str, expected_revision: str, *, reason: str, **kwargs: Any
    ) -> dict[str, Any]:
        key = self._idempotency_key("finding.contested", kwargs.pop("idempotency_key", None))
        return self.record_event(
            "finding.contested",
            {"finding_id": finding_id, "expected_revision": expected_revision, "reason": reason},
            idempotency_key=key,
            tags=("finding",),
        )

    def resolve_finding(
        self, finding_id: str, expected_revision: str, *, resolution: str, **kwargs: Any
    ) -> dict[str, Any]:
        key = self._idempotency_key("finding.resolved", kwargs.pop("idempotency_key", None))
        return self.record_event(
            "finding.resolved",
            {
                "finding_id": finding_id,
                "expected_revision": expected_revision,
                "resolution": resolution,
            },
            idempotency_key=key,
            tags=("finding", "truth"),
        )

    def propose_decision(
        self,
        *,
        scope: str,
        proposal: str,
        alternatives: Sequence[str] = (),
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = self._idempotency_key("decision.proposed", idempotency_key)
        decision_id = self._new_entity_id("decision", "decision.proposed", key)
        return self.record_event(
            "decision.proposed",
            {
                "decision_id": decision_id,
                "scope": scope,
                "proposal": proposal,
                "alternatives": _optional_list(alternatives, "alternatives"),
            },
            idempotency_key=key,
            tags=("decision",),
        )

    def accept_decision(
        self,
        decision_id: str,
        expected_revision: str,
        *,
        rationale: str,
        evidence_refs: Sequence[Mapping[str, str]] = (),
        dissent: Sequence[str] = (),
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        evidence = self._bind_evidence_refs(evidence_refs)
        key = self._idempotency_key("decision.accepted", idempotency_key)
        return self.record_event(
            "decision.accepted",
            {
                "decision_id": decision_id,
                "expected_revision": expected_revision,
                "rationale": rationale,
                "evidence_refs": evidence,
                "dissent": _optional_list(dissent, "dissent"),
            },
            idempotency_key=key,
            tags=("decision", "truth"),
        )

    def reject_decision(
        self, decision_id: str, expected_revision: str, *, rationale: str, **kwargs: Any
    ) -> dict[str, Any]:
        key = self._idempotency_key("decision.rejected", kwargs.pop("idempotency_key", None))
        return self.record_event(
            "decision.rejected",
            {
                "decision_id": decision_id,
                "expected_revision": expected_revision,
                "rationale": rationale,
            },
            idempotency_key=key,
            tags=("decision", "truth"),
        )

    def defer_decision(
        self, decision_id: str, expected_revision: str, *, reason: str, **kwargs: Any
    ) -> dict[str, Any]:
        key = self._idempotency_key("decision.deferred", kwargs.pop("idempotency_key", None))
        return self.record_event(
            "decision.deferred",
            {"decision_id": decision_id, "expected_revision": expected_revision, "reason": reason},
            idempotency_key=key,
            tags=("decision",),
        )

    def supersede_decision(
        self,
        decision_id: str,
        expected_revision: str,
        *,
        replacement_decision_id: str,
        reason: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        snapshot = self.snapshot()
        if entity(snapshot, "decision", replacement_decision_id) is None:
            raise ValidationError("replacement decision does not exist")
        key = self._idempotency_key("decision.superseded", idempotency_key)
        replacement = {"kind": "decision", "id": replacement_decision_id}
        return self.record_event(
            "decision.superseded",
            {
                "decision_id": decision_id,
                "expected_revision": expected_revision,
                "replacement_decision_id": replacement_decision_id,
                "reason": reason,
            },
            idempotency_key=key,
            relations=(
                self._relation(
                    replacement,
                    "supersedes",
                    {"kind": "decision", "id": decision_id},
                ),
            ),
            tags=("decision", "truth"),
        )

    def create_handoff(
        self,
        *,
        to: Sequence[str],
        completed: Sequence[str] = (),
        active: Sequence[str] = (),
        next_actions: Sequence[str],
        blockers: Sequence[str] = (),
        risks: Sequence[str] = (),
        open_questions: Sequence[str] = (),
        related_refs: Sequence[Mapping[str, str]] = (),
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = self._idempotency_key("handoff.created", idempotency_key)
        handoff_id = self._new_entity_id("handoff", "handoff.created", key)
        refs = self._assert_refs_exist(related_refs)
        subject = {"kind": "handoff", "id": handoff_id}
        return self.record_event(
            "handoff.created",
            {
                "handoff_id": handoff_id,
                "to": sorted(set(_nonempty_list(to, "to"))),
                "completed": _optional_list(completed, "completed"),
                "active": _optional_list(active, "active"),
                "next_actions": _nonempty_list(next_actions, "next_actions"),
                "blockers": _optional_list(blockers, "blockers"),
                "risks": _optional_list(risks, "risks"),
                "open_questions": _optional_list(open_questions, "open_questions"),
                "related_refs": refs,
            },
            idempotency_key=key,
            relations=[self._relation(subject, "depends_on", value) for value in refs],
            tags=("handoff",),
        )

    def acknowledge_handoff(
        self,
        handoff_id: str,
        expected_revision: str,
        *,
        note: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = self._idempotency_key("handoff.acknowledged", idempotency_key)
        return self.record_event(
            "handoff.acknowledged",
            {"handoff_id": handoff_id, "expected_revision": expected_revision, "note": note},
            idempotency_key=key,
            tags=("handoff",),
        )

    def correct_event(
        self,
        target_event_id: str,
        *,
        expected_target_sha256: str,
        replacement_payload: Mapping[str, Any],
        superseded_correction_event_ids: Sequence[str] = (),
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = self._idempotency_key("event.corrected", idempotency_key)
        payload: dict[str, Any] = {
            "target_event_id": target_event_id,
            "expected_target_sha256": expected_target_sha256,
            "replacement_payload": dict(replacement_payload),
        }
        superseded = _optional_list(
            superseded_correction_event_ids,
            "superseded_correction_event_ids",
        )
        if superseded:
            payload["superseded_correction_event_ids"] = superseded
        return self.record_event(
            "event.corrected",
            payload,
            idempotency_key=key,
            tags=("maintenance", "correction"),
        )

    def show_event(self, event_id: str) -> dict[str, Any]:
        record = self.events.get(event_id)
        return {
            "event_id": record.event_id,
            "canonical_sha256": record.sha256,
            "event": dict(record.event),
        }

    def invalidate_event(
        self,
        target_event_id: str,
        *,
        reason: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = self._idempotency_key("event.invalidated", idempotency_key)
        target = {"kind": "event", "id": target_event_id}
        return self.record_event(
            "event.invalidated",
            {"target_ref": target, "reason": reason},
            idempotency_key=key,
            tags=("maintenance", "invalidation"),
        )

    def revoke_invalidation(
        self,
        invalidation_event_id: str,
        *,
        reason: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = self._idempotency_key("event.invalidation_revoked", idempotency_key)
        return self.record_event(
            "event.invalidation_revoked",
            {
                "invalidation_event_id": invalidation_event_id,
                "reason": reason,
            },
            idempotency_key=key,
            tags=("maintenance", "invalidation-revocation"),
        )

    def abandon_idempotency_receipt(
        self,
        key_digest: str,
        *,
        reason: str,
    ) -> dict[str, Any]:
        """Audit and tombstone an orphan receipt that cannot be retried."""

        normalized_reason = reason.strip()
        if (
            not normalized_reason
            or len(normalized_reason) > 2048
            or any(ord(character) < 32 for character in normalized_reason)
        ):
            raise ValidationError("receipt abandonment reason is invalid")
        with self._canonical_write_lock():
            try:
                self.sessions.require_active(
                    self.session_id,
                    capability="receipt:abandon",
                )
            except LifecycleConflictError as exc:
                raise LifecycleConflictError(
                    "receipt abandonment requires an active receipt:abandon capability"
                ) from exc
            actor = self._actor()
            self.policy.assert_safe(
                {
                    "key_digest": key_digest,
                    "reason": normalized_reason,
                    "actor": actor,
                },
                context="idempotency receipt abandonment",
            )
            reservation = self.events.idempotency.get_by_digest(key_digest)
            if reservation is None and self.events.idempotency.get_migration() is None:
                reservation = self.events.idempotency.get_legacy_by_digest(key_digest)
            existing = self.events.idempotency.get_abandonment(key_digest)
            if existing is None and self.events.idempotency.get_migration() is None:
                existing = self.events.idempotency.get_legacy_abandonment(key_digest)
            if reservation is None:
                if existing is not None:
                    return dict(existing)
                raise ValidationError("idempotency receipt does not exist")

            for record in self.events.iter_events():
                event = record.event
                event_digest = self.events.idempotency.key_digest(
                    str(event["idempotency_namespace"]),
                    str(event["idempotency_key"]),
                )
                if record.event_id == reservation.event_id or event_digest == key_digest:
                    raise LifecycleConflictError(
                        "a receipt with a canonical event cannot be abandoned"
                    )
            abandonment = self.events.idempotency.abandon(
                reservation,
                reason=normalized_reason,
                actor_session_id=str(actor["session_id"]),
                actor_principal_id=str(actor["principal_id"]),
            )
        return dict(abandonment)

    def receipt_status(self) -> dict[str, Any]:
        records, _ = self._records_and_snapshot()
        return self.receipt_recovery.status(records)

    def reconcile_idempotency_receipts(
        self,
        *,
        adopt_legacy_orphans: Sequence[str] = (),
        prepare_rollback: bool = False,
    ) -> dict[str, Any]:
        with self._canonical_write_lock():
            actor = self._actor()
            records, _ = self._records_and_snapshot()
            if prepare_rollback:
                return self.receipt_recovery.prepare_rollback(records, actor=actor)
            return self.receipt_recovery.reconcile(
                records,
                actor=actor,
                adopt_legacy_orphans=adopt_legacy_orphans,
            )

    def acquire_claim(
        self,
        resources: Sequence[str],
        *,
        mode: str = "exclusive",
        ttl_seconds: int = 2 * 3600,
        description: str = "",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        claim = self.claims.acquire(
            resources,
            owner_session_id=self._active_session().session_id,
            mode=mode,
            ttl_seconds=ttl_seconds,
            description=description,
            idempotency_key=idempotency_key,
        )
        return _public_claim(claim, include_nonce=True)

    def list_claims(self, *, active_only: bool = True) -> list[dict[str, Any]]:
        return [_public_claim(value) for value in self.claims.list_claims(active_only=active_only)]

    def renew_claim(self, claim_id: str, *, nonce: str, ttl_seconds: int) -> dict[str, Any]:
        claim = self.claims.renew(
            claim_id,
            owner_session_id=self._active_session().session_id,
            nonce=nonce,
            ttl_seconds=ttl_seconds,
        )
        return _public_claim(claim, include_nonce=True)

    def release_claim(self, claim_id: str, *, nonce: str) -> dict[str, Any]:
        claim = self.claims.release(
            claim_id,
            owner_session_id=self._active_session().session_id,
            nonce=nonce,
        )
        return _public_claim(claim)

    def break_claim(self, claim_id: str, *, reason: str) -> dict[str, Any]:
        claim = self.claims.break_claim(
            claim_id,
            actor_session_id=self._active_session().session_id,
            reason=reason,
        )
        return _public_claim(claim)

    def build_views(self) -> dict[str, Any]:
        self._active_session()
        paths = render_views(self.snapshot(), self.paths.cache / "views")
        return {
            "paths": [path.relative_to(self.repo_root).as_posix() for path in paths],
            "count": len(paths),
        }

    def rebuild_index(self) -> dict[str, int]:
        with SQLiteIndex(self.paths, self.events, self.manifests) as index:
            return asdict(index.rebuild())

    def doctor(self) -> dict[str, Any]:
        issues: list[str] = []
        warnings: list[str] = []
        event_count = 0
        manifest_count = 0
        try:
            records, snapshot = self._records_and_snapshot()
            event_count = len(records)
            warnings.extend(snapshot.warnings)
            receipt_report = self.receipt_recovery.status(records)
            issues.extend(receipt_report["issues"])
            warnings.extend(receipt_report["warnings"])
            manifest_issues, manifest_warnings = self._manifest_reference_issues(
                records,
                snapshot,
            )
            issues.extend(manifest_issues)
            warnings.extend(manifest_warnings)
            issues.extend(self._hard_projection_warnings(snapshot.warnings))
        except Exception as exc:
            issues.append(f"events: {exc}")
        try:
            manifests = list(self.manifests.iter_manifests())
            manifest_count = len(manifests)
        except Exception as exc:
            issues.append(f"manifests: {exc}")
        try:
            self.sessions.list_sessions(active_only=False)
        except Exception as exc:
            issues.append(f"sessions: {exc}")
        try:
            self.claims.list_claims(active_only=False)
        except Exception as exc:
            issues.append(f"claims: {exc}")
        index_result: dict[str, int] | None = None
        if not issues:
            try:
                with SQLiteIndex(self.paths, self.events, self.manifests) as index:
                    index_result = asdict(index.sync(verify_unchanged=True))
            except Exception as exc:
                issues.append(f"index: {exc}")
        return {
            "ok": not issues,
            "workspace_id": self.workspace_id,
            "schema_count": len(self.schemas.schema_names),
            "event_count": event_count,
            "manifest_count": manifest_count,
            "issues": issues,
            "warnings": sorted(set(warnings)),
            "index": index_result,
        }
