"""Ledger-derived receipt recovery and checkout-local completeness anchors."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from agent_commons.config import CommonsPaths
from agent_commons.core.canonical import (
    canonical_json_file_bytes,
    canonical_sha256,
    loads_json_strict,
    sha256_bytes,
)
from agent_commons.core.schema_registry import SchemaRegistry
from agent_commons.errors import IntegrityError, ValidationError
from agent_commons.storage.atomic import atomic_write_replace
from agent_commons.storage.events import EventRecord, semantic_event_body
from agent_commons.storage.idempotency import IdempotencyReservation, IdempotencyStore


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class ReceiptRecovery:
    """Reconcile rebuildable receipts without weakening in-flight reservations."""

    def __init__(
        self,
        paths: CommonsPaths,
        schemas: SchemaRegistry,
        store: IdempotencyStore,
        *,
        workspace_id: str,
    ) -> None:
        self.paths = paths
        self.schemas = schemas
        self.store = store
        self.workspace_id = workspace_id
        self.store.bind_workspace(workspace_id)

    def _event_info(self, record: EventRecord) -> dict[str, str]:
        event = record.event
        namespace = str(event["idempotency_namespace"])
        key = str(event["idempotency_key"])
        return {
            "path": self.paths.canonical_relative(record.path),
            "event_sha256": sha256_bytes(record.path.read_bytes()),
            "key_digest": self.store.key_digest(namespace, key),
            "namespace": namespace,
            "key": key,
            "semantic_sha256": canonical_sha256(semantic_event_body(event)),
            "event_id": record.event_id,
            "recorded_at": str(event["recorded_at"]),
        }

    def _event_map(self, records: Sequence[EventRecord]) -> dict[str, dict[str, str]]:
        result: dict[str, dict[str, str]] = {}
        identities: set[tuple[str, str]] = set()
        for record in records:
            info = self._event_info(record)
            digest = info["key_digest"]
            identity = (info["namespace"], info["key"])
            if digest in result or identity in identities:
                raise IntegrityError("multiple canonical events share an idempotency identity")
            result[digest] = info
            identities.add(identity)
        return result

    @staticmethod
    def _receipt_matches(receipt: IdempotencyReservation, info: Mapping[str, str]) -> bool:
        return all(
            getattr(receipt, field) == info[field]
            for field in ("key_digest", "namespace", "semantic_sha256", "event_id", "recorded_at")
        )

    @staticmethod
    def _abandonment_matches(abandonment: Mapping[str, object], info: Mapping[str, str]) -> bool:
        return all(
            abandonment.get(field) == info[field]
            for field in ("key_digest", "namespace", "semantic_sha256", "event_id")
        )

    def _load_anchor(self) -> dict[str, Any] | None:
        path = self.store.anchor_path
        self.store.assert_operational_path(path)
        if path.is_symlink():
            raise IntegrityError(f"ledger anchor path must not be a symlink: {path}")
        if not path.exists():
            return None
        raw = path.read_bytes()
        value = loads_json_strict(raw)
        if not isinstance(value, dict) or raw != canonical_json_file_bytes(value):
            raise IntegrityError("ledger anchor is not canonical JSON")
        try:
            self.schemas.validate("commons.ledger_anchor.v1", value)
        except ValidationError as exc:
            raise IntegrityError("ledger anchor is invalid") from exc
        if (
            value["workspace_id"] != self.workspace_id
            or value["scope_id"] != self.store.scope["scope_id"]
        ):
            raise IntegrityError("ledger anchor conflicts with the current recovery scope")
        return value

    def _current_anchor_entries(self, event_map: Mapping[str, Mapping[str, str]]) -> dict[str, str]:
        return {
            info["path"]: info["event_sha256"]
            for info in sorted(event_map.values(), key=lambda item: item["path"])
        }

    def _verify_anchor(
        self,
        anchor: Mapping[str, Any] | None,
        current: Mapping[str, str],
        *,
        scope_document_exists: bool,
    ) -> tuple[list[str], list[str]]:
        if anchor is None:
            if scope_document_exists:
                return ["ledger anchor is missing for an observed receipt scope"], []
            return [], sorted(current)
        anchored = dict(anchor["events"])
        issues: list[str] = []
        for path, digest in anchored.items():
            if path not in current:
                issues.append(f"anchored canonical event is missing: {path}")
            elif current[path] != digest:
                issues.append(f"anchored canonical event changed bytes: {path}")
        additions = sorted(path for path in current if path not in anchored)
        return issues, additions

    def status(self, records: Sequence[EventRecord]) -> dict[str, Any]:
        issues: list[str] = []
        warnings: list[str] = []
        event_map = self._event_map(records)
        current_anchor = self._current_anchor_entries(event_map)
        migration = self.store.get_migration()
        scope_document = self.store.get_scope_document()
        anchor = self._load_anchor()
        anchor_issues, additions = self._verify_anchor(
            anchor,
            current_anchor,
            scope_document_exists=scope_document is not None,
        )
        issues.extend(anchor_issues)

        missing: list[str] = []
        orphan: list[str] = []
        conflicts: list[str] = []
        tombstone_matches: list[str] = []
        reconciled: list[str] = []
        for digest, info in event_map.items():
            receipt = self.store.get_by_digest(digest)
            if receipt is None:
                missing.append(digest)
            elif not self._receipt_matches(receipt, info):
                conflicts.append(digest)
            abandonment = self.store.get_abandonment(digest)
            reconciliation = self.store.get_reconciliation(digest)
            if abandonment is not None:
                if not self._abandonment_matches(abandonment, info):
                    conflicts.append(digest)
                elif reconciliation is None:
                    tombstone_matches.append(digest)
                elif (
                    reconciliation["event_id"] != info["event_id"]
                    or reconciliation["event_sha256"] != info["event_sha256"]
                    or reconciliation["semantic_sha256"] != info["semantic_sha256"]
                ):
                    conflicts.append(digest)
                else:
                    reconciled.append(digest)
        for receipt in self.store.iter_reservations():
            info = event_map.get(receipt.key_digest)
            if info is None:
                orphan.append(receipt.key_digest)

        legacy_receipts = list(self.store.iter_legacy_reservations())
        legacy_abandonments = list(self.store.iter_legacy_abandonments())
        legacy_orphans: list[str] = []
        legacy_conflicts: list[str] = []
        if migration is None:
            for receipt in legacy_receipts:
                info = event_map.get(receipt.key_digest)
                if info is None:
                    legacy_orphans.append(receipt.key_digest)
                elif not self._receipt_matches(receipt, info):
                    legacy_conflicts.append(receipt.key_digest)
            if records or legacy_receipts or legacy_abandonments:
                issues.append(
                    "idempotency v2 migration is required; run `agent-commons receipt reconcile`"
                )

        if additions and anchor is not None:
            issues.append(
                f"ledger anchor has {len(additions)} unobserved canonical event(s); "
                "run `agent-commons receipt reconcile`"
            )
        if anchor is None and migration is not None:
            if scope_document is None:
                issues.append(
                    "receipt scope is new and requires bootstrap; run "
                    "`agent-commons receipt reconcile`"
                )
        if missing:
            issues.append(
                f"{len(missing)} canonical event idempotency receipt(s) require reconstruction; "
                "run `agent-commons receipt reconcile`"
            )
        if tombstone_matches:
            issues.append(
                f"{len(tombstone_matches)} exact tombstone/event pair(s) require reconciliation; "
                "run `agent-commons receipt reconcile`"
            )
        if orphan:
            issues.append(
                f"orphan idempotency receipt {orphan[0]} has no canonical event in this scope"
            )
        if conflicts:
            issues.append(f"canonical event has a conflicting idempotency receipt: {conflicts[0]}")
        if legacy_orphans:
            issues.append(
                f"legacy orphan idempotency receipt {legacy_orphans[0]} requires explicit "
                "adoption or abandonment"
            )
        if legacy_conflicts:
            issues.append(
                f"legacy idempotency receipt conflicts with canonical event: {legacy_conflicts[0]}"
            )
        if reconciled:
            warnings.append(f"{len(reconciled)} abandonment tombstone(s) are reconciled in scope")
        return {
            "ok": not issues,
            "workspace_id": self.workspace_id,
            "migration_state": "v2" if migration is not None else "legacy",
            "scope": self.store.scope,
            "scope_initialized": scope_document is not None,
            "anchor_state": (
                "conflict"
                if anchor_issues
                else "absent"
                if anchor is None
                else "needs_reconcile"
                if additions
                else "healthy"
            ),
            "anchor_generation": None if anchor is None else anchor["generation"],
            "event_count": len(event_map),
            "receipt_count": sum(1 for _ in self.store.iter_reservations()),
            "missing_receipts": sorted(set(missing)),
            "orphan_receipts": sorted(set(orphan)),
            "conflicting_receipts": sorted(set(conflicts)),
            "tombstone_matches": sorted(set(tombstone_matches)),
            "reconciled_tombstones": sorted(set(reconciled)),
            "legacy_receipt_count": len(legacy_receipts),
            "legacy_abandonment_count": len(legacy_abandonments),
            "legacy_orphan_receipts": sorted(set(legacy_orphans)),
            "legacy_conflicting_receipts": sorted(set(legacy_conflicts)),
            "unobserved_events": additions,
            "issues": issues,
            "warnings": warnings,
        }

    def _write_anchor(
        self,
        current: Mapping[str, str],
        anchor: Mapping[str, Any] | None,
    ) -> Mapping[str, Any]:
        if anchor is not None and dict(anchor["events"]) == dict(current):
            return anchor
        document: dict[str, Any] = {
            "schema": "commons.ledger_anchor.v1",
            "workspace_id": self.workspace_id,
            "scope_id": self.store.scope["scope_id"],
            "generation": 0 if anchor is None else int(anchor["generation"]) + 1,
            "events": dict(sorted(current.items())),
            "observed_at": _utc_now(),
        }
        self.schemas.validate("commons.ledger_anchor.v1", document)
        self.store.ensure_directory(self.store.anchor_path.parent)
        atomic_write_replace(
            self.store.anchor_path,
            canonical_json_file_bytes(document),
            mode=0o600,
        )
        return document

    def _derive_receipts_and_tombstones(
        self,
        event_map: Mapping[str, Mapping[str, str]],
        *,
        actor: Mapping[str, Any],
    ) -> tuple[int, int]:
        derived = 0
        tombstones = 0
        for digest, info in event_map.items():
            abandonment = self.store.get_abandonment(digest)
            if abandonment is not None:
                if not self._abandonment_matches(abandonment, info):
                    raise IntegrityError("idempotency abandonment conflicts with a canonical event")
                if self.store.get_reconciliation(digest) is None:
                    self.store.reconcile_abandonment(
                        abandonment,
                        namespace=info["namespace"],
                        key=info["key"],
                        semantic_sha256=info["semantic_sha256"],
                        event_id=info["event_id"],
                        event_sha256=info["event_sha256"],
                        actor_session_id=str(actor["session_id"]),
                        actor_principal_id=str(actor["principal_id"]),
                        reason="exact canonical event observed in current checkout",
                    )
                    tombstones += 1
            reservation = self.store.get_by_digest(digest)
            if reservation is None:
                reservation = self.store.reserve(
                    namespace=info["namespace"],
                    key=info["key"],
                    semantic_sha256=info["semantic_sha256"],
                    event_id_factory=lambda event_id=info["event_id"]: event_id,
                    recorded_at=info["recorded_at"],
                )
                derived += int(reservation.created)
            if not self._receipt_matches(reservation, info):
                raise IntegrityError("derived receipt conflicts with its canonical event")
        return derived, tombstones

    def _preflight_event_identities(self, event_map: Mapping[str, Mapping[str, str]]) -> None:
        for digest, info in event_map.items():
            receipt = self.store.get_by_digest(digest)
            if receipt is not None and not self._receipt_matches(receipt, info):
                raise IntegrityError("idempotency receipt conflicts with a canonical event")
            abandonment = self.store.get_abandonment(digest)
            if abandonment is not None and not self._abandonment_matches(abandonment, info):
                raise IntegrityError("idempotency abandonment conflicts with a canonical event")
            reconciliation = self.store.get_reconciliation(digest)
            if reconciliation is not None and (
                reconciliation["event_id"] != info["event_id"]
                or reconciliation["event_sha256"] != info["event_sha256"]
                or reconciliation["semantic_sha256"] != info["semantic_sha256"]
            ):
                raise IntegrityError("idempotency reconciliation conflicts with a canonical event")

    def reconcile(
        self,
        records: Sequence[EventRecord],
        *,
        actor: Mapping[str, Any],
        adopt_legacy_orphans: Sequence[str] = (),
    ) -> dict[str, Any]:
        event_map = self._event_map(records)
        self._preflight_event_identities(event_map)
        current_anchor = self._current_anchor_entries(event_map)
        migration = self.store.get_migration()
        scope_document = self.store.get_scope_document()
        anchor = self._load_anchor()
        anchor_issues, _ = self._verify_anchor(
            anchor,
            current_anchor,
            scope_document_exists=scope_document is not None,
        )
        if anchor_issues:
            raise IntegrityError(anchor_issues[0])

        legacy_receipts = list(self.store.iter_legacy_reservations())
        legacy_abandonments = list(self.store.iter_legacy_abandonments())
        adopted = {self.store._validate_digest(value) for value in adopt_legacy_orphans}
        imported = 0
        if migration is None:
            legacy_orphans: dict[str, IdempotencyReservation] = {}
            for receipt in legacy_receipts:
                info = event_map.get(receipt.key_digest)
                if info is None:
                    legacy_orphans[receipt.key_digest] = receipt
                elif not self._receipt_matches(receipt, info):
                    raise IntegrityError(
                        "legacy idempotency receipt conflicts with a canonical event"
                    )
            unknown = adopted - set(legacy_orphans)
            if unknown:
                raise ValidationError(
                    f"cannot adopt non-orphan legacy receipt: {sorted(unknown)[0]}"
                )
            unresolved = set(legacy_orphans) - adopted
            if unresolved:
                digest = sorted(unresolved)[0]
                raise IntegrityError(
                    f"legacy orphan idempotency receipt {digest} requires "
                    f"`receipt reconcile --adopt-legacy-orphan {digest}` or abandonment"
                )
            for abandonment in legacy_abandonments:
                self.store.copy_legacy_abandonment(abandonment)

            # The anchor is published before scoped recovery state. A crash can
            # therefore resume from the anchor without mistaking bootstrap for deletion.
            anchor = self._write_anchor(current_anchor, anchor)
            for receipt in legacy_receipts:
                if receipt.key_digest in event_map or receipt.key_digest in adopted:
                    result = self.store.import_reservation(receipt)
                    imported += int(result.created)
        else:
            anchor = self._write_anchor(current_anchor, anchor)

        derived, reconciled = self._derive_receipts_and_tombstones(
            event_map,
            actor=actor,
        )
        self.store.ensure_scope()
        if migration is None:
            migration = self.store.mark_migrated(
                actor_session_id=str(actor["session_id"]),
                legacy_receipt_count=len(legacy_receipts),
                legacy_abandonment_count=len(legacy_abandonments),
            )
        result = self.status(records)
        result.update(
            {
                "imported_receipts": imported,
                "derived_receipts": derived,
                "reconciled_tombstones_count": reconciled,
                "adopted_legacy_orphans": sorted(adopted),
                "migration": migration,
            }
        )
        return result

    def prepare_for_write(
        self,
        records: Sequence[EventRecord],
        *,
        actor: Mapping[str, Any],
        requested_identity: tuple[str, str] | None,
    ) -> None:
        migration = self.store.get_migration()
        legacy_state = any(self.store.iter_legacy_reservations()) or any(
            self.store.iter_legacy_abandonments()
        )
        if migration is None:
            if records or legacy_state:
                raise IntegrityError(
                    "idempotency v2 migration is required; run `agent-commons receipt reconcile`"
                )
            self.reconcile(records, actor=actor)

        status = self.status(records)
        hard_prefixes = (
            "ledger anchor is missing",
            "ledger anchor conflicts",
            "anchored canonical event",
            "canonical event has a conflicting",
            "legacy idempotency receipt conflicts",
        )
        hard = [issue for issue in status["issues"] if issue.startswith(hard_prefixes)]
        if hard:
            raise IntegrityError(hard[0])

        recoverable = bool(
            status["missing_receipts"]
            or status["tombstone_matches"]
            or status["unobserved_events"]
            or status["anchor_state"] == "absent"
        )
        if recoverable:
            self.reconcile(records, actor=actor)
            status = self.status(records)

        orphans = set(status["orphan_receipts"])
        requested_digest: str | None = None
        if requested_identity is not None:
            requested_digest = self.store.key_digest(*requested_identity)
        if orphans and requested_digest not in orphans:
            raise IntegrityError(
                f"orphan idempotency receipt {sorted(orphans)[0]} has no canonical event "
                "in this scope"
            )
        remaining = [
            issue
            for issue in status["issues"]
            if not issue.startswith("orphan idempotency receipt")
        ]
        if remaining:
            raise IntegrityError(remaining[0])

    def prepare_rollback(
        self,
        records: Sequence[EventRecord],
        *,
        actor: Mapping[str, Any],
    ) -> dict[str, Any]:
        if self.store.get_migration() is None:
            raise IntegrityError("idempotency v2 migration has not completed")
        recovery = self.reconcile(records, actor=actor)
        if recovery["orphan_receipts"]:
            raise IntegrityError(
                "in-flight orphan receipts must be retried or abandoned before rollback"
            )
        if recovery["issues"]:
            raise IntegrityError(str(recovery["issues"][0]))
        event_map = self._event_map(records)
        for legacy in self.store.iter_legacy_reservations():
            info = event_map.get(legacy.key_digest)
            if info is None or not self._receipt_matches(legacy, info):
                raise IntegrityError(
                    "legacy receipt set is not safe for rollback from this checkout"
                )
        receipt_count = 0
        for digest, info in event_map.items():
            receipt = self.store.get_by_digest(digest)
            if receipt is None or not self._receipt_matches(receipt, info):
                raise IntegrityError("current receipt set is incomplete for rollback")
            self.store.prepare_legacy_receipt(receipt)
            receipt_count += 1
        abandonment_count = 0
        for abandonment in self.store.iter_abandonments():
            digest = str(abandonment["key_digest"])
            info = event_map.get(digest)
            if info is not None and self._abandonment_matches(abandonment, info):
                # v1 cannot represent a reconciled tombstone. The exact legacy
                # receipt still prevents different semantic content in this checkout.
                continue
            self.store.prepare_legacy_abandonment(abandonment)
            abandonment_count += 1
        return {
            "ok": True,
            "workspace_id": self.workspace_id,
            "scope": self.store.scope,
            "legacy_receipts_prepared": receipt_count,
            "legacy_abandonments_prepared": abandonment_count,
            "prepared_by_session_id": actor["session_id"],
            "warning": "stop linked-worktree writers and use v1 only in this checkout",
        }
