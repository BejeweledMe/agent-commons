"""Frozen, bounded canonical Design Package records and typed refusals."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from agent_commons.core.canonical import canonical_json_bytes
from agent_commons.core.ids import is_typed_id
from agent_commons.core.refs import normalize_ref
from agent_commons.errors import CommonsError, ValidationError

MAX_PACKAGE_TITLE_CHARS = 256
MAX_SCREEN_TITLE_CHARS = 256
MAX_SCREENS = 64
MAX_DESIGN_PACKAGE_BYTES = 65_536
MAX_OWNED_CONTAINER_ITEMS = 65
MAX_OWNED_CONTAINERS = 1_024
MAX_OWNED_DEPTH = 8

_CONTENT_REVISION_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_ALLOWED_CLASSIFICATIONS = frozenset({"public", "internal"})
_ALLOWED_MEDIA_TYPES = frozenset({"image/png", "image/jpeg"})


class DesignPackageRefusalCode(StrEnum):
    """Stable public failure vocabulary for Design Package operations."""

    MISSING = "design_package_missing"
    STALE = "design_package_stale"
    UNAUTHORIZED = "design_package_unauthorized"
    UNSAFE = "design_package_unsafe"
    OVERSIZED = "design_package_oversized"
    INVALID = "design_package_invalid"
    UNAVAILABLE = "design_package_unavailable"


class DesignPackageRefusal(CommonsError):
    """A fail-closed refusal with bounded, path-free remediation."""

    def __init__(
        self,
        code: DesignPackageRefusalCode,
        message: str,
        remediation: str,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.remediation = remediation

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code.value,
            "message": self.message,
            "remediation": self.remediation,
        }


@dataclass(frozen=True, order=True)
class ExactEntityBinding:
    """One exact canonical entity revision owned by a screen binding."""

    kind: str
    identifier: str
    revision: str

    @classmethod
    def from_payload(cls, value: Mapping[str, object]) -> ExactEntityBinding:
        owned = _own_mapping(value, "entity binding")
        if set(owned) != {"ref", "revision"}:
            raise ValidationError("entity binding must contain exactly ref and revision")
        ref = normalize_ref(_mapping(owned.get("ref"), "entity binding.ref"))
        if not is_typed_id(ref["id"], ref["kind"]):
            raise ValidationError("entity binding id must match its canonical kind")
        revision = _nonempty_string(owned.get("revision"), "entity binding.revision")
        if not is_typed_id(revision, "evt"):
            raise ValidationError("entity binding revision must be evt.<ULID>")
        return cls(kind=ref["kind"], identifier=ref["id"], revision=revision)

    def to_payload(self) -> dict[str, object]:
        return {
            "ref": {"kind": self.kind, "id": self.identifier},
            "revision": self.revision,
        }


@dataclass(frozen=True)
class ScreenBinding:
    """One ordered, exact and preview-qualified design screen."""

    screen_id: str
    ordinal: int
    title: str
    artifact_binding: ExactEntityBinding
    artifact_content_revision: str
    producer_task_binding: ExactEntityBinding
    classification: str
    media_type: str
    safe_preview_eligible: bool = True

    @classmethod
    def from_payload(cls, value: Mapping[str, object]) -> ScreenBinding:
        owned = _own_mapping(value, "screen binding")
        expected = {
            "screen_id",
            "ordinal",
            "title",
            "artifact_binding",
            "artifact_content_revision",
            "producer_task_binding",
            "classification",
            "media_type",
            "safe_preview_eligible",
        }
        if set(owned) != expected:
            raise ValidationError("screen binding contains unsupported or missing fields")
        screen_id = _nonempty_string(owned.get("screen_id"), "screen_id")
        if not is_typed_id(screen_id, "screen"):
            raise ValidationError("screen_id must be screen.<ULID>")
        ordinal = owned.get("ordinal")
        if isinstance(ordinal, bool) or not isinstance(ordinal, int):
            raise ValidationError("screen ordinal must be an integer")
        if not 1 <= ordinal <= MAX_SCREENS:
            raise ValidationError(f"screen ordinal must be between 1 and {MAX_SCREENS}")
        artifact = ExactEntityBinding.from_payload(
            _mapping(owned.get("artifact_binding"), "artifact_binding")
        )
        if artifact.kind != "artifact":
            raise ValidationError("artifact_binding must reference an artifact")
        task = ExactEntityBinding.from_payload(
            _mapping(owned.get("producer_task_binding"), "producer_task_binding")
        )
        if task.kind != "task":
            raise ValidationError("producer_task_binding must reference a task")
        content_revision = _nonempty_string(
            owned.get("artifact_content_revision"), "artifact_content_revision"
        )
        if _CONTENT_REVISION_RE.fullmatch(content_revision) is None:
            raise ValidationError("artifact_content_revision must be a lowercase SHA-256 revision")
        classification = _nonempty_string(owned.get("classification"), "classification")
        if classification not in _ALLOWED_CLASSIFICATIONS:
            raise ValidationError("screen classification is not preview eligible")
        media_type = _nonempty_string(owned.get("media_type"), "media_type")
        if media_type not in _ALLOWED_MEDIA_TYPES:
            raise ValidationError("screen media_type is not preview eligible")
        if owned.get("safe_preview_eligible") is not True:
            raise ValidationError("safe_preview_eligible must be true")
        return cls(
            screen_id=screen_id,
            ordinal=ordinal,
            title=_bounded_string(
                owned.get("title"), "screen title", maximum=MAX_SCREEN_TITLE_CHARS
            ),
            artifact_binding=artifact,
            artifact_content_revision=content_revision,
            producer_task_binding=task,
            classification=classification,
            media_type=media_type,
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "screen_id": self.screen_id,
            "ordinal": self.ordinal,
            "title": self.title,
            "artifact_binding": self.artifact_binding.to_payload(),
            "artifact_content_revision": self.artifact_content_revision,
            "producer_task_binding": self.producer_task_binding.to_payload(),
            "classification": self.classification,
            "media_type": self.media_type,
            "safe_preview_eligible": self.safe_preview_eligible,
        }


@dataclass(frozen=True)
class DesignPackageDraft:
    """A complete canonical Design Package revision before ledger metadata."""

    title: str
    screens: tuple[ScreenBinding, ...]

    @classmethod
    def from_payload(cls, value: Mapping[str, object]) -> DesignPackageDraft:
        owned = own_design_package_payload(value)
        if set(owned) != {"title", "screens"}:
            raise ValidationError("design package draft must contain exactly title and screens")
        raw_screens = _list(owned.get("screens"), "screens")
        if not 1 <= len(raw_screens) <= MAX_SCREENS:
            raise ValidationError(f"screens must contain 1 to {MAX_SCREENS} entries")
        screens = tuple(
            ScreenBinding.from_payload(_mapping(item, "screens item")) for item in raw_screens
        )
        if tuple(screen.ordinal for screen in screens) != tuple(range(1, len(screens) + 1)):
            raise ValidationError("screen ordinals must be contiguous and match array order")
        if len({screen.screen_id for screen in screens}) != len(screens):
            raise ValidationError("screen identifiers must be unique")
        draft = cls(
            title=_bounded_string(
                owned.get("title"), "package title", maximum=MAX_PACKAGE_TITLE_CHARS
            ),
            screens=screens,
        )
        if len(canonical_json_bytes(draft.to_payload())) > MAX_DESIGN_PACKAGE_BYTES:
            raise ValidationError(
                f"design package exceeds the {MAX_DESIGN_PACKAGE_BYTES}-byte semantic limit"
            )
        return draft

    def to_payload(self) -> dict[str, object]:
        return {
            "title": self.title,
            "screens": [screen.to_payload() for screen in self.screens],
        }


@dataclass(frozen=True)
class DesignPackageRecord(Mapping[str, object]):
    """One deeply owned, immutable projected Design Package revision."""

    design_package_id: str
    revision: str
    effective_revision: str
    source_event_id: str
    draft: DesignPackageDraft
    producer_session_id: str
    recorded_at: str | None
    author_session_ids: tuple[str, ...]
    state: str = "published"

    @classmethod
    def create(
        cls,
        *,
        design_package_id: str,
        revision: str,
        source_event_id: str,
        draft: DesignPackageDraft,
        producer_session_id: str,
        recorded_at: str | None,
        author_session_ids: tuple[str, ...],
    ) -> DesignPackageRecord:
        if not is_typed_id(design_package_id, "design_package"):
            raise ValidationError("design_package_id must be design_package.<ULID>")
        if not is_typed_id(revision, "evt") or not is_typed_id(source_event_id, "evt"):
            raise ValidationError("design package revisions must be evt.<ULID>")
        producer = _nonempty_string(producer_session_id, "producer_session_id")
        return cls(
            design_package_id=design_package_id,
            revision=revision,
            effective_revision=revision,
            source_event_id=source_event_id,
            draft=DesignPackageDraft.from_payload(draft.to_payload()),
            producer_session_id=producer,
            recorded_at=recorded_at,
            author_session_ids=tuple(sorted(set(author_session_ids))),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "design_package_id": self.design_package_id,
            **self.draft.to_payload(),
            "id": self.design_package_id,
            "state": self.state,
            "revision": self.revision,
            "effective_revision": self.effective_revision,
            "source_event_id": self.source_event_id,
            "producer_session_id": self.producer_session_id,
            "recorded_at": self.recorded_at,
            "author_session_ids": list(self.author_session_ids),
        }

    def __getitem__(self, key: str) -> object:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())


def validate_design_package_payload(event_type: str, payload: Mapping[str, Any]) -> None:
    """Validate the closed Design Package event family beyond JSON Schema."""

    if event_type not in {"design_package.created", "design_package.revised"}:
        return
    package_id = payload.get("design_package_id")
    if not is_typed_id(package_id, "design_package"):
        raise ValidationError("design_package_id must be design_package.<ULID>")
    allowed = {"design_package_id", "title", "screens"}
    if event_type == "design_package.revised":
        allowed.add("expected_revision")
        if not is_typed_id(payload.get("expected_revision"), "evt"):
            raise ValidationError("expected_revision must be evt.<ULID>")
    if set(payload) != allowed:
        raise ValidationError(f"{event_type} contains unsupported or missing fields")
    DesignPackageDraft.from_payload({"title": payload["title"], "screens": payload["screens"]})


def design_package_correction_changes(
    event_type: str,
    original: Mapping[str, object],
    replacement: Mapping[str, object],
) -> tuple[str, ...]:
    """Return provenance fields a correction is forbidden to change."""

    if event_type not in {"design_package.created", "design_package.revised"}:
        return ()
    return ("screens",) if original.get("screens") != replacement.get("screens") else ()


class _OwnershipBudget:
    def __init__(self) -> None:
        self.remaining = MAX_OWNED_CONTAINERS

    def take(self) -> None:
        self.remaining -= 1
        if self.remaining < 0:
            raise ValidationError("design package exceeds the safe ownership limit")


def own_design_package_payload(value: object) -> dict[str, object]:
    """Return a bounded plain-builtin copy of an untrusted package payload."""

    return _own_mapping(value, "design package payload")


def _own_mapping(value: object, field: str) -> dict[str, object]:
    try:
        owned = _own_plain(value, budget=_OwnershipBudget(), depth=0)
    except ValidationError:
        raise
    except Exception:
        raise ValidationError(f"{field} could not be safely copied") from None
    if not isinstance(owned, dict):
        raise ValidationError(f"{field} must be an object")
    return owned


def _own_plain(value: object, *, budget: _OwnershipBudget, depth: int) -> object:
    if depth > MAX_OWNED_DEPTH:
        raise ValidationError("design package exceeds the safe ownership depth")
    if isinstance(value, Mapping):
        budget.take()
        try:
            iterator = iter(value.items())
        except Exception:
            raise ValidationError("design package mapping could not be safely copied") from None
        owned: dict[str, object] = {}
        for index in range(MAX_OWNED_CONTAINER_ITEMS + 1):
            try:
                key, item = next(iterator)
            except StopIteration:
                return owned
            except Exception:
                raise ValidationError("design package mapping could not be safely copied") from None
            if index == MAX_OWNED_CONTAINER_ITEMS:
                raise ValidationError("design package container exceeds the safe ownership limit")
            if not isinstance(key, str):
                raise ValidationError("design package object keys must be strings")
            plain_key = str(key)
            if plain_key in owned:
                raise ValidationError("design package object keys must be unique")
            owned[plain_key] = _own_plain(item, budget=budget, depth=depth + 1)
        raise AssertionError("unreachable")
    if isinstance(value, list):
        budget.take()
        try:
            iterator = iter(value)
        except Exception:
            raise ValidationError("design package list could not be safely copied") from None
        owned_list: list[object] = []
        for index in range(MAX_OWNED_CONTAINER_ITEMS + 1):
            try:
                item = next(iterator)
            except StopIteration:
                return owned_list
            except Exception:
                raise ValidationError("design package list could not be safely copied") from None
            if index == MAX_OWNED_CONTAINER_ITEMS:
                raise ValidationError("design package container exceeds the safe ownership limit")
            owned_list.append(_own_plain(item, budget=budget, depth=depth + 1))
        raise AssertionError("unreachable")
    if isinstance(value, str):
        return str(value)
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    return value


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ValidationError(f"{field} must be an object")
    return value


def _list(value: object, field: str) -> list[object]:
    if not isinstance(value, list):
        raise ValidationError(f"{field} must be a list")
    return list(value)


def _nonempty_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field} must be a non-empty string")
    return value.strip()


def _bounded_string(value: object, field: str, *, maximum: int) -> str:
    normalized = _nonempty_string(value, field)
    try:
        normalized.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValidationError(f"{field} must be valid UTF-8 text") from exc
    if len(normalized) > maximum:
        raise ValidationError(f"{field} must contain at most {maximum} characters")
    return normalized
