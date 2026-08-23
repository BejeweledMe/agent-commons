"""Typed role and role-link payloads after schema and domain validation.

The immutable records in this module are one step inside the canonical JSON
boundary. JSON Schema plus ``validate_payload`` establish every input shape
before a parser here is called; serializers preserve the existing payloads
without changing stored event names or bytes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, NotRequired, TypeAlias, TypedDict, cast

from .envelopes import EventEnvelope, FrozenJsonObject, JsonValue, TypedRef, TypedRefPayload


class AgentGrantsPayload(TypedDict):
    create_roles: str
    retire_roles: str
    open_links: str


class AgentLifetimePayload(TypedDict):
    kind: str
    task_id: NotRequired[str]


class AgentChangesPayload(TypedDict):
    name: NotRequired[str]
    grants: NotRequired[AgentGrantsPayload]
    context_mode: NotRequired[str]
    skills: NotRequired[list[str]]
    tool_allowlist: NotRequired[list[str]]
    turnover_budget: NotRequired[int | None]


class IsolationDowngradePayload(TypedDict):
    reason: str
    operator_capability: str


class AgentCreatedPayload(TypedDict):
    agent_id: str
    name: str
    profile_id: str
    grants: AgentGrantsPayload
    context_mode: str
    origin: str
    approval: str
    rationale: str
    lifetime: AgentLifetimePayload
    template: NotRequired[bool]
    created_by_agent_id: NotRequired[str | None]
    turnover_budget: NotRequired[int | None]
    skills: NotRequired[list[str]]
    tool_allowlist: NotRequired[list[str]]
    proposal_ref: NotRequired[TypedRefPayload]
    extensions: NotRequired[dict[str, JsonValue]]


class AgentReconfiguredPayload(TypedDict):
    agent_id: str
    expected_revision: str
    changes: AgentChangesPayload
    reason: str
    isolation_downgrade: NotRequired[IsolationDowngradePayload]
    extensions: NotRequired[dict[str, JsonValue]]


class AgentRetiredPayload(TypedDict):
    agent_id: str
    expected_revision: str
    reason: str
    retired_by: str
    cascade_of: NotRequired[str | None]
    extensions: NotRequired[dict[str, JsonValue]]


class AgentLinkPayload(TypedDict):
    link_id: str
    from_agent_id: NotRequired[str]
    to_agent_id: NotRequired[str]
    allowed_action: NotRequired[str]
    deadline_seconds: NotRequired[int]
    expected_revision: NotRequired[str]
    reason: str
    extensions: NotRequired[dict[str, JsonValue]]


AgentCreatedEventType: TypeAlias = Literal["agent.created"]
AgentReconfiguredEventType: TypeAlias = Literal["agent.reconfigured"]
AgentRetiredEventType: TypeAlias = Literal["agent.retired"]
AgentLinkEventType: TypeAlias = Literal["agent.link_opened", "agent.link_closed"]


@dataclass(frozen=True)
class AgentGrants:
    create_roles: str
    retire_roles: str
    open_links: str

    @classmethod
    def from_payload(cls, value: Mapping[str, object]) -> AgentGrants:
        return cls(
            create_roles=_required_string(value, "create_roles"),
            retire_roles=_required_string(value, "retire_roles"),
            open_links=_required_string(value, "open_links"),
        )

    def to_payload(self) -> AgentGrantsPayload:
        return {
            "create_roles": self.create_roles,
            "retire_roles": self.retire_roles,
            "open_links": self.open_links,
        }


@dataclass(frozen=True)
class AgentLifetime:
    kind: str
    task_id: str | None

    @classmethod
    def from_payload(cls, value: Mapping[str, object]) -> AgentLifetime:
        return cls(
            kind=_required_string(value, "kind"),
            task_id=_optional_string(value, "task_id"),
        )

    def to_payload(self) -> AgentLifetimePayload:
        payload: AgentLifetimePayload = {"kind": self.kind}
        if self.task_id is not None:
            payload["task_id"] = self.task_id
        return payload


@dataclass(frozen=True)
class AgentChanges:
    name: str | None
    grants: AgentGrants | None
    context_mode: str | None
    skills: tuple[str, ...] | None
    tool_allowlist: tuple[str, ...] | None
    has_turnover_budget: bool
    turnover_budget: int | None

    @classmethod
    def from_payload(cls, value: Mapping[str, object]) -> AgentChanges:
        return cls(
            name=_optional_string(value, "name"),
            grants=_optional_grants(value, "grants"),
            context_mode=_optional_string(value, "context_mode"),
            skills=_optional_string_tuple(value, "skills"),
            tool_allowlist=_optional_string_tuple(value, "tool_allowlist"),
            has_turnover_budget="turnover_budget" in value,
            turnover_budget=_optional_nullable_int(value, "turnover_budget"),
        )

    def to_payload(self) -> AgentChangesPayload:
        payload: AgentChangesPayload = {}
        if self.name is not None:
            payload["name"] = self.name
        if self.grants is not None:
            payload["grants"] = self.grants.to_payload()
        if self.context_mode is not None:
            payload["context_mode"] = self.context_mode
        if self.skills is not None:
            payload["skills"] = list(self.skills)
        if self.tool_allowlist is not None:
            payload["tool_allowlist"] = list(self.tool_allowlist)
        if self.has_turnover_budget:
            payload["turnover_budget"] = self.turnover_budget
        return payload


@dataclass(frozen=True)
class IsolationDowngrade:
    reason: str
    operator_capability: str

    @classmethod
    def from_payload(cls, value: Mapping[str, object]) -> IsolationDowngrade:
        return cls(
            reason=_required_string(value, "reason"),
            operator_capability=_required_string(value, "operator_capability"),
        )

    def to_payload(self) -> IsolationDowngradePayload:
        return {
            "reason": self.reason,
            "operator_capability": self.operator_capability,
        }


class AgentEnvelope(EventEnvelope):
    """Base class for the closed role-event family."""

    agent_id: str


@dataclass(frozen=True)
class AgentCreatedEnvelope(AgentEnvelope):
    agent_id: str
    name: str
    profile_id: str
    grants: AgentGrants
    context_mode: str
    origin: str
    approval: str
    rationale: str
    lifetime: AgentLifetime
    template: bool | None
    has_created_by_agent_id: bool
    created_by_agent_id: str | None
    has_turnover_budget: bool
    turnover_budget: int | None
    skills: tuple[str, ...] | None
    tool_allowlist: tuple[str, ...] | None
    proposal_ref: TypedRef | None
    extensions: FrozenJsonObject | None
    event_type: AgentCreatedEventType = "agent.created"

    def to_payload(self) -> AgentCreatedPayload:
        from .envelopes import thaw_json_object

        payload: AgentCreatedPayload = {
            "agent_id": self.agent_id,
            "name": self.name,
            "profile_id": self.profile_id,
            "grants": self.grants.to_payload(),
            "context_mode": self.context_mode,
            "origin": self.origin,
            "approval": self.approval,
            "rationale": self.rationale,
            "lifetime": self.lifetime.to_payload(),
        }
        if self.template is not None:
            payload["template"] = self.template
        if self.has_created_by_agent_id:
            payload["created_by_agent_id"] = self.created_by_agent_id
        if self.has_turnover_budget:
            payload["turnover_budget"] = self.turnover_budget
        if self.skills is not None:
            payload["skills"] = list(self.skills)
        if self.tool_allowlist is not None:
            payload["tool_allowlist"] = list(self.tool_allowlist)
        if self.proposal_ref is not None:
            payload["proposal_ref"] = self.proposal_ref.to_payload()
        if self.extensions is not None:
            payload["extensions"] = thaw_json_object(self.extensions)
        return payload


@dataclass(frozen=True)
class AgentReconfiguredEnvelope(AgentEnvelope):
    agent_id: str
    expected_revision: str
    changes: AgentChanges
    reason: str
    isolation_downgrade: IsolationDowngrade | None
    extensions: FrozenJsonObject | None
    event_type: AgentReconfiguredEventType = "agent.reconfigured"

    def to_payload(self) -> AgentReconfiguredPayload:
        from .envelopes import thaw_json_object

        payload: AgentReconfiguredPayload = {
            "agent_id": self.agent_id,
            "expected_revision": self.expected_revision,
            "changes": self.changes.to_payload(),
            "reason": self.reason,
        }
        if self.isolation_downgrade is not None:
            payload["isolation_downgrade"] = self.isolation_downgrade.to_payload()
        if self.extensions is not None:
            payload["extensions"] = thaw_json_object(self.extensions)
        return payload


@dataclass(frozen=True)
class AgentRetiredEnvelope(AgentEnvelope):
    agent_id: str
    expected_revision: str
    reason: str
    retired_by: str
    has_cascade_of: bool
    cascade_of: str | None
    extensions: FrozenJsonObject | None
    event_type: AgentRetiredEventType = "agent.retired"

    def to_payload(self) -> AgentRetiredPayload:
        from .envelopes import thaw_json_object

        payload: AgentRetiredPayload = {
            "agent_id": self.agent_id,
            "expected_revision": self.expected_revision,
            "reason": self.reason,
            "retired_by": self.retired_by,
        }
        if self.has_cascade_of:
            payload["cascade_of"] = self.cascade_of
        if self.extensions is not None:
            payload["extensions"] = thaw_json_object(self.extensions)
        return payload


@dataclass(frozen=True)
class AgentLinkEnvelope(EventEnvelope):
    event_type: AgentLinkEventType
    link_id: str
    from_agent_id: str | None
    to_agent_id: str | None
    allowed_action: str | None
    deadline_seconds: int | None
    expected_revision: str | None
    reason: str
    extensions: FrozenJsonObject | None

    def to_payload(self) -> AgentLinkPayload:
        from .envelopes import thaw_json_object

        payload: AgentLinkPayload = {"link_id": self.link_id, "reason": self.reason}
        if self.from_agent_id is not None:
            payload["from_agent_id"] = self.from_agent_id
        if self.to_agent_id is not None:
            payload["to_agent_id"] = self.to_agent_id
        if self.allowed_action is not None:
            payload["allowed_action"] = self.allowed_action
        if self.deadline_seconds is not None:
            payload["deadline_seconds"] = self.deadline_seconds
        if self.expected_revision is not None:
            payload["expected_revision"] = self.expected_revision
        if self.extensions is not None:
            payload["extensions"] = thaw_json_object(self.extensions)
        return payload


AgentRoleEnvelope: TypeAlias = AgentEnvelope | AgentLinkEnvelope


def parse_agent_role_envelope(
    event_type: str, payload: Mapping[str, object]
) -> AgentRoleEnvelope | None:
    """Parse a role or role-link payload that has already passed validation."""

    if event_type == "agent.created":
        return AgentCreatedEnvelope(
            agent_id=_required_string(payload, "agent_id"),
            name=_required_string(payload, "name"),
            profile_id=_required_string(payload, "profile_id"),
            grants=AgentGrants.from_payload(_required_mapping(payload, "grants")),
            context_mode=_required_string(payload, "context_mode"),
            origin=_required_string(payload, "origin"),
            approval=_required_string(payload, "approval"),
            rationale=_required_string(payload, "rationale"),
            lifetime=AgentLifetime.from_payload(_required_mapping(payload, "lifetime")),
            template=_optional_bool(payload, "template"),
            has_created_by_agent_id="created_by_agent_id" in payload,
            created_by_agent_id=_optional_string(payload, "created_by_agent_id"),
            has_turnover_budget="turnover_budget" in payload,
            turnover_budget=_optional_nullable_int(payload, "turnover_budget"),
            skills=_optional_string_tuple(payload, "skills"),
            tool_allowlist=_optional_string_tuple(payload, "tool_allowlist"),
            proposal_ref=_optional_ref(payload, "proposal_ref"),
            extensions=_optional_frozen_object(payload, "extensions"),
        )
    if event_type == "agent.reconfigured":
        return AgentReconfiguredEnvelope(
            agent_id=_required_string(payload, "agent_id"),
            expected_revision=_required_string(payload, "expected_revision"),
            changes=AgentChanges.from_payload(_required_mapping(payload, "changes")),
            reason=_required_string(payload, "reason"),
            isolation_downgrade=_optional_isolation_downgrade(payload),
            extensions=_optional_frozen_object(payload, "extensions"),
        )
    if event_type == "agent.retired":
        return AgentRetiredEnvelope(
            agent_id=_required_string(payload, "agent_id"),
            expected_revision=_required_string(payload, "expected_revision"),
            reason=_required_string(payload, "reason"),
            retired_by=_required_string(payload, "retired_by"),
            has_cascade_of="cascade_of" in payload,
            cascade_of=_optional_string(payload, "cascade_of"),
            extensions=_optional_frozen_object(payload, "extensions"),
        )
    if event_type in {"agent.link_opened", "agent.link_closed"}:
        return AgentLinkEnvelope(
            event_type=cast(AgentLinkEventType, event_type),
            link_id=_required_string(payload, "link_id"),
            from_agent_id=_optional_string(payload, "from_agent_id"),
            to_agent_id=_optional_string(payload, "to_agent_id"),
            allowed_action=_optional_string(payload, "allowed_action"),
            deadline_seconds=_optional_int(payload, "deadline_seconds"),
            expected_revision=_optional_string(payload, "expected_revision"),
            reason=_required_string(payload, "reason"),
            extensions=_optional_frozen_object(payload, "extensions"),
        )
    return None


def _required_string(payload: Mapping[str, object], field: str) -> str:
    return cast(str, payload[field])


def _optional_string(payload: Mapping[str, object], field: str) -> str | None:
    return cast(str | None, payload.get(field))


def _optional_nullable_int(payload: Mapping[str, object], field: str) -> int | None:
    return cast(int | None, payload.get(field))


def _optional_bool(payload: Mapping[str, object], field: str) -> bool | None:
    return cast(bool | None, payload.get(field))


def _optional_int(payload: Mapping[str, object], field: str) -> int | None:
    return cast(int | None, payload.get(field))


def _required_mapping(payload: Mapping[str, object], field: str) -> Mapping[str, object]:
    return cast(Mapping[str, object], payload[field])


def _optional_mapping(payload: Mapping[str, object], field: str) -> Mapping[str, object] | None:
    if field not in payload:
        return None
    return _required_mapping(payload, field)


def _optional_grants(payload: Mapping[str, object], field: str) -> AgentGrants | None:
    value = _optional_mapping(payload, field)
    return AgentGrants.from_payload(value) if value is not None else None


def _optional_ref(payload: Mapping[str, object], field: str) -> TypedRef | None:
    value = _optional_mapping(payload, field)
    return TypedRef.from_payload(value) if value is not None else None


def _optional_string_tuple(payload: Mapping[str, object], field: str) -> tuple[str, ...] | None:
    if field not in payload:
        return None
    return tuple(cast(list[str], payload[field]))


def _optional_isolation_downgrade(
    payload: Mapping[str, object],
) -> IsolationDowngrade | None:
    value = _optional_mapping(payload, "isolation_downgrade")
    return IsolationDowngrade.from_payload(value) if value is not None else None


def _optional_frozen_object(payload: Mapping[str, object], field: str) -> FrozenJsonObject | None:
    if field not in payload:
        return None
    from .envelopes import freeze_json_object

    return freeze_json_object(_required_mapping(payload, field))
