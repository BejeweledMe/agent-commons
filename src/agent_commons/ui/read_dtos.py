"""Typed, wire-compatible DTOs for local UI read models.

The local panel still receives the established JSON objects.  These immutable
records make the in-memory UI boundary explicit without changing HTTP routes,
persisted events, or their serialized shapes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, TypeAlias, TypedDict, cast

from agent_commons.core.bounded import bounded_copy
from agent_commons.domain.envelopes import (
    FrozenJsonArray,
    FrozenJsonObject,
    FrozenJsonValue,
    JsonValue,
)

JsonValueInput: TypeAlias = JsonValue | FrozenJsonValue


# The Work application must never render a runtime loader error, executable
# resolver detail, or operator-file location.  Keep this vocabulary closed at
# the Python boundary: adding a displayable setup condition is an intentional
# UI-contract change, rather than an accidental pass-through of a new error.
SetupGuidanceBlockerCode: TypeAlias = Literal[
    "setup_not_a_repository",
    "setup_uninitialized",
    "setup_unconfigured",
    "setup_no_provider_found",
    "setup_support_binary_unresolved",
    "setup_config_rejected_by_loader",
]
SetupGuidanceTool: TypeAlias = Literal["Claude", "Codex", "git", "agent-commons-mcp"]
SetupGuidanceNextActionKey: TypeAlias = Literal[
    "choose_git_repository",
    "initialize_workspace",
    "install_provider_and_check_again",
    "install_support_tool_and_check_again",
    "configure_runtime",
    "repair_workspace_configuration",
    "setup_ready",
]
SetupGuidanceLocationLabel: TypeAlias = Literal["workspace_configuration"]

ProviderAuthStateKey: TypeAlias = Literal[
    "ready",
    "authentication_required",
    "authenticating",
    "timed_out",
    "cancelled",
    "failed",
    "unsupported",
    "credential_store_unavailable",
]
ProviderAuthOperationKey: TypeAlias = Literal["status", "login"]
ProviderAuthActionKey: TypeAlias = Literal[
    "authenticate",
    "cancel_authentication",
    "check_again",
    "continue_launch",
]
ProviderAuthFreshness: TypeAlias = Literal["fresh", "stale"]


class LaunchContextPackPayload(TypedDict):
    """One exact current Context Pack revision selectable by Work."""

    context_pack_id: str
    revision: str
    summary: str
    fact_count: int
    open_question_count: int


class LaunchContextPackOptionsStatusPayload(TypedDict):
    """Freshness and fail-closed completeness state for the bounded option set."""

    freshness: Literal["current"]
    truncated: bool
    refusal: Literal["context_pack_options_truncated"] | None


@dataclass(frozen=True, slots=True)
class LaunchContextPackDTO:
    """Bounded, content-minimal Context Pack identity for a launch form.

    Facts, sources, decisions, compiled text, transcripts, and provider data
    deliberately stay behind the runtime boundary.  The summary is already
    bounded by the canonical Context Pack schema and is copied into a plain
    string here so projection containers never cross into the browser DTO.
    """

    context_pack_id: str
    revision: str
    summary: str
    fact_count: int
    open_question_count: int

    def to_wire(self) -> LaunchContextPackPayload:
        return {
            "context_pack_id": self.context_pack_id,
            "revision": self.revision,
            "summary": self.summary,
            "fact_count": self.fact_count,
            "open_question_count": self.open_question_count,
        }


class ProviderAuthPayload(TypedDict):
    """The complete secret-free provider availability contract for Work."""

    profile_id: str
    provider: Literal["claude", "codex"]
    operation: ProviderAuthOperationKey
    state: ProviderAuthStateKey
    supported: bool
    blocks_launch: bool
    checked_at: str
    freshness: ProviderAuthFreshness
    fresh_for_seconds: int
    action_ids: list[ProviderAuthActionKey]
    post_start_recovery: Literal["new_run_only"]


@dataclass(frozen=True, slots=True)
class ProviderAuthDTO:
    """Closed, maintainer-authored auth state safe for browser persistence.

    This record intentionally has no provider diagnostic text.  The Work app
    renders every state and action from its paired EN/RU table, so raw process
    output, OAuth material, account identifiers, and host paths cannot cross
    this typed boundary by accident.
    """

    profile_id: str
    provider: Literal["claude", "codex"]
    operation: ProviderAuthOperationKey
    state: ProviderAuthStateKey
    supported: bool
    blocks_launch: bool
    checked_at: str
    freshness: ProviderAuthFreshness
    fresh_for_seconds: int
    action_ids: tuple[ProviderAuthActionKey, ...]

    def to_wire(self) -> ProviderAuthPayload:
        return {
            "profile_id": self.profile_id,
            "provider": self.provider,
            "operation": self.operation,
            "state": self.state,
            "supported": self.supported,
            "blocks_launch": self.blocks_launch,
            "checked_at": self.checked_at,
            "freshness": self.freshness,
            "fresh_for_seconds": self.fresh_for_seconds,
            "action_ids": list(self.action_ids),
            # A provider process that already started is never resumed by this
            # surface.  Recovery is a fresh, separately bounded run.
            "post_start_recovery": "new_run_only",
        }


class SetupGuidancePayload(TypedDict):
    """The complete, redacted wire contract for Work setup guidance."""

    blocker_code: SetupGuidanceBlockerCode | None
    tools: list[SetupGuidanceTool]
    next_action_key: SetupGuidanceNextActionKey
    location_label: SetupGuidanceLocationLabel | None


@dataclass(frozen=True, slots=True)
class SetupGuidanceDTO:
    """Closed-vocabulary setup explanation safe to return to Work.

    This deliberately cannot carry a path, a loader/refusal message, config
    text, provider output, or an arbitrary diagnostic string.  The optional
    generic location label is supplied only after the browser explicitly asks
    to reveal it; it still never identifies a filesystem location.
    """

    blocker_code: SetupGuidanceBlockerCode | None
    tools: tuple[SetupGuidanceTool, ...]
    next_action_key: SetupGuidanceNextActionKey
    location_label: SetupGuidanceLocationLabel | None = None

    def to_wire(self) -> SetupGuidancePayload:
        return {
            "blocker_code": self.blocker_code,
            "tools": list(self.tools),
            "next_action_key": self.next_action_key,
            "location_label": self.location_label,
        }


def _freeze_json(value: JsonValueInput) -> FrozenJsonValue:
    """Recursively own a JSON value held by an immutable UI DTO."""

    if isinstance(value, (FrozenJsonArray, FrozenJsonObject)):
        return value
    if isinstance(value, Mapping):
        return FrozenJsonObject(tuple((key, _freeze_json(child)) for key, child in value.items()))
    if isinstance(value, list):
        return FrozenJsonArray(tuple(_freeze_json(child) for child in value))
    return value


def _thaw_json(value: FrozenJsonValue) -> JsonValue:
    """Return a fresh standard JSON container from a DTO-owned value."""

    if isinstance(value, FrozenJsonObject):
        return {key: _thaw_json(child) for key, child in value.values}
    if isinstance(value, FrozenJsonArray):
        return [_thaw_json(child) for child in value.values]
    return value


class RunBlockedAttentionPayload(TypedDict):
    kind: Literal["run_blocked"]
    id: str
    agent_id: JsonValue
    target_ref: JsonValue
    run_state: JsonValue
    reason_code: JsonValue
    summary: JsonValue
    operation_id: JsonValue
    metadata: JsonValue
    answerable_here: bool
    answer_from_session: list[str]
    deadline: JsonValue


class WorkReturnedAttentionPayload(TypedDict):
    kind: Literal["work_returned"]
    id: str
    task_id: str
    title: JsonValue
    task_state: JsonValue
    task_revision: str
    delegation_id: str
    agent_id: JsonValue
    agent_name: JsonValue


class ThreadAttentionPayload(TypedDict):
    kind: Literal["thread"]
    id: str
    thread_type: str
    subject: JsonValue
    revision: JsonValue
    proposal: None


class ProposalAttentionPayload(TypedDict):
    kind: Literal["proposal"]
    id: str
    thread_type: str
    subject: JsonValue
    revision: JsonValue
    proposal: dict[str, JsonValue]


class ConfigBrokenAttentionPayload(TypedDict):
    kind: Literal["config_broken"]
    id: str
    agent_id: str
    name: JsonValue
    missing_skills: list[str]


AttentionItemPayload: TypeAlias = (
    RunBlockedAttentionPayload
    | WorkReturnedAttentionPayload
    | ThreadAttentionPayload
    | ProposalAttentionPayload
    | ConfigBrokenAttentionPayload
)


class AttentionResponsePayload(TypedDict):
    items: list[AttentionItemPayload]
    count: int
    writes_enabled: bool


@dataclass(frozen=True, slots=True)
class RunBlockedAttention:
    identifier: str
    agent_id: JsonValueInput
    target_ref: JsonValueInput
    run_state: JsonValueInput
    reason_code: JsonValueInput
    summary: JsonValueInput
    operation_id: JsonValueInput
    metadata: JsonValueInput
    answerable_here: bool
    answer_from_session: tuple[str, ...]
    deadline: JsonValueInput

    def __post_init__(self) -> None:
        for field in (
            "agent_id",
            "target_ref",
            "run_state",
            "reason_code",
            "summary",
            "operation_id",
            "metadata",
            "deadline",
        ):
            object.__setattr__(self, field, _freeze_json(getattr(self, field)))

    def to_wire(self) -> RunBlockedAttentionPayload:
        return {
            "kind": "run_blocked",
            "id": self.identifier,
            "agent_id": _thaw_json(cast(FrozenJsonValue, self.agent_id)),
            "target_ref": _thaw_json(cast(FrozenJsonValue, self.target_ref)),
            "run_state": _thaw_json(cast(FrozenJsonValue, self.run_state)),
            "reason_code": _thaw_json(cast(FrozenJsonValue, self.reason_code)),
            "summary": _thaw_json(cast(FrozenJsonValue, self.summary)),
            "operation_id": _thaw_json(cast(FrozenJsonValue, self.operation_id)),
            "metadata": _thaw_json(cast(FrozenJsonValue, self.metadata)),
            "answerable_here": self.answerable_here,
            "answer_from_session": list(self.answer_from_session),
            "deadline": _thaw_json(cast(FrozenJsonValue, self.deadline)),
        }


@dataclass(frozen=True, slots=True)
class WorkReturnedAttention:
    task_id: str
    title: JsonValueInput
    task_state: JsonValueInput
    task_revision: str
    delegation_id: str
    agent_id: JsonValueInput
    agent_name: JsonValueInput

    def __post_init__(self) -> None:
        for field in ("title", "task_state", "agent_id", "agent_name"):
            object.__setattr__(self, field, _freeze_json(getattr(self, field)))

    def to_wire(self) -> WorkReturnedAttentionPayload:
        return {
            "kind": "work_returned",
            "id": self.task_id,
            "task_id": self.task_id,
            "title": _thaw_json(cast(FrozenJsonValue, self.title)),
            "task_state": _thaw_json(cast(FrozenJsonValue, self.task_state)),
            "task_revision": self.task_revision,
            "delegation_id": self.delegation_id,
            "agent_id": _thaw_json(cast(FrozenJsonValue, self.agent_id)),
            "agent_name": _thaw_json(cast(FrozenJsonValue, self.agent_name)),
        }


@dataclass(frozen=True, slots=True)
class ThreadAttention:
    identifier: str
    thread_type: str
    subject: JsonValueInput
    revision: JsonValueInput

    def __post_init__(self) -> None:
        object.__setattr__(self, "subject", _freeze_json(self.subject))
        object.__setattr__(self, "revision", _freeze_json(self.revision))

    def to_wire(self) -> ThreadAttentionPayload:
        return {
            "kind": "thread",
            "id": self.identifier,
            "thread_type": self.thread_type,
            "subject": _thaw_json(cast(FrozenJsonValue, self.subject)),
            "revision": _thaw_json(cast(FrozenJsonValue, self.revision)),
            "proposal": None,
        }


@dataclass(frozen=True, slots=True)
class ProposalAttention:
    identifier: str
    thread_type: str
    subject: JsonValueInput
    revision: JsonValueInput
    proposal: JsonValueInput

    def __post_init__(self) -> None:
        for field in ("subject", "revision", "proposal"):
            object.__setattr__(self, field, _freeze_json(getattr(self, field)))

    def to_wire(self) -> ProposalAttentionPayload:
        return {
            "kind": "proposal",
            "id": self.identifier,
            "thread_type": self.thread_type,
            "subject": _thaw_json(cast(FrozenJsonValue, self.subject)),
            "revision": _thaw_json(cast(FrozenJsonValue, self.revision)),
            "proposal": cast(
                dict[str, JsonValue], _thaw_json(cast(FrozenJsonValue, self.proposal))
            ),
        }


@dataclass(frozen=True, slots=True)
class ConfigBrokenAttention:
    agent_id: str
    name: JsonValueInput
    missing_skills: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _freeze_json(self.name))

    def to_wire(self) -> ConfigBrokenAttentionPayload:
        return {
            "kind": "config_broken",
            "id": self.agent_id,
            "agent_id": self.agent_id,
            "name": _thaw_json(cast(FrozenJsonValue, self.name)),
            "missing_skills": list(self.missing_skills),
        }


AttentionItem: TypeAlias = (
    RunBlockedAttention
    | WorkReturnedAttention
    | ThreadAttention
    | ProposalAttention
    | ConfigBrokenAttention
)


@dataclass(frozen=True, slots=True)
class AttentionResponse:
    """The immutable panel response before its established display bounding."""

    items: tuple[AttentionItem, ...]
    writes_enabled: bool

    def to_wire(self) -> AttentionResponsePayload:
        """Serialize the pre-existing attention JSON shape, including item bounds."""

        serialized = [item.to_wire() for item in self.items]
        return {
            "items": [cast(AttentionItemPayload, bounded_copy(item)) for item in serialized],
            "count": len(serialized),
            "writes_enabled": self.writes_enabled,
        }
