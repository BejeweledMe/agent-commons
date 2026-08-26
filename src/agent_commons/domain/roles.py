"""Standing roles: grant algebra, lineage, and the principals behind a session.

A role is persistent; a delegation is one bounded run.  Everything an agent is
*allowed* to do is derived here from the immutable ledger rather than stored on
the record, because a stored copy needs a propagation pass and a propagation
pass can be skipped.  Deriving it makes an authority downgrade take effect on
the next call, including for work that is already running.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from agent_commons.core.ids import is_typed_id
from agent_commons.domain.states import NON_TERMINAL_DELEGATION_STATES
from agent_commons.errors import ValidationError

#: Ordered because the whole model is "narrower or equal, never wider".
GRANT_LEVELS: dict[str, int] = {"deny": 0, "ask": 1, "auto": 2}

#: The automatic level is withheld: the 2026-08-10 review defeated the
#: guarantees ADR 0009 claims for it (C1, C2, H1, H2 in
#: docs/audits/2026-08-10-standing-roles-review.md), and the original brief's
#: rule applies -- do not ship the automatic level partially, because an inert
#: brake is worse than an absent one.  A stored ``auto`` stays valid in the
#: ledger but is *effective* at ``ask``: every automatic action asks a person
#: instead.  Lifting this requires the defeat paths to be closed and covered by
#: tests that enter through the same seams a user does.
AUTOMATIC_LEVEL_WITHHELD = True

GRANT_NAMES = ("create_roles", "retire_roles", "open_links")

DENY_ALL: dict[str, str] = dict.fromkeys(GRANT_NAMES, "deny")

#: Which profiles a role may hand to a role it creates.  A builder may create a
#: builder or the strictly weaker reviewer of the same provider; a reviewer may
#: only create reviewers.  Cross-provider profiles are incomparable, so neither
#: can be reached from the other.
PROFILE_NARROWING: dict[str, frozenset[str]] = {
    "codex-builder": frozenset({"codex-builder", "codex-independent-reviewer"}),
    "codex-independent-reviewer": frozenset({"codex-independent-reviewer"}),
    "claude-builder": frozenset({"claude-builder", "claude-independent-reviewer"}),
    "claude-independent-reviewer": frozenset({"claude-independent-reviewer"}),
}

#: Isolation is ordered too: `fresh` is stronger, and moving down it is the only
#: reconfiguration that needs an explicit operator acknowledgement.
CONTEXT_MODES: dict[str, int] = {"accumulated": 0, "fresh": 1}

#: Guards against a corrupted lineage turning a walk into a hang.  Deeper than
#: any reachable chain: automatic creation terminates after two generations.
_MAX_LINEAGE = 64

__all__ = (
    "AUTOMATIC_LEVEL_WITHHELD",
    "CONTEXT_MODES",
    "DENY_ALL",
    "GRANT_LEVELS",
    "GRANT_NAMES",
    "NON_TERMINAL_DELEGATION_STATES",
    "PROFILE_NARROWING",
    "agent_delegations",
    "descendants",
    "effective_grants",
    "grant_level",
    "lineage",
    "principals",
    "prior_verdicts",
    "retirement_blockers",
    "session_agent_map",
    "stored_grants",
    "turnover_blockers",
    "turnover_used",
)


@dataclass(frozen=True)
class RolePayloadValidators:
    """Generic payload checks used by the role-specific validation dispatcher."""

    validate_ref: Callable[[Any, str], None]
    validate_string_list: Callable[[Any, str], None]


_ROLE_GRANT_LEVELS = {"deny", "ask", "auto"}
_ROLE_TARGET_PROFILES = {
    "codex-builder",
    "codex-independent-reviewer",
    "claude-builder",
    "claude-independent-reviewer",
}
_ROLE_CONTEXT_MODES = {"fresh", "accumulated"}
_ROLE_ORIGINS = {"human", "agent"}
_ROLE_AUTHORIZATIONS = {"human", "human_confirmed", "automatic"}
_ROLE_RETIRED_BY = {"human", "agent", "cascade"}
#: What a temporary link permits.  A typed action rather than an open/closed
#: flag, so adding one extends this set instead of reshaping the record.
_ROLE_LINK_ACTIONS = {"ask", "handoff_work"}
_ROLE_MUTABLE_FIELDS = {
    "name",
    "grants",
    "context_mode",
    "skills",
    "tool_allowlist",
    # Mutable so the operator remedy for a widened grant exists at all: a role
    # reconfigured to create or retire roles needs a ceiling, and a role that
    # dropped those grants can shed one.  The lifecycle keeps it monotone
    # against the creator, exactly as creation does.
    "turnover_budget",
}


def validate_role_payload(
    event_type: str,
    payload: Mapping[str, Any],
    *,
    validators: RolePayloadValidators,
) -> None:
    """Validate the raw payload rules owned by the standing-role domain."""

    if event_type == "agent.created":
        _validate_agent_created(payload, validators=validators)
    if event_type == "agent.reconfigured":
        _validate_agent_reconfigured(payload, validators=validators)
    if event_type == "agent.retired":
        if payload["retired_by"] not in _ROLE_RETIRED_BY:
            raise ValidationError("retired_by must be human, agent, or cascade")
        cascade_of = payload.get("cascade_of")
        if cascade_of is not None and not is_typed_id(cascade_of, "agent"):
            raise ValidationError("cascade_of must be an agent identifier or null")
    if event_type == "agent.link_opened":
        if payload["allowed_action"] not in _ROLE_LINK_ACTIONS:
            raise ValidationError("invalid link allowed_action")
        if payload["from_agent_id"] == payload["to_agent_id"]:
            raise ValidationError("a link requires two distinct roles")
        # Still bounded when supplied -- history carries it, and an operator may
        # record an intended horizon -- but never required and never enforced.
        if payload.get("deadline_seconds") is not None:
            _validate_role_bounded_integer(
                payload["deadline_seconds"], "deadline_seconds", minimum=1, maximum=604_800
            )


def _validate_agent_grants(value: Any, field: str) -> None:
    if not isinstance(value, Mapping) or set(value) != set(GRANT_NAMES):
        raise ValidationError(f"{field} must contain exactly {', '.join(GRANT_NAMES)}")
    for name in GRANT_NAMES:
        if value[name] not in _ROLE_GRANT_LEVELS:
            raise ValidationError(f"{field}.{name} must be deny, ask, or auto")


def _validate_agent_lifetime(value: Any) -> None:
    if not isinstance(value, Mapping) or "kind" not in value:
        raise ValidationError("lifetime must be an object with a kind")
    kind = value["kind"]
    if kind == "persistent":
        if set(value) != {"kind"}:
            raise ValidationError("a persistent lifetime carries no other field")
        return
    if kind != "task_scoped":
        raise ValidationError("lifetime.kind must be persistent or task_scoped")
    if set(value) != {"kind", "task_id"} or not is_typed_id(value.get("task_id"), "task"):
        raise ValidationError("a task_scoped lifetime requires exactly a task_id")


def _validate_agent_created(
    payload: Mapping[str, Any], *, validators: RolePayloadValidators
) -> None:
    _validate_agent_grants(payload["grants"], "grants")
    _validate_agent_lifetime(payload["lifetime"])
    if payload["profile_id"] not in _ROLE_TARGET_PROFILES:
        raise ValidationError("invalid agent profile_id")
    if payload["context_mode"] not in _ROLE_CONTEXT_MODES:
        raise ValidationError("context_mode must be fresh or accumulated")
    if payload["origin"] not in _ROLE_ORIGINS:
        raise ValidationError("origin must be human or agent")
    if payload["approval"] not in _ROLE_AUTHORIZATIONS:
        raise ValidationError("approval must be human, human_confirmed, or automatic")
    creator = payload.get("created_by_agent_id")
    if payload["origin"] == "agent":
        if not is_typed_id(creator, "agent"):
            raise ValidationError("an agent-created role must name its creating role")
        if payload["approval"] == "human":
            raise ValidationError(
                "an agent-created role is authorized automatically or human_confirmed"
            )
        # A human-confirmed creation must point at the proposal it confirms.
        # Without that binding, `created_by_agent_id` is a free-text claim and
        # "role X asked for this" cannot be checked six months later.
        if payload["approval"] == "human_confirmed":
            proposal = payload.get("proposal_ref")
            if not isinstance(proposal, Mapping) or proposal.get("kind") != "thread":
                raise ValidationError(
                    "a human-confirmed role must bind the proposal thread it confirms"
                )
            validators.validate_ref(proposal, "proposal_ref")
    else:
        if creator is not None:
            raise ValidationError("a human-created role has no creating role")
        if payload["approval"] != "human":
            raise ValidationError("a human-created role records human approval")
        if payload.get("proposal_ref") is not None:
            raise ValidationError("a human-created role confirms no proposal")
    budget = payload.get("turnover_budget")
    needs_budget = any(
        payload["grants"][name] != "deny" for name in ("create_roles", "retire_roles")
    )
    if needs_budget and (isinstance(budget, bool) or not isinstance(budget, int)):
        raise ValidationError(
            "a role that may create or retire roles requires an integer turnover_budget"
        )
    if budget is not None and (isinstance(budget, bool) or not isinstance(budget, int)):
        raise ValidationError("turnover_budget must be an integer or null")
    if "template" in payload and not isinstance(payload["template"], bool):
        raise ValidationError("template must be a boolean")


def _validate_agent_reconfigured(
    payload: Mapping[str, Any], *, validators: RolePayloadValidators
) -> None:
    changes = payload["changes"]
    if not isinstance(changes, Mapping) or not changes:
        raise ValidationError("changes must be a non-empty object")
    unsupported = sorted(set(changes) - _ROLE_MUTABLE_FIELDS)
    if unsupported:
        raise ValidationError("changes contains immutable agent fields: " + ", ".join(unsupported))
    if "grants" in changes:
        _validate_agent_grants(changes["grants"], "changes.grants")
    if "context_mode" in changes and changes["context_mode"] not in _ROLE_CONTEXT_MODES:
        raise ValidationError("changes.context_mode must be fresh or accumulated")
    if "name" in changes and (not isinstance(changes["name"], str) or not changes["name"].strip()):
        raise ValidationError("changes.name must be a non-empty string")
    if "turnover_budget" in changes:
        budget = changes["turnover_budget"]
        if budget is not None and (isinstance(budget, bool) or not isinstance(budget, int)):
            raise ValidationError("changes.turnover_budget must be an integer or null")
    for field in ("skills", "tool_allowlist"):
        if field in changes:
            validators.validate_string_list(changes[field], f"changes.{field}")
    downgrade = payload.get("isolation_downgrade")
    if downgrade is not None:
        if not isinstance(downgrade, Mapping) or set(downgrade) != {
            "reason",
            "operator_capability",
        }:
            raise ValidationError(
                "isolation_downgrade must contain exactly reason and operator_capability"
            )
        if downgrade["operator_capability"] != "agent:isolation_downgrade":
            raise ValidationError("isolation_downgrade names the agent:isolation_downgrade gate")
        if not isinstance(downgrade["reason"], str) or not downgrade["reason"].strip():
            raise ValidationError("isolation_downgrade.reason must be a non-empty string")


def _validate_role_bounded_integer(value: Any, field: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{field} must be an integer")
    if not minimum <= value <= maximum:
        raise ValidationError(f"{field} must be between {minimum} and {maximum}")
    return value


def grant_level(value: object) -> int:
    return GRANT_LEVELS.get(str(value), 0)


def stored_grants(record: Mapping[str, Any] | None) -> dict[str, str]:
    grants = (record or {}).get("grants")
    if not isinstance(grants, Mapping):
        return dict(DENY_ALL)
    return {name: str(grants.get(name, "deny")) for name in GRANT_NAMES}


def lineage(
    agents: Mapping[str, Mapping[str, Any]], agent_id: str
) -> tuple[Mapping[str, Any], ...]:
    """The role and its creators, nearest first.  A cycle stops the walk."""

    chain: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    current = agent_id
    while current and current not in seen and len(chain) < _MAX_LINEAGE:
        record = agents.get(current)
        if record is None:
            break
        seen.add(current)
        chain.append(record)
        created_by = record.get("created_by_agent_id")
        current = str(created_by) if created_by else ""
    return tuple(chain)


def effective_grants(agents: Mapping[str, Mapping[str, Any]], agent_id: str) -> dict[str, str]:
    """The narrowest grant across the role and every creator above it.

    A retired ancestor collapses the whole line to ``deny``: that is what makes
    a cascade retire final even if one of its writes did not land.
    """

    chain = lineage(agents, agent_id)
    if not chain:
        return dict(DENY_ALL)
    ceiling = GRANT_LEVELS["ask"] if AUTOMATIC_LEVEL_WITHHELD else GRANT_LEVELS["auto"]
    effective = {name: ceiling for name in GRANT_NAMES}
    for record in chain:
        if record.get("state") != "active":
            return dict(DENY_ALL)
        stored = stored_grants(record)
        for name in GRANT_NAMES:
            effective[name] = min(effective[name], grant_level(stored[name]))
    inverse = {value: key for key, value in GRANT_LEVELS.items()}
    return {name: inverse[value] for name, value in effective.items()}


def descendants(agents: Mapping[str, Mapping[str, Any]], agent_id: str) -> tuple[str, ...]:
    """Every role created below this one, transitively, in stable order."""

    children: dict[str, list[str]] = {}
    for identifier, record in agents.items():
        parent = record.get("created_by_agent_id")
        if parent:
            children.setdefault(str(parent), []).append(identifier)
    found: list[str] = []
    seen = {agent_id}
    frontier = [agent_id]
    while frontier:
        current = frontier.pop()
        for child in sorted(children.get(current, ())):
            if child in seen:
                continue
            seen.add(child)
            found.append(child)
            frontier.append(child)
    return tuple(sorted(found))


def turnover_used(agents: Mapping[str, Mapping[str, Any]], agent_id: str) -> int:
    """Creations *and* explicit retirements below a role, counted together.

    Counted apart, a create/retire cycle walks past any ceiling on headcount
    while every individual step stays inside it.  A lifetime expiry is not
    counted: it is declared at creation and cannot be used to churn.
    """

    total = 0
    for identifier in descendants(agents, agent_id):
        record = agents[identifier]
        total += 1
        if record.get("state") == "retired" and record.get("retired_by") != "lifetime":
            total += 1
    return total


def turnover_blockers(
    agents: Mapping[str, Mapping[str, Any]], creator_id: str, *, cost: int = 1
) -> list[str]:
    """Ancestors whose turnover budget this operation would exceed."""

    blocked: list[str] = []
    for record in lineage(agents, creator_id):
        budget = record.get("turnover_budget")
        if not isinstance(budget, int) or isinstance(budget, bool):
            continue
        if turnover_used(agents, str(record["id"])) + cost > budget:
            blocked.append(str(record["id"]))
    return blocked


def session_agent_map(
    delegations: Mapping[str, Mapping[str, Any]],
) -> dict[str, frozenset[str]]:
    """Which roles each delegated child session has run as.

    Built over every delegation, terminal ones included: the work whose
    independence is in question was authored during a run that has long since
    ended.  A session can appear under more than one role across runs, so this
    collects all of them -- keeping only the most recent would let the earlier
    role review its own work.
    """

    bindings: dict[str, set[str]] = {}
    for record in delegations.values():
        agent_id = record.get("agent_id")
        session_id = record.get("child_session_id")
        if agent_id and session_id:
            bindings.setdefault(str(session_id), set()).add(str(agent_id))
    return {session_id: frozenset(agents) for session_id, agents in bindings.items()}


def principals(bindings: Mapping[str, frozenset[str]], session_ids: Iterable[str]) -> set[str]:
    """Who a set of sessions really is.

    Independence is a property of the judge, not of the process that happened to
    run it.  Comparing sessions lets one standing role author work in one run and
    approve it in the next; comparing principals does not.  New principal kinds
    belong here, so a call site never has to learn about them.
    """

    resolved: set[str] = set()
    for session_id in session_ids:
        identifier = str(session_id)
        if not identifier:
            continue
        resolved.add(f"session:{identifier}")
        resolved.update(f"agent:{agent_id}" for agent_id in bindings.get(identifier, ()))
    return resolved


def agent_delegations(
    delegations: Mapping[str, Mapping[str, Any]], agent_id: str
) -> tuple[Mapping[str, Any], ...]:
    return tuple(
        record for record in delegations.values() if str(record.get("agent_id", "")) == agent_id
    )


def retirement_blockers(
    *,
    agents: Mapping[str, Mapping[str, Any]],
    delegations: Mapping[str, Mapping[str, Any]],
    reviews: Mapping[str, Mapping[str, Any]],
    agent_id: str,
) -> list[str]:
    """Why this role cannot leave service yet.

    Parentage is the wrong guard here.  "Only roles you created" is a sound
    default, but the invariant that actually protects the workspace is about
    state: nobody may retire a role that still owes a running delegation or an
    unfinished review, however it came to exist.
    """

    record = agents.get(agent_id)
    if record is None:
        return ["role does not exist"]
    if record.get("state") != "active":
        return ["role is already retired"]
    blockers: list[str] = []
    for delegation in agent_delegations(delegations, agent_id):
        if delegation.get("state") in NON_TERMINAL_DELEGATION_STATES:
            blockers.append(f"delegation {delegation.get('id')} is {delegation.get('state')}")
        target = delegation.get("target_ref") or {}
        if (
            delegation.get("purpose") == "independent_review"
            and isinstance(target, Mapping)
            and target.get("kind") == "review"
        ):
            review = reviews.get(str(target.get("id", "")))
            if review is not None and review.get("state") == "requested":
                blockers.append(f"review {target.get('id')} is still open")
    return sorted(set(blockers))


def prior_verdicts(
    reviews: Mapping[str, Mapping[str, Any]],
    bindings: Mapping[str, frozenset[str]],
    *,
    agent_id: str,
    target_ref: Mapping[str, Any],
) -> tuple[str, ...]:
    """Reviews this role has already completed against the same subject.

    Role memory is defined as *receiving your own earlier judgment on the same
    subject*, which is checkable, rather than as *knowing the past*, which is
    not and would be harmful to forbid.  These records are never hidden from the
    role -- they are surfaced to the human reading the verdict, so a judgment
    from an accumulated context does not read as a clean-slate one.

    ``bindings`` is a ``session_agent_map`` result: a session maps to the *set*
    of roles it ran as, so membership is the test.  Comparing the frozenset to
    the id with ``==`` was always false, which is why wiring this as written
    would have reported "no prior verdicts" for everyone (M7, 2026-08-10
    review).
    """

    found = []
    for identifier, review in reviews.items():
        if review.get("state") == "requested" or review.get("target_ref") != dict(target_ref):
            continue
        session_id = str((review.get("actor") or {}).get("session_id", ""))
        if session_id and agent_id in bindings.get(session_id, frozenset()):
            found.append(identifier)
    return tuple(sorted(found))
