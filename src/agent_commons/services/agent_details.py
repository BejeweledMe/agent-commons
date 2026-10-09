"""Exact, bounded editor data for one standing agent, never truncated buffers."""

from __future__ import annotations

from typing import Any

from agent_commons.core.canonical import canonical_json_bytes
from agent_commons.core.ids import is_typed_id
from agent_commons.domain.agents import effective_grants
from agent_commons.domain.roles import agent_delegations, retirement_blockers
from agent_commons.errors import CommonsError
from agent_commons.library import LibraryError
from agent_commons.runtime.model import profile_tool_summary
from agent_commons.services.roles import role_model


class AgentDetailsError(CommonsError):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code = code
        self.status = status


def agent_details(manager: Any, agent_id: str, *, library: Any, catalog: Any) -> dict[str, Any]:
    if not is_typed_id(agent_id, "agent"):
        raise AgentDetailsError("agent_invalid_id", "Choose a valid agent.", 400)
    snapshot = manager.snapshot()
    if any(issue.severity == "error" for issue in snapshot.issues):
        raise AgentDetailsError("agent_details_unavailable", "Resolve workspace integrity first.")
    record = snapshot.agents.get(agent_id)
    if record is None:
        raise AgentDetailsError("agent_not_found", "This agent no longer exists.", 404)
    profile = profile_tool_summary().get(str(record.get("profile_id")), {})
    specialization = None
    specialization_state = "none"
    ref = record.get("specialization_ref")
    if ref is not None:
        try:
            entry = library.resolve(ref)
            content = entry["content"]
            methods = []
            for kind, selected in (
                ("entry", [content["entry_skill"]]),
                ("core", content["core_skills"]),
                ("conditional", content["conditional_skills"]),
            ):
                for item in selected:
                    skill_ref = item["ref"] if kind == "conditional" else item
                    skill = library.resolve(skill_ref)
                    methods.append(
                        {
                            "ref": skill["ref"],
                            "name": skill["content"]["name"],
                            "kind": kind,
                            "when": item["when"] if kind == "conditional" else None,
                        }
                    )
            specialization = {
                "ref": entry["ref"],
                "name": content["name"],
                "description": content["description"],
                "methods": methods,
            }
            specialization_state = "ready"
        except (LibraryError, OSError):
            # The immutable reference still renders; missing local content is
            # not an empty specialization and must not become an edit buffer.
            specialization_state = "unavailable"
    links = [
        {
            "id": link_id,
            "revision": snapshot.entity_revision("agent_link", link_id),
            "from_agent_id": link["from_agent_id"],
            "to_agent_id": link["to_agent_id"],
            "allowed_action": link["allowed_action"],
            "state": link["state"],
        }
        for link_id, link in sorted(snapshot.agent_links.items())
        if link.get("state") == "open"
        and agent_id in (link.get("from_agent_id"), link.get("to_agent_id"))
    ]
    result = {
        "schema": "agent_commons.agent-details.v1",
        "id": agent_id,
        "revision": snapshot.entity_revision("agent", agent_id),
        "name": record["name"],
        "profile_id": record["profile_id"],
        "model": role_model(record),
        "state": record["state"],
        "context_mode": record["context_mode"],
        "lifetime": dict(record["lifetime"]),
        "grants": dict(record["grants"]),
        "effective_grants": effective_grants(snapshot.agents, agent_id),
        "skills": list(record.get("skills") or []),
        "tool_allowlist": list(record.get("tool_allowlist") or []),
        "turnover_budget": record.get("turnover_budget"),
        "supervisor_agent_id": record.get("supervisor_agent_id"),
        "created_by_agent_id": record.get("created_by_agent_id"),
        "origin": record["origin"],
        "approval": record["approval"],
        "specialization_ref": dict(ref) if ref is not None else None,
        "specialization": specialization,
        "specialization_state": specialization_state,
        "profile_tools": sorted(
            set(profile.get("fixed", []))
            | set(profile.get("narrowable", []))
            | set(profile.get("grant_tools", {}).values())
        ),
        "profile_skills": sorted(item["id"] for item in catalog.get("skills", [])),
        "live_delegations": sorted(
            str(item["id"])
            for item in agent_delegations(snapshot.delegations, agent_id)
            if item.get("state") in {"requested", "active", "input_needed"}
        ),
        "retirement_blockers": retirement_blockers(
            agents=snapshot.agents,
            delegations=snapshot.delegations,
            reviews=snapshot.reviews,
            agent_id=agent_id,
        ),
        "links": links,
        "organization_children": [
            {"id": child_id, "name": child["name"]}
            for child_id, child in sorted(snapshot.agents.items())
            if child.get("supervisor_agent_id") == agent_id and child.get("state") == "active"
        ],
    }
    if len(canonical_json_bytes(result)) > 512 * 1024:
        raise AgentDetailsError(
            "agent_details_too_large", "Agent settings exceed the editor limit."
        )
    manager.policy.assert_safe(result, context="agent settings")
    return result
