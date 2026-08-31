"""Frozen, bounded Context Pack domain records and typed refusals."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from agent_commons.core.canonical import canonical_json_bytes
from agent_commons.core.ids import is_typed_id
from agent_commons.core.refs import normalize_ref
from agent_commons.errors import CommonsError, ValidationError

MAX_SUMMARY_CHARS = 4096
MAX_FACTS = 64
MAX_FACT_CHARS = 1024
MAX_SOURCES_PER_FACT = 8
MAX_DECISION_REFS = 32
MAX_OPEN_QUESTIONS = 32
MAX_OPEN_QUESTION_CHARS = 1024
MAX_CONTEXT_PACK_BYTES = 65_536
MAX_OWNED_CONTAINER_ITEMS = 65
MAX_OWNED_CONTAINERS = 2_048
MAX_OWNED_DEPTH = 8

ALLOWED_SOURCE_KINDS = frozenset({"artifact", "finding", "task", "thread", "verification"})


class ContextPackRefusalCode(StrEnum):
    """Stable public failure vocabulary for Context Pack operations."""

    MISSING = "context_pack_missing"
    STALE = "context_pack_stale"
    UNSAFE = "context_pack_unsafe"
    OVERSIZED = "context_pack_oversized"
    INVALID = "context_pack_invalid"
    UNAVAILABLE = "context_pack_unavailable"


class ContextPackRefusal(CommonsError):
    """A fail-closed Context Pack refusal with bounded remediation text."""

    def __init__(
        self,
        code: ContextPackRefusalCode,
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
class RevisionBoundRef:
    """One exact canonical reference owned by the containing record."""

    kind: str
    identifier: str
    revision: str

    @classmethod
    def from_payload(cls, value: Mapping[str, object]) -> RevisionBoundRef:
        value = _own_mapping(value, "revision-bound reference")
        if set(value) != {"ref", "revision"}:
            raise ValidationError("revision-bound reference must contain ref and revision")
        ref = normalize_ref(_mapping(value.get("ref"), "reference.ref"))
        revision = _nonempty_string(value.get("revision"), "reference.revision")
        if not is_typed_id(ref["id"], ref["kind"]):
            raise ValidationError("reference.ref.id must match its canonical entity kind")
        if not is_typed_id(revision, "evt"):
            raise ValidationError("reference.revision must be an evt.<ULID> identifier")
        return cls(kind=ref["kind"], identifier=ref["id"], revision=revision)

    def to_payload(self) -> dict[str, object]:
        return {
            "ref": {"kind": self.kind, "id": self.identifier},
            "revision": self.revision,
        }


@dataclass(frozen=True)
class ContextFact:
    statement: str
    source_refs: tuple[RevisionBoundRef, ...]

    @classmethod
    def from_payload(cls, value: Mapping[str, object]) -> ContextFact:
        value = _own_mapping(value, "fact")
        if set(value) != {"statement", "source_refs"}:
            raise ValidationError("fact must contain exactly statement and source_refs")
        statement = _bounded_string(
            value.get("statement"), "fact.statement", maximum=MAX_FACT_CHARS
        )
        raw_refs = _list(value.get("source_refs"), "fact.source_refs")
        if not 1 <= len(raw_refs) <= MAX_SOURCES_PER_FACT:
            raise ValidationError(
                f"fact.source_refs must contain 1 to {MAX_SOURCES_PER_FACT} references"
            )
        refs = tuple(
            RevisionBoundRef.from_payload(_mapping(item, "fact.source_refs item"))
            for item in raw_refs
        )
        if any(ref.kind not in ALLOWED_SOURCE_KINDS for ref in refs):
            raise ValidationError("fact.source_refs contains an unsupported source kind")
        if len(set(refs)) != len(refs):
            raise ValidationError("fact.source_refs must be unique")
        return cls(statement=statement, source_refs=refs)

    def to_payload(self) -> dict[str, object]:
        return {
            "statement": self.statement,
            "source_refs": [ref.to_payload() for ref in self.source_refs],
        }


@dataclass(frozen=True)
class ContextPackDraft:
    """A complete semantic Context Pack revision before ledger metadata."""

    summary: str
    facts: tuple[ContextFact, ...]
    decision_refs: tuple[RevisionBoundRef, ...]
    open_questions: tuple[str, ...]

    @classmethod
    def from_payload(cls, value: Mapping[str, object]) -> ContextPackDraft:
        value = own_context_pack_payload(value)
        allowed = {"summary", "facts", "decision_refs", "open_questions"}
        if set(value) != allowed:
            raise ValidationError(
                "context pack draft must contain exactly summary, facts, decision_refs, "
                "and open_questions"
            )
        summary = _bounded_string(value.get("summary"), "summary", maximum=MAX_SUMMARY_CHARS)
        raw_facts = _list(value.get("facts"), "facts")
        if len(raw_facts) > MAX_FACTS:
            raise ValidationError(f"facts must contain at most {MAX_FACTS} entries")
        facts = tuple(ContextFact.from_payload(_mapping(item, "facts item")) for item in raw_facts)
        raw_decisions = _list(value.get("decision_refs"), "decision_refs")
        if len(raw_decisions) > MAX_DECISION_REFS:
            raise ValidationError(f"decision_refs must contain at most {MAX_DECISION_REFS} entries")
        decisions = tuple(
            RevisionBoundRef.from_payload(_mapping(item, "decision_refs item"))
            for item in raw_decisions
        )
        if any(ref.kind != "decision" for ref in decisions):
            raise ValidationError("decision_refs may reference only decisions")
        if len(set(decisions)) != len(decisions):
            raise ValidationError("decision_refs must be unique")
        raw_questions = _list(value.get("open_questions"), "open_questions")
        if len(raw_questions) > MAX_OPEN_QUESTIONS:
            raise ValidationError(
                f"open_questions must contain at most {MAX_OPEN_QUESTIONS} entries"
            )
        questions = tuple(
            _bounded_string(
                question,
                "open_questions item",
                maximum=MAX_OPEN_QUESTION_CHARS,
            )
            for question in raw_questions
        )
        draft = cls(
            summary=summary,
            facts=facts,
            decision_refs=decisions,
            open_questions=questions,
        )
        if len(canonical_json_bytes(draft.to_payload())) > MAX_CONTEXT_PACK_BYTES:
            raise ValidationError(
                f"context pack exceeds the {MAX_CONTEXT_PACK_BYTES}-byte semantic limit"
            )
        return draft

    def to_payload(self) -> dict[str, object]:
        return {
            "summary": self.summary,
            "facts": [fact.to_payload() for fact in self.facts],
            "decision_refs": [ref.to_payload() for ref in self.decision_refs],
            "open_questions": list(self.open_questions),
        }

    @property
    def source_refs(self) -> tuple[RevisionBoundRef, ...]:
        return tuple(ref for fact in self.facts for ref in fact.source_refs)


@dataclass(frozen=True)
class ContextPackBinding:
    """Exact immutable compiler binding; launch integration is deliberately C2."""

    context_pack_id: str
    context_pack_revision: str
    compiler_version: str
    compiled_context_fingerprint: str


@dataclass(frozen=True)
class ContextPackRecord(Mapping[str, object]):
    """One deeply owned, immutable projected Context Pack revision."""

    context_pack_id: str
    revision: str
    effective_revision: str
    source_event_id: str
    draft: ContextPackDraft
    recorded_at: str | None
    author_session_ids: tuple[str, ...]
    state: str = "published"

    @classmethod
    def create(
        cls,
        *,
        context_pack_id: str,
        revision: str,
        source_event_id: str,
        draft: ContextPackDraft,
        recorded_at: str | None,
        author_session_ids: tuple[str, ...],
    ) -> ContextPackRecord:
        if not is_typed_id(context_pack_id, "context_pack"):
            raise ValidationError("context_pack_id must be context_pack.<ULID>")
        if not is_typed_id(revision, "evt") or not is_typed_id(source_event_id, "evt"):
            raise ValidationError("context pack revisions must be evt.<ULID> identifiers")
        return cls(
            context_pack_id=context_pack_id,
            revision=revision,
            effective_revision=revision,
            source_event_id=source_event_id,
            draft=ContextPackDraft.from_payload(draft.to_payload()),
            recorded_at=recorded_at,
            author_session_ids=tuple(sorted(set(author_session_ids))),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "context_pack_id": self.context_pack_id,
            **self.draft.to_payload(),
            "id": self.context_pack_id,
            "state": self.state,
            "revision": self.revision,
            "effective_revision": self.effective_revision,
            "source_event_id": self.source_event_id,
            "recorded_at": self.recorded_at,
            "author_session_ids": list(self.author_session_ids),
        }

    def __getitem__(self, key: str) -> object:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())


def validate_context_pack_payload(event_type: str, payload: Mapping[str, Any]) -> None:
    """Validate the closed Context Pack event family beyond JSON Schema."""

    if event_type not in {"context_pack.created", "context_pack.revised"}:
        return
    pack_id = payload.get("context_pack_id")
    if not is_typed_id(pack_id, "context_pack"):
        raise ValidationError("context_pack_id must be context_pack.<ULID>")
    allowed = {
        "context_pack_id",
        "summary",
        "facts",
        "decision_refs",
        "open_questions",
    }
    if event_type == "context_pack.revised":
        allowed.add("expected_revision")
        revision = payload.get("expected_revision")
        if not is_typed_id(revision, "evt"):
            raise ValidationError("expected_revision must be evt.<ULID>")
    if set(payload) != allowed:
        raise ValidationError(f"{event_type} contains unsupported or missing fields")
    ContextPackDraft.from_payload({key: payload[key] for key in ContextPackDraftFields})


ContextPackDraftFields = ("summary", "facts", "decision_refs", "open_questions")

CONTEXT_PACK_PROVENANCE_FIELDS = ("facts", "decision_refs")


def context_pack_correction_changes(
    event_type: str,
    original: Mapping[str, object],
    replacement: Mapping[str, object],
) -> tuple[str, ...]:
    """Return pack fields whose correction would detach content from provenance.

    Immutable event-envelope relations are not rewritten by ``event.corrected``.
    Facts include their exact source bindings, and decisions are themselves exact
    bindings, so changing either field requires a canonical pack revision.
    """

    if event_type not in {"context_pack.created", "context_pack.revised"}:
        return ()
    return tuple(
        field
        for field in CONTEXT_PACK_PROVENANCE_FIELDS
        if original.get(field) != replacement.get(field)
    )


class _OwnershipBudget:
    def __init__(self) -> None:
        self.remaining = MAX_OWNED_CONTAINERS

    def take(self) -> None:
        self.remaining -= 1
        if self.remaining < 0:
            raise ValidationError("context pack exceeds the safe ownership limit")


def _own_mapping(value: object, field: str) -> dict[str, object]:
    """Boundedly copy possibly adversarial containers into plain builtins."""

    try:
        owned = _own_plain(value, budget=_OwnershipBudget(), depth=0)
    except ValidationError:
        raise
    except Exception:
        # Container exceptions may include their contents.  Preserve neither
        # the exception text nor repr at this untrusted boundary.
        raise ValidationError(f"{field} could not be safely copied") from None
    if not isinstance(owned, dict):
        raise ValidationError(f"{field} must be an object")
    return owned


def own_context_pack_payload(value: object) -> dict[str, object]:
    """Return a bounded plain-builtin copy of an untrusted pack payload."""

    return _own_mapping(value, "context pack payload")


def _own_plain(value: object, *, budget: _OwnershipBudget, depth: int) -> object:
    if depth > MAX_OWNED_DEPTH:
        raise ValidationError("context pack exceeds the safe ownership depth")
    if isinstance(value, Mapping):
        budget.take()
        try:
            iterator = iter(value.items())
        except Exception:
            raise ValidationError("context pack mapping could not be safely copied") from None
        owned: dict[str, object] = {}
        for index in range(MAX_OWNED_CONTAINER_ITEMS + 1):
            try:
                key, item = next(iterator)
            except StopIteration:
                return owned
            except Exception:
                raise ValidationError("context pack mapping could not be safely copied") from None
            if index == MAX_OWNED_CONTAINER_ITEMS:
                raise ValidationError("context pack container exceeds the safe ownership limit")
            if not isinstance(key, str):
                raise ValidationError("context pack object keys must be strings")
            plain_key = str(key)
            if plain_key in owned:
                raise ValidationError("context pack object keys must be unique")
            owned[plain_key] = _own_plain(item, budget=budget, depth=depth + 1)
        raise AssertionError("unreachable")
    if isinstance(value, list):
        budget.take()
        try:
            iterator = iter(value)
        except Exception:
            raise ValidationError("context pack list could not be safely copied") from None
        owned_list: list[object] = []
        for index in range(MAX_OWNED_CONTAINER_ITEMS + 1):
            try:
                item = next(iterator)
            except StopIteration:
                return owned_list
            except Exception:
                raise ValidationError("context pack list could not be safely copied") from None
            if index == MAX_OWNED_CONTAINER_ITEMS:
                raise ValidationError("context pack container exceeds the safe ownership limit")
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
