"""Standing-role commands mixed into the universal workspace manager."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agent_commons.core.refs import normalize_ref
from agent_commons.domain.agents import (
    GRANT_NAMES,
    agent_delegations,
    descendants,
    effective_grants,
    lineage,
    retirement_blockers,
    turnover_used,
)
from agent_commons.domain.lifecycle import acting_agent_id, require_entity
from agent_commons.domain.snapshot import ProjectSnapshot
from agent_commons.domain.states import NON_TERMINAL_DELEGATION_STATES
from agent_commons.errors import LifecycleConflictError

from ._validation import _optional_list


def role_model(record: Mapping[str, Any] | None) -> str | None:
    """The model a role was hired on, or None when its profile decides.

    One reader beside the one writer below, because the field lives in a
    free-form object: everything that consumes it -- the launch that puts it in
    a provider's argv, the catalogue that offers it again, a preset that passes
    it on -- must agree on where it is and on what a malformed value means.

    A record is replayed from a file this process may not have written, so this
    never raises and never trusts: anything that is not a plain string under
    ``extensions.model`` reads as absent, and the profile's own model stands.
    Callers still validate before use; this only refuses to guess.
    """

    if not isinstance(record, Mapping):
        return None
    extensions = record.get("extensions")
    if not isinstance(extensions, Mapping):
        return None
    model = extensions.get("model")
    return model if isinstance(model, str) and model else None


class RoleCommands:
    """Commands for standing roles and their temporary links."""

    def list_agents(self, *, include_retired: bool = False) -> list[dict[str, Any]]:
        snapshot = self.snapshot()
        return [
            self._agent_view(snapshot, record)
            for _, record in sorted(snapshot.agents.items())
            if include_retired or record.get("state") == "active"
        ]

    def get_agent(self, agent_id: str) -> dict[str, Any]:
        snapshot = self.snapshot()
        return self._agent_view(snapshot, require_entity(snapshot, "agent", agent_id))

    @staticmethod
    def _agent_view(snapshot: ProjectSnapshot, record: Mapping[str, Any]) -> dict[str, Any]:
        """A role plus the authority it actually has right now.

        Effective grants are recomputed on every read, so a level lowered on an
        ancestor shows here -- and applies -- without a propagation pass.
        """

        agent_id = str(record["id"])
        return {
            **dict(record),
            "effective_grants": effective_grants(snapshot.agents, agent_id),
            "created_roles": list(descendants(snapshot.agents, agent_id)),
            "turnover_used": turnover_used(snapshot.agents, agent_id),
            "live_delegations": sorted(
                str(item.get("id"))
                for item in agent_delegations(snapshot.delegations, agent_id)
                if item.get("state") in NON_TERMINAL_DELEGATION_STATES
            ),
            "retirement_blockers": retirement_blockers(
                agents=snapshot.agents,
                delegations=snapshot.delegations,
                reviews=snapshot.reviews,
                agent_id=agent_id,
            ),
        }

    def create_agent(
        self,
        *,
        name: str,
        profile_id: str,
        grants: Mapping[str, str] | None = None,
        context_mode: str = "fresh",
        rationale: str,
        lifetime: Mapping[str, Any] | None = None,
        skills: Sequence[str] = (),
        tool_allowlist: Sequence[str] = (),
        turnover_budget: int | None = None,
        template: bool = False,
        model: str | None = None,
        created_by_agent_id: str | None = None,
        approval: str | None = None,
        proposal_ref: Mapping[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Record a standing role.

        ``model`` is the one thing here that is chosen once and never again: a
        different model needs the role's whole context recomputed for it, so
        changing it on a hired role is not offered and `reconfigure_agent` has
        no field for it.  Absent, the role runs whatever its profile names.

        It is stored under ``payload.extensions`` -- the free-form object the
        payload schema already carries -- and not as a new top-level field.  A
        model is operator preference on top of a profile, not a fifth thing the
        domain reasons about: no rule reads it, no state depends on it, and
        recorded events keep the shape they have always had.
        """

        key = self._idempotency_key("agent.created", idempotency_key)
        agent_id = self._new_entity_id("agent", "agent.created", key)
        snapshot = self.snapshot()
        session = self._active_session()
        acting = acting_agent_id(snapshot, session.session_id)
        # A role recorded by a session that is itself running as a role is an
        # agent-created role whatever the caller says.  Deriving origin from the
        # ledger keeps `agent list` honest about where staff came from.
        creator = created_by_agent_id or acting
        origin = "agent" if creator else "human"
        if approval is None:
            approval = (
                "human"
                if origin == "human"
                else ("automatic" if acting == creator else "human_confirmed")
            )
        payload: dict[str, Any] = {
            "agent_id": agent_id,
            "name": name,
            "profile_id": profile_id,
            # Denied by default in every path: a standing right to change the
            # staff is the exception, and a declared lifetime covers the common
            # "made for this task, gone when it lands" case without one.
            "grants": dict(grants or dict.fromkeys(GRANT_NAMES, "deny")),
            "context_mode": context_mode,
            "origin": origin,
            "approval": approval,
            "rationale": rationale,
            "lifetime": dict(lifetime or {"kind": "persistent"}),
            "created_by_agent_id": creator,
            "turnover_budget": turnover_budget,
            "template": bool(template),
        }
        if model is not None:
            # Imported where it is used, not at module scope: the canonical
            # layer owes the optional runtime package no import edge, and a
            # model name is the one thing here that has anything to do with it.
            from agent_commons.runtime.model import validate_model_name

            # Validated here as well as at every caller: this is the last point
            # before the value becomes an immutable canonical event, and an
            # unsafe name recorded once would be replayed into a provider's
            # argv on every launch of this role afterwards.
            payload["extensions"] = {"model": validate_model_name(model)}
        if proposal_ref is not None:
            payload["proposal_ref"] = normalize_ref(proposal_ref)
        for field_name, values in (
            ("skills", skills),
            ("tool_allowlist", tool_allowlist),
        ):
            if values:
                payload[field_name] = _optional_list(list(values), field_name)
        relations = []
        if creator:
            relations.append(
                self._relation(
                    {"kind": "agent", "id": agent_id},
                    "created_by",
                    {"kind": "agent", "id": str(creator)},
                )
            )
        return self.record_event(
            "agent.created",
            payload,
            idempotency_key=key,
            relations=relations,
            tags=("agent", origin),
        )

    def propose_agent(
        self,
        *,
        name: str,
        profile_id: str,
        rationale: str,
        grants: Mapping[str, str] | None = None,
        context_mode: str = "fresh",
        turnover_budget: int | None = None,
        lifetime: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Ask a human for a role this session's own grant cannot record itself.

        A proposal is not a record: until it is confirmed it changes no run and
        grants nothing.  It is an ordinary typed thread, so it lands in the same
        inbox and the same panel as every other thing an agent needs a person
        for.
        """

        snapshot = self.snapshot()
        acting = acting_agent_id(snapshot, self._active_session().session_id)
        if acting is None:
            raise LifecycleConflictError("only a session running as a role proposes a role")
        if effective_grants(snapshot.agents, acting)["create_roles"] == "deny":
            raise LifecycleConflictError(f"role {acting} may not propose roles")
        proposal = {
            "action": "create_role",
            "name": name,
            "profile_id": profile_id,
            "rationale": rationale,
            "grants": dict(grants or dict.fromkeys(GRANT_NAMES, "deny")),
            "context_mode": context_mode,
            "turnover_budget": turnover_budget,
            "lifetime": dict(lifetime or {"kind": "persistent"}),
            "proposed_by_agent_id": acting,
        }
        return self.open_thread(
            thread_type="proposal",
            subject=f"New role proposed: {name}",
            desired_outcome="a human confirms or declines this role",
            to=("operator",),
            related_refs=({"kind": "agent", "id": acting},),
            extensions={"staff_proposal": proposal},
            idempotency_key=self._idempotency_key("thread.opened", idempotency_key),
        )

    def list_agent_proposals(self) -> list[dict[str, Any]]:
        """Open role proposals awaiting a person, newest last."""

        snapshot = self.snapshot()
        found = []
        for identifier, thread in sorted(snapshot.threads.items()):
            if thread.get("state") != "open" or thread.get("thread_type") != "proposal":
                continue
            proposal = (thread.get("extensions") or {}).get("staff_proposal")
            if isinstance(proposal, Mapping) and proposal.get("action") == "create_role":
                found.append(
                    {
                        "thread_id": identifier,
                        "revision": thread.get("revision"),
                        "proposal": dict(proposal),
                        "recorded_at": thread.get("recorded_at"),
                    }
                )
        return found

    def approve_agent_proposal(
        self,
        thread_id: str,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Record exactly the role that was proposed, crediting its proposer.

        The fields come from the proposal rather than from the caller, so
        confirming cannot quietly become creating something else under the
        proposer's name.
        """

        # Create the role and close the proposal as one action.  Approving used
        # to leave the thread open, so a second click minted a duplicate role,
        # spent turnover budget again, and left the item glowing in the
        # attention queue forever -- "approving that proposal" happening twice
        # (round 2, both product reviewers).  Under one write lock the second
        # approval finds the thread resolved and is refused.
        with self._canonical_write_lock():
            snapshot = self.snapshot()
            thread = require_entity(snapshot, "thread", thread_id)
            if thread.get("state") != "open":
                raise LifecycleConflictError(
                    "this role proposal is already resolved and cannot be confirmed again"
                )
            proposal = (thread.get("extensions") or {}).get("staff_proposal")
            if not isinstance(proposal, Mapping) or proposal.get("action") != "create_role":
                raise LifecycleConflictError("thread carries no role-creation proposal")
            created = self.create_agent(
                name=str(proposal["name"]),
                profile_id=str(proposal["profile_id"]),
                rationale=str(proposal["rationale"]),
                context_mode=str(proposal.get("context_mode", "fresh")),
                grants=proposal.get("grants"),
                turnover_budget=proposal.get("turnover_budget"),
                lifetime=proposal.get("lifetime"),
                created_by_agent_id=str(proposal["proposed_by_agent_id"]),
                approval="human_confirmed",
                proposal_ref={"kind": "thread", "id": thread_id},
                idempotency_key=idempotency_key,
            )
            self.resolve_thread(
                thread_id,
                str(thread.get("effective_revision") or thread["revision"]),
                resolution="accepted",
                summary=f"confirmed role {created['entity_ref']['id']}",
                idempotency_key=(f"{idempotency_key}:resolve" if idempotency_key else None),
            )
            return created

    def decline_agent_proposal(
        self,
        thread_id: str,
        *,
        reason: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Resolve an open role proposal without creating the proposed role."""

        snapshot = self.snapshot()
        thread = require_entity(snapshot, "thread", thread_id)
        proposal = (thread.get("extensions") or {}).get("staff_proposal")
        if thread.get("state") != "open":
            raise LifecycleConflictError("this role proposal is already resolved")
        if not isinstance(proposal, Mapping) or proposal.get("action") != "create_role":
            raise LifecycleConflictError("thread carries no role-creation proposal")
        return self.resolve_thread(
            thread_id,
            str(thread.get("effective_revision") or thread["revision"]),
            resolution="rejected",
            summary=reason,
            idempotency_key=idempotency_key,
        )

    def reconfigure_agent(
        self,
        agent_id: str,
        expected_revision: str,
        *,
        changes: Mapping[str, Any],
        reason: str,
        isolation_downgrade_reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = self._idempotency_key("agent.reconfigured", idempotency_key)
        payload: dict[str, Any] = {
            "agent_id": agent_id,
            "expected_revision": expected_revision,
            "changes": dict(changes),
            "reason": reason,
        }
        if isolation_downgrade_reason is not None:
            self.sessions.require_active(
                self._active_session().session_id,
                capability="agent:isolation_downgrade",
            )
            payload["isolation_downgrade"] = {
                "reason": isolation_downgrade_reason,
                "operator_capability": "agent:isolation_downgrade",
            }
        return self.record_event(
            "agent.reconfigured",
            payload,
            idempotency_key=key,
            tags=("agent",),
        )

    def retire_agent(
        self,
        agent_id: str,
        expected_revision: str | None = None,
        *,
        reason: str,
        cascade: bool = False,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Take a role out of service, optionally with everything it created.

        Nothing is deleted; the ledger keeps the whole history.  The whole
        cascade runs inside one canonical write lock: a half-applied cascade
        leaves exactly the orphaned authority it exists to remove, and a
        concurrent writer that gave the root live work between two separate
        writes used to produce precisely that -- descendants retired, root
        active.  Members are written leaves-first, so if anything still fails
        mid-cascade a child is never left active under a retired parent.
        """

        with self._canonical_write_lock():
            snapshot = self.snapshot()
            require_entity(snapshot, "agent", agent_id)
            targets = [*descendants(snapshot.agents, agent_id), agent_id] if cascade else [agent_id]
            targets = [identifier for identifier in targets if identifier in snapshot.agents]
            # Leaves-first: order by distance from the human root, deepest first,
            # so a role is always retired before the creator it reports to.
            # descendants() returns ULID (chronological, ancestors-first) order,
            # the opposite of what a cascade needs.
            targets.sort(
                key=lambda identifier: (len(lineage(snapshot.agents, identifier)), identifier),
                reverse=True,
            )
            pending = [
                identifier
                for identifier in targets
                if snapshot.agents[identifier].get("state") == "active"
            ]
            blocked = {
                identifier: retirement_blockers(
                    agents=snapshot.agents,
                    delegations=snapshot.delegations,
                    reviews=snapshot.reviews,
                    agent_id=identifier,
                )
                for identifier in pending
            }
            refusals = {key_: value for key_, value in blocked.items() if value}
            if refusals:
                detail = "; ".join(
                    f"{name}: {', '.join(items)}" for name, items in refusals.items()
                )
                # Say what was actually attempted.  Reporting a plain retire as a
                # refused cascade is a small lie in the record, and this branch
                # has already paid for one of those.
                scope = "cascade retire is refused as a whole" if cascade else "retire is refused"
                raise LifecycleConflictError(f"{scope}: a role owing live work: {detail}")
            base_key = self._idempotency_key("agent.retired", idempotency_key)
            session = self._active_session()
            retired_by = "agent" if acting_agent_id(snapshot, session.session_id) else "human"
            results = []
            for identifier in pending:
                record = snapshot.agents[identifier]
                revision = (
                    expected_revision
                    if identifier == agent_id and expected_revision
                    else str(record.get("effective_revision") or record["revision"])
                )
                payload: dict[str, Any] = {
                    "agent_id": identifier,
                    "expected_revision": revision,
                    "reason": reason,
                    "retired_by": "cascade" if identifier != agent_id else retired_by,
                }
                if identifier != agent_id:
                    payload["cascade_of"] = agent_id
                results.append(
                    self.record_event(
                        "agent.retired",
                        payload,
                        idempotency_key=f"{base_key}:{identifier}",
                        tags=("agent",),
                    )
                )
            if not results:
                raise LifecycleConflictError(f"agent is already retired: {agent_id}")
            return {"retired": results, "count": len(results)}

    def open_agent_link(
        self,
        *,
        from_agent_id: str,
        to_agent_id: str,
        allowed_action: str = "ask",
        deadline_seconds: int | None = None,
        reason: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = self._idempotency_key("agent.link_opened", idempotency_key)
        link_id = self._new_entity_id("agent_link", "agent.link_opened", key)
        payload: dict[str, Any] = {
            "link_id": link_id,
            "from_agent_id": from_agent_id,
            "to_agent_id": to_agent_id,
            # A typed action, never an open/closed flag: adding
            # `handoff_work` later extends this enum instead of reshaping
            # the record.
            "allowed_action": allowed_action,
            "reason": reason,
        }
        # Optional, and recorded only when an operator actually stated one: a
        # horizon nothing can enforce (replay has no clock) is intent, not a
        # rule, and a required field nobody reads is a tax on every caller.
        if deadline_seconds is not None:
            payload["deadline_seconds"] = deadline_seconds
        return self.record_event(
            "agent.link_opened",
            payload,
            idempotency_key=key,
            relations=[
                self._relation(
                    {"kind": "agent_link", "id": link_id},
                    "links",
                    {"kind": "agent", "id": to_agent_id},
                )
            ],
            tags=("agent_link", allowed_action),
        )

    def close_agent_link(
        self,
        link_id: str,
        expected_revision: str,
        *,
        reason: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = self._idempotency_key("agent.link_closed", idempotency_key)
        return self.record_event(
            "agent.link_closed",
            {"link_id": link_id, "expected_revision": expected_revision, "reason": reason},
            idempotency_key=key,
            tags=("agent_link",),
        )
