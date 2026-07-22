"""Crash-safe, checkout-scoped idempotency receipt storage."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agent_commons.config import CommonsPaths, receipt_scope_descriptor
from agent_commons.core.canonical import canonical_json_file_bytes, loads_json_strict, sha256_bytes
from agent_commons.core.ids import is_typed_id, new_sortable_id
from agent_commons.core.schema_registry import SchemaRegistry
from agent_commons.errors import IdempotencyConflictError, ImmutableCollisionError, ValidationError
from agent_commons.storage.atomic import _fsync_directory, atomic_write_immutable


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class IdempotencyReservation:
    key_digest: str
    namespace: str
    semantic_sha256: str
    event_id: str
    recorded_at: str
    path: Path
    created: bool


class IdempotencyStore:
    """Store in-flight receipts in the current worktree/ref recovery scope."""

    def __init__(
        self,
        paths: CommonsPaths,
        schemas: SchemaRegistry,
        *,
        workspace_id: str | None = None,
    ) -> None:
        self.paths = paths
        self.schemas = schemas
        self._workspace_id = workspace_id
        self._scope_cache: dict[str, str] | None = None
        self.scope_refresh_count = 0
        self.scope_git_probe_count = 0

    def bind_workspace(self, workspace_id: str) -> None:
        if not isinstance(workspace_id, str) or not workspace_id:
            raise ValidationError("idempotency workspace ID must be non-empty")
        if self._workspace_id is not None and self._workspace_id != workspace_id:
            raise IdempotencyConflictError("idempotency store is bound to another workspace")
        self._workspace_id = workspace_id

    @property
    def workspace_id(self) -> str:
        if self._workspace_id is None:
            raise ValidationError("idempotency store is not bound to a workspace")
        return self._workspace_id

    @property
    def scope(self) -> dict[str, str]:
        if self._scope_cache is None:
            self.refresh_scope()
        assert self._scope_cache is not None
        return dict(self._scope_cache)

    def refresh_scope(self) -> dict[str, str]:
        """Probe Git once per public operation, then reuse the stable descriptor."""

        scope = receipt_scope_descriptor(self.paths.repo_root, self.workspace_id)
        self._scope_cache = scope
        self.scope_refresh_count += 1
        self.scope_git_probe_count += {
            "symbolic": 2,
            "detached": 3,
            "non-git": 1,
        }[scope["ref_kind"]]
        return dict(scope)

    @property
    def scope_root(self) -> Path:
        return self.paths.idempotency_v2 / "scopes" / self.scope["scope_id"]

    @property
    def receipts_root(self) -> Path:
        return self.scope_root / "receipts"

    @property
    def anchor_path(self) -> Path:
        return self.scope_root / "ledger-anchor.json"

    @property
    def migration_path(self) -> Path:
        return self.paths.idempotency_v2 / "migration.json"

    @staticmethod
    def key_digest(namespace: str, key: str) -> str:
        if not isinstance(namespace, str) or not namespace.strip():
            raise ValidationError("idempotency namespace must be non-empty")
        if not isinstance(key, str) or not key:
            raise ValidationError("idempotency key must be non-empty")
        return sha256_bytes((namespace + "\0" + key).encode("utf-8"))

    def _path(self, digest: str) -> Path:
        return self.receipts_root / digest[:2] / f"{digest}.json"

    def _legacy_path(self, digest: str) -> Path:
        return self.paths.idempotency / digest[:2] / f"{digest}.json"

    def _abandonment_path(self, digest: str) -> Path:
        return self.paths.idempotency_v2 / "abandonments" / f"{digest}.json"

    def _legacy_abandonment_path(self, digest: str) -> Path:
        return self.paths.legacy_abandonments / f"{digest}.json"

    def _reconciliation_path(self, digest: str) -> Path:
        return (
            self.paths.idempotency_v2
            / "reconciliations"
            / self.scope["scope_id"]
            / f"{digest}.json"
        )

    def assert_operational_path(self, path: Path) -> None:
        root = self.paths.state_root.absolute()
        candidate = path.absolute()
        try:
            relative = candidate.relative_to(root)
        except ValueError as exc:
            raise IdempotencyConflictError(
                f"operational idempotency path escapes state root: {path}"
            ) from exc
        cursor = root
        components = relative.parts[:-1] if path.suffix else relative.parts
        for part in components:
            cursor = cursor / part
            if cursor.is_symlink():
                raise IdempotencyConflictError(
                    f"operational idempotency parent must not be a symlink: {cursor}"
                )
            if cursor.exists() and not cursor.is_dir():
                raise IdempotencyConflictError(
                    f"operational idempotency parent is not a directory: {cursor}"
                )

    def ensure_directory(self, path: Path) -> None:
        self.assert_operational_path(path)
        root = self.paths.state_root.absolute()
        relative = path.absolute().relative_to(root)
        cursor = root
        for part in relative.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise IdempotencyConflictError(
                    f"operational idempotency directory must not be a symlink: {cursor}"
                )
            cursor.mkdir(mode=0o700, exist_ok=True)
            if not cursor.is_dir() or cursor.is_symlink():
                raise IdempotencyConflictError(
                    f"unsafe operational idempotency directory: {cursor}"
                )

    @staticmethod
    def _validate_digest(digest: str) -> str:
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValidationError("idempotency receipt digest must be lowercase SHA-256")
        return digest

    def ensure_scope(self) -> Mapping[str, object]:
        scope = self.scope
        document: dict[str, object] = {
            "schema": "commons.idempotency_scope.v2",
            **scope,
        }
        self.schemas.validate("commons.idempotency_scope.v2", document)
        self.ensure_directory(self.scope_root)
        atomic_write_immutable(
            self.scope_root / "scope.json",
            canonical_json_file_bytes(document),
            mode=0o600,
        )
        return document

    def get_scope_document(self) -> dict[str, object] | None:
        value = self._load_document(
            self.scope_root / "scope.json",
            schema="commons.idempotency_scope.v2",
            label="idempotency scope",
        )
        if value is not None and value != {"schema": "commons.idempotency_scope.v2", **self.scope}:
            raise IdempotencyConflictError("idempotency scope descriptor conflicts with its path")
        return value

    def lookup(self, *, namespace: str, key: str) -> IdempotencyReservation | None:
        digest = self.key_digest(namespace, key)
        if self.get_abandonment(digest) is not None and self.get_reconciliation(digest) is None:
            raise IdempotencyConflictError(
                "idempotency identity was explicitly abandoned; choose a new key"
            )
        path = self._path(digest)
        value = self._load(path)
        if value is None:
            return None
        if value["key_digest"] != digest or value["namespace"] != namespace:
            raise IdempotencyConflictError(
                "idempotency receipt does not match its requested identity"
            )
        return self._reservation(value, path, created=False)

    def reserve(
        self,
        *,
        namespace: str,
        key: str,
        semantic_sha256: str,
        event_id_factory: Callable[[], str] | None = None,
        recorded_at: str | None = None,
    ) -> IdempotencyReservation:
        digest = self.key_digest(namespace, key)
        if self.get_abandonment(digest) is not None and self.get_reconciliation(digest) is None:
            raise IdempotencyConflictError(
                "idempotency identity was explicitly abandoned; choose a new key"
            )
        if len(semantic_sha256) != 64:
            raise ValidationError("semantic_sha256 must be a SHA-256 hex digest")
        path = self._path(digest)
        existing = self._load(path)
        if existing is not None:
            return self._validate_existing(
                existing,
                path,
                namespace=namespace,
                semantic_sha256=semantic_sha256,
                created=False,
            )

        event_id = (event_id_factory or (lambda: new_sortable_id("evt")))()
        if not is_typed_id(event_id, "evt"):
            raise ValidationError("idempotency event ID must be an evt.<ULID> identity")
        receipt = {
            "schema": "commons.idempotency_receipt.v1",
            "key_digest": digest,
            "namespace": namespace,
            "semantic_sha256": semantic_sha256,
            "event_id": event_id,
            "recorded_at": recorded_at or _utc_now(),
        }
        self.schemas.validate("commons.idempotency_receipt.v1", receipt)
        self.ensure_directory(path.parent)
        try:
            result = atomic_write_immutable(path, canonical_json_file_bytes(receipt), mode=0o600)
        except ImmutableCollisionError:
            winner = self._load(path)
            if winner is None:  # pragma: no cover - defensive filesystem guard
                raise
            return self._validate_existing(
                winner,
                path,
                namespace=namespace,
                semantic_sha256=semantic_sha256,
                created=False,
            )
        return self._validate_existing(
            receipt,
            path,
            namespace=namespace,
            semantic_sha256=semantic_sha256,
            created=result.created,
        )

    def import_reservation(self, reservation: IdempotencyReservation) -> IdempotencyReservation:
        path = self._path(reservation.key_digest)
        receipt = {
            "schema": "commons.idempotency_receipt.v1",
            "key_digest": reservation.key_digest,
            "namespace": reservation.namespace,
            "semantic_sha256": reservation.semantic_sha256,
            "event_id": reservation.event_id,
            "recorded_at": reservation.recorded_at,
        }
        self.schemas.validate("commons.idempotency_receipt.v1", receipt)
        self.ensure_directory(path.parent)
        result = atomic_write_immutable(path, canonical_json_file_bytes(receipt), mode=0o600)
        return self._reservation(receipt, path, created=result.created)

    def get_by_digest(self, digest: str) -> IdempotencyReservation | None:
        digest = self._validate_digest(digest)
        path = self._path(digest)
        value = self._load(path)
        if value is None:
            return None
        if value["key_digest"] != digest:
            raise IdempotencyConflictError(
                "idempotency receipt digest does not match its canonical path"
            )
        return self._reservation(value, path, created=False)

    def get_legacy_by_digest(self, digest: str) -> IdempotencyReservation | None:
        digest = self._validate_digest(digest)
        path = self._legacy_path(digest)
        value = self._load(path)
        if value is None:
            return None
        if value["key_digest"] != digest:
            raise IdempotencyConflictError(
                "legacy idempotency receipt digest does not match its canonical path"
            )
        return self._reservation(value, path, created=False)

    def iter_reservations(self) -> Iterable[IdempotencyReservation]:
        yield from self._iter_receipts(self.receipts_root, legacy=False)

    def iter_legacy_reservations(self) -> Iterable[IdempotencyReservation]:
        yield from self._iter_receipts(self.paths.idempotency, legacy=True)

    def _iter_receipts(self, root: Path, *, legacy: bool) -> Iterable[IdempotencyReservation]:
        self.assert_operational_path(root)
        if not root.exists():
            return
        for path in sorted(root.rglob("*.json")):
            try:
                relative = path.relative_to(root)
            except ValueError as exc:  # pragma: no cover - rglob remains below the root
                raise IdempotencyConflictError(
                    "idempotency receipt is outside operational storage"
                ) from exc
            digest = path.stem
            if (
                len(relative.parts) != 2
                or path.suffix != ".json"
                or relative.parts[0] != digest[:2]
            ):
                raise IdempotencyConflictError("idempotency receipt has an invalid canonical path")
            value = self.get_legacy_by_digest(digest) if legacy else self.get_by_digest(digest)
            if value is None:  # pragma: no cover - path was just enumerated
                raise IdempotencyConflictError("idempotency receipt disappeared during scan")
            yield value

    def get_abandonment(self, digest: str) -> dict[str, object] | None:
        digest = self._validate_digest(digest)
        return self._get_abandonment_at(self._abandonment_path(digest), digest)

    def get_legacy_abandonment(self, digest: str) -> dict[str, object] | None:
        digest = self._validate_digest(digest)
        return self._get_abandonment_at(self._legacy_abandonment_path(digest), digest)

    def _get_abandonment_at(self, path: Path, digest: str) -> dict[str, object] | None:
        value = self._load_document(
            path,
            schema="commons.idempotency_abandonment.v1",
            label="idempotency abandonment",
        )
        if value is not None and (value["key_digest"] != digest or path.stem != digest):
            raise IdempotencyConflictError(
                "idempotency abandonment digest does not match its canonical path"
            )
        return value

    def iter_abandonments(self) -> Iterable[Mapping[str, object]]:
        yield from self._iter_abandonments(self.paths.idempotency_v2 / "abandonments", legacy=False)

    def iter_legacy_abandonments(self) -> Iterable[Mapping[str, object]]:
        yield from self._iter_abandonments(self.paths.legacy_abandonments, legacy=True)

    def _iter_abandonments(self, root: Path, *, legacy: bool) -> Iterable[Mapping[str, object]]:
        self.assert_operational_path(root)
        if not root.exists():
            return
        for path in sorted(root.glob("*.json")):
            value = (
                self.get_legacy_abandonment(path.stem)
                if legacy
                else self.get_abandonment(path.stem)
            )
            if value is None:  # pragma: no cover - path was just enumerated
                raise IdempotencyConflictError("idempotency abandonment disappeared during scan")
            yield value

    def copy_legacy_abandonment(self, abandonment: Mapping[str, object]) -> None:
        value = dict(abandonment)
        digest = self._validate_digest(str(value.get("key_digest", "")))
        self.schemas.validate("commons.idempotency_abandonment.v1", value)
        self.ensure_directory(self._abandonment_path(digest).parent)
        atomic_write_immutable(
            self._abandonment_path(digest), canonical_json_file_bytes(value), mode=0o600
        )

    def get_reconciliation(self, digest: str) -> dict[str, object] | None:
        digest = self._validate_digest(digest)
        value = self._load_document(
            self._reconciliation_path(digest),
            schema="commons.idempotency_reconciliation.v1",
            label="idempotency reconciliation",
        )
        if value is None:
            return None
        if (
            value["key_digest"] != digest
            or value["workspace_id"] != self.workspace_id
            or value["scope_id"] != self.scope["scope_id"]
        ):
            raise IdempotencyConflictError(
                "idempotency reconciliation conflicts with its recovery scope"
            )
        abandonment = self.get_abandonment(digest)
        if abandonment is None:
            raise IdempotencyConflictError("idempotency reconciliation has no tombstone")
        tombstone_sha256 = sha256_bytes(canonical_json_file_bytes(dict(abandonment)))
        if value["tombstone_sha256"] != tombstone_sha256:
            raise IdempotencyConflictError("idempotency reconciliation tombstone hash conflicts")
        return value

    def reconcile_abandonment(
        self,
        abandonment: Mapping[str, object],
        *,
        namespace: str,
        key: str,
        semantic_sha256: str,
        event_id: str,
        event_sha256: str,
        actor_session_id: str,
        actor_principal_id: str,
        reason: str,
    ) -> Mapping[str, object]:
        digest = self.key_digest(namespace, key)
        expected = {
            "key_digest": digest,
            "namespace": namespace,
            "semantic_sha256": semantic_sha256,
            "event_id": event_id,
        }
        for field, value in expected.items():
            if abandonment.get(field) != value:
                raise IdempotencyConflictError(
                    "canonical event does not exactly match its idempotency abandonment"
                )
        document: dict[str, object] = {
            "schema": "commons.idempotency_reconciliation.v1",
            "workspace_id": self.workspace_id,
            "scope_id": self.scope["scope_id"],
            **expected,
            "event_sha256": event_sha256,
            "tombstone_sha256": sha256_bytes(canonical_json_file_bytes(dict(abandonment))),
            "actor_session_id": actor_session_id,
            "actor_principal_id": actor_principal_id,
            "reason": reason,
            "reconciled_at": _utc_now(),
        }
        self.schemas.validate("commons.idempotency_reconciliation.v1", document)
        self.ensure_directory(self._reconciliation_path(digest).parent)
        atomic_write_immutable(
            self._reconciliation_path(digest),
            canonical_json_file_bytes(document),
            mode=0o600,
        )
        return document

    def abandon(
        self,
        reservation: IdempotencyReservation,
        *,
        reason: str,
        actor_session_id: str,
        actor_principal_id: str,
    ) -> Mapping[str, object]:
        """Tombstone an orphan reservation before removing its live receipt."""

        digest = self._validate_digest(reservation.key_digest)
        existing = self.get_abandonment(digest)
        if existing is None:
            abandonment: dict[str, object] = {
                "schema": "commons.idempotency_abandonment.v1",
                "key_digest": digest,
                "namespace": reservation.namespace,
                "semantic_sha256": reservation.semantic_sha256,
                "event_id": reservation.event_id,
                "reason": reason,
                "actor_session_id": actor_session_id,
                "actor_principal_id": actor_principal_id,
                "abandoned_at": _utc_now(),
            }
            self.schemas.validate("commons.idempotency_abandonment.v1", abandonment)
            self.ensure_directory(self._abandonment_path(digest).parent)
            atomic_write_immutable(
                self._abandonment_path(digest),
                canonical_json_file_bytes(abandonment),
                mode=0o600,
            )
        else:
            abandonment = existing
            for field in ("namespace", "semantic_sha256", "event_id"):
                if abandonment[field] != getattr(reservation, field):
                    raise IdempotencyConflictError(
                        "idempotency abandonment conflicts with its receipt"
                    )
        reservation.path.unlink(missing_ok=True)
        _fsync_directory(reservation.path.parent)
        return abandonment

    def get_migration(self) -> dict[str, object] | None:
        value = self._load_document(
            self.migration_path,
            schema="commons.idempotency_migration.v2",
            label="idempotency migration",
        )
        if value is not None and value["workspace_id"] != self.workspace_id:
            raise IdempotencyConflictError("idempotency migration belongs to another workspace")
        return value

    def mark_migrated(
        self,
        *,
        actor_session_id: str,
        legacy_receipt_count: int,
        legacy_abandonment_count: int,
    ) -> Mapping[str, object]:
        document: dict[str, object] = {
            "schema": "commons.idempotency_migration.v2",
            "workspace_id": self.workspace_id,
            "format": 2,
            "migrated_at": _utc_now(),
            "migrated_by_session_id": actor_session_id,
            "legacy_receipt_count": legacy_receipt_count,
            "legacy_abandonment_count": legacy_abandonment_count,
        }
        self.schemas.validate("commons.idempotency_migration.v2", document)
        self.ensure_directory(self.migration_path.parent)
        atomic_write_immutable(self.migration_path, canonical_json_file_bytes(document), mode=0o600)
        return document

    def prepare_legacy_receipt(self, reservation: IdempotencyReservation) -> None:
        receipt = {
            "schema": "commons.idempotency_receipt.v1",
            "key_digest": reservation.key_digest,
            "namespace": reservation.namespace,
            "semantic_sha256": reservation.semantic_sha256,
            "event_id": reservation.event_id,
            "recorded_at": reservation.recorded_at,
        }
        self.schemas.validate("commons.idempotency_receipt.v1", receipt)
        self.ensure_directory(self._legacy_path(reservation.key_digest).parent)
        atomic_write_immutable(
            self._legacy_path(reservation.key_digest),
            canonical_json_file_bytes(receipt),
            mode=0o600,
        )

    def prepare_legacy_abandonment(self, abandonment: Mapping[str, object]) -> None:
        value = dict(abandonment)
        digest = self._validate_digest(str(value.get("key_digest", "")))
        self.schemas.validate("commons.idempotency_abandonment.v1", value)
        self.ensure_directory(self._legacy_abandonment_path(digest).parent)
        atomic_write_immutable(
            self._legacy_abandonment_path(digest),
            canonical_json_file_bytes(value),
            mode=0o600,
        )

    def _load(self, path: Path) -> dict[str, object] | None:
        return self._load_document(
            path,
            schema="commons.idempotency_receipt.v1",
            label="idempotency receipt",
        )

    def _load_document(
        self,
        path: Path,
        *,
        schema: str,
        label: str,
    ) -> dict[str, object] | None:
        self.assert_operational_path(path)
        if path.is_symlink():
            raise IdempotencyConflictError(f"{label} path must not be a symlink: {path}")
        if not path.exists():
            return None
        raw = path.read_bytes()
        value = loads_json_strict(raw)
        if not isinstance(value, dict):
            raise IdempotencyConflictError(f"{label} is not an object: {path}")
        if raw != canonical_json_file_bytes(value):
            raise IdempotencyConflictError(f"{label} is not canonical JSON: {path}")
        try:
            self.schemas.validate(schema, value)
        except ValidationError as exc:
            raise IdempotencyConflictError(f"invalid {label}: {path}") from exc
        return value

    @staticmethod
    def _reservation(
        value: Mapping[str, object], path: Path, *, created: bool
    ) -> IdempotencyReservation:
        return IdempotencyReservation(
            key_digest=str(value["key_digest"]),
            namespace=str(value["namespace"]),
            semantic_sha256=str(value["semantic_sha256"]),
            event_id=str(value["event_id"]),
            recorded_at=str(value["recorded_at"]),
            path=path,
            created=created,
        )

    def _validate_existing(
        self,
        value: Mapping[str, object],
        path: Path,
        *,
        namespace: str,
        semantic_sha256: str,
        created: bool,
    ) -> IdempotencyReservation:
        if value["namespace"] != namespace or value["semantic_sha256"] != semantic_sha256:
            raise IdempotencyConflictError(
                "idempotency key is already reserved for different semantic content"
            )
        if value["key_digest"] != path.stem:
            raise IdempotencyConflictError(
                "idempotency receipt digest does not match its canonical path"
            )
        return self._reservation(value, path, created=created)
