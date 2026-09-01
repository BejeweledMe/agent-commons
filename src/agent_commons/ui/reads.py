"""Read models for the local UI.

``UIReads`` owns the panel-specific projection and operational read models.
It is a state-free mixin: the cache, manager factory, session provenance, and
configured paths remain on ``UIContext``.  That keeps the JSON surface stable
while preventing new screens from extending the cache facade directly.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any, cast

from agent_commons.catalog import CATALOG_SECTIONS, load_role_catalog
from agent_commons.core.bounded import bounded_copy
from agent_commons.domain.agents import PROFILE_NARROWING
from agent_commons.domain.attention import awaits_human
from agent_commons.domain.collections import collection_for
from agent_commons.domain.envelopes import JsonValue
from agent_commons.errors import CommonsError, ValidationError
from agent_commons.runtime.model import (
    BuiltinProfileId,
    profile_tool_summary,
    validate_model_name,
)
from agent_commons.runtime.provider_qualification import ProviderQualificationStore
from agent_commons.services.delegation_runtime import load_runtime_configuration
from agent_commons.services.provider_availability import ProviderAvailabilityService
from agent_commons.services.roles import role_model
from agent_commons.ui.actions import SETUP_SUPPORT_BINARY_UNRESOLVED
from agent_commons.ui.context_pack_dtos import (
    context_pack_catalog_payload,
    context_pack_detail_payload,
)
from agent_commons.ui.read_dtos import (
    AttentionItem,
    AttentionResponse,
    ConfigBrokenAttention,
    LaunchContextPackDTO,
    ProposalAttention,
    RunBlockedAttention,
    SetupGuidanceBlockerCode,
    SetupGuidanceDTO,
    SetupGuidanceLocationLabel,
    SetupGuidanceNextActionKey,
    SetupGuidancePayload,
    SetupGuidanceTool,
    ThreadAttention,
    WorkReturnedAttention,
)

_LOG = logging.getLogger("agent_commons.ui")


def session_state(session: Any) -> str:
    """Effective session state: a session whose TTL lapsed is not active."""

    if session.status != "active":
        return str(session.status)
    return "expired" if session.expired else "active"


def _elapsed_seconds(started_at: Any, ended_at: Any) -> float | None:
    """Seconds between attempt timestamps, or None when that is not honest."""

    try:
        start = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(ended_at).replace("Z", "+00:00"))
        elapsed = (end - start).total_seconds()
    except (TypeError, ValueError, OverflowError):
        return None
    return elapsed if elapsed >= 0 else None


def _attention_json(value: object) -> JsonValue:
    """Type a value already validated by the canonical or runtime read boundary."""

    return cast(JsonValue, value)


class UIReads:
    """Read-only UI models, layered over ``UIContext``'s shared state."""

    def provider_auth_status(self, *, profile_id: str) -> dict[str, Any]:
        """Return one short-lived, secret-free provider availability snapshot."""

        return self._launch_coordinator.provider_auth_status(profile_id)

    def provider_availability(self) -> list[dict[str, object]]:
        """Join closed capabilities and current operational provider observations."""

        config = load_runtime_configuration(self._profile_config, workspace_root=self.repo)
        manager = self.manager()
        auth_by_profile: dict[str, Mapping[str, Any]] = {}
        for profile_id in config.profiles.profile_ids:
            try:
                auth_by_profile[profile_id.value] = self._launch_coordinator.provider_auth_status(
                    profile_id.value
                )
            except Exception:  # noqa: BLE001 - availability collapses provider detail
                continue
        return ProviderAvailabilityService(
            config.profiles,
            workspace_root=self.repo,
            qualifications=ProviderQualificationStore(
                manager.paths.state_root,
                read_only=True,
            ),
            limits=config.limits,
        ).list(auth_by_profile=auth_by_profile)

    def work_context_packs(self) -> dict[str, Any]:
        """Return current Context Pack revisions through Work's narrow DTO."""

        records = self.manager().context_packs.list()
        return context_pack_catalog_payload(records)

    def work_context_pack(self, *, context_pack_id: str) -> dict[str, Any]:
        """Return the current exact revision for editing in Work."""

        record = self.manager().context_packs.get(context_pack_id)
        return context_pack_detail_payload(record)

    def setup_status(self) -> dict[str, Any]:
        """Return first-run state and only the temporary diagnostics it may expose."""

        from agent_commons.ui import setup

        report = setup.setup_state_report(self.repo, profile_config=self._profile_config)
        state = report["state"]
        status: dict[str, Any] = {
            "state": state,
            "operator_panel": self.operator_panel,
            "launch_enabled": self.launch_enabled,
            "catalog_editing_enabled": self.catalog_editing_enabled,
        }
        if state == setup.SETUP_CONFIGURED:
            return status
        if state == setup.CONFIG_REJECTED_BY_LOADER:
            status["rejected_reason"] = report["rejected_reason"]
            status["rejected_path"] = report["rejected_path"]
        discovery = setup.discover_providers(self.repo)
        missing = self._unresolved_support_binaries(discovery)
        status["providers"] = discovery.describe()
        status["providers_found"] = list(discovery.providers_found)
        status["providers_missing"] = list(discovery.providers_missing)
        status["support_missing"] = list(missing)
        status["config_path"] = str(self._profile_config or setup.default_runtime_config_path())
        status["blocking_refusal"] = (
            setup.SETUP_NO_PROVIDER_FOUND
            if not discovery.providers_found
            else (SETUP_SUPPORT_BINARY_UNRESOLVED if missing else None)
        )
        return status

    def work_setup_guidance(self, *, reveal_location_label: bool = False) -> SetupGuidancePayload:
        """Return the narrow setup explanation that the Work client may render.

        ``setup_status`` remains the legacy panel's technical first-run read,
        including temporary paths and guarded-loader details.  Work consumes
        this separate DTO so a future field added to that legacy shape cannot
        accidentally become browser-visible here.
        """

        from agent_commons.ui import setup

        report = setup.setup_state_report(self.repo, profile_config=self._profile_config)
        state = str(report["state"])
        blocker_code: SetupGuidanceBlockerCode | None
        tools: tuple[SetupGuidanceTool, ...]
        next_action_key: SetupGuidanceNextActionKey
        location_label: SetupGuidanceLocationLabel | None = None

        if state == setup.SETUP_CONFIGURED:
            blocker_code = None
            tools = ()
            next_action_key = "setup_ready"
        elif state == setup.SETUP_NOT_A_REPOSITORY:
            blocker_code = "setup_not_a_repository"
            tools = ()
            next_action_key = "choose_git_repository"
        elif state == setup.SETUP_UNINITIALIZED:
            blocker_code = "setup_uninitialized"
            tools = ()
            next_action_key = "initialize_workspace"
        elif state == setup.CONFIG_REJECTED_BY_LOADER:
            blocker_code = "setup_config_rejected_by_loader"
            tools = ()
            next_action_key = "repair_workspace_configuration"
            if reveal_location_label:
                location_label = "workspace_configuration"
        else:
            discovery = setup.discover_providers(self.repo)
            if not discovery.providers_found:
                blocker_code = "setup_no_provider_found"
                tools = ("Claude", "Codex", "Grok")
                next_action_key = "install_provider_and_check_again"
            else:
                missing = self._unresolved_support_binaries(discovery)
                supported_names: dict[str, SetupGuidanceTool] = {
                    "agent-commons-mcp": "agent-commons-mcp",
                    "git": "git",
                }
                required_tools = tuple(
                    supported_names[name] for name in missing if name in supported_names
                )
                if required_tools:
                    blocker_code = "setup_support_binary_unresolved"
                    tools = required_tools
                    next_action_key = "install_support_tool_and_check_again"
                else:
                    blocker_code = "setup_unconfigured"
                    tools = ()
                    next_action_key = "configure_runtime"

        return SetupGuidanceDTO(
            blocker_code=blocker_code,
            tools=tools,
            next_action_key=next_action_key,
            location_label=location_label,
        ).to_wire()

    def profile_info(self) -> dict[str, dict[str, Any]]:
        """Which provider and model each configured profile would run."""

        with self._guard:
            cached = self._profile_info
        if cached is not None:
            return cached

        from agent_commons.services.delegation_runtime import load_runtime_configuration

        summary: dict[str, dict[str, Any]] = {}
        try:
            config = load_runtime_configuration(self._profile_config, workspace_root=self.repo)
            for profile_id in config.profiles.profile_ids:
                profile = config.profiles.get(profile_id)
                model = getattr(profile, "model", None)
                summary[str(profile_id)] = {
                    "provider": str(profile.provider),
                    "model": str(model) if model is not None else None,
                }
        except (CommonsError, OSError, ValueError) as exc:
            # Operator-file detail does not belong in a bearer-token response.
            _LOG.warning("profile config not readable, serving no profile detail: %s", exc)
            summary = {}
        with self._guard:
            self._profile_info = summary
        return summary

    def model_options(self, snapshot: Any) -> dict[str, list[str]]:
        """Models the hire form may offer, from profile and active-role records only."""

        options: dict[str, set[str]] = {
            profile_id.provider.value: set() for profile_id in BuiltinProfileId
        }

        def offer(provider: str | None, model: Any) -> None:
            if provider not in options or not isinstance(model, str):
                return
            try:
                validated = validate_model_name(model)
            except ValidationError:
                _LOG.debug("a recorded model name is not offerable: %r", model[:64])
                return
            if validated is not None:
                options[provider].add(validated)

        for info in self.profile_info().values():
            offer(str(info.get("provider")), info.get("model"))
        for record in snapshot.agents.values():
            if record.get("state") != "active":
                continue
            try:
                provider = BuiltinProfileId(str(record.get("profile_id"))).provider.value
            except ValueError:
                continue
            offer(provider, role_model(record))
        return {provider: sorted(models) for provider, models in sorted(options.items())}

    def catalog(self) -> dict[str, Any]:
        """The read-only role catalogue plus operator-owned configuration metadata."""

        from agent_commons.ui import CATALOG_SCHEMA

        snapshot = self.manager().snapshot()
        catalogue = load_role_catalog(self._catalog_path, workspace_root=self.repo)
        for section in CATALOG_SECTIONS:
            field = "skills" if section == "skills" else "tool_allowlist"
            for entry in catalogue.get(section) or []:
                entry["users"] = sorted(
                    str(record["id"])
                    for record in snapshot.agents.values()
                    if record.get("state") == "active"
                    and entry.get("id") in (record.get(field) or ())
                )
        editable = ["presets"] + (list(CATALOG_SECTIONS) if self.catalog_editing_enabled else [])
        return {
            "schema": CATALOG_SCHEMA,
            "editable_here": editable,
            "operator_owned": ["profiles"]
            + ([] if self.catalog_editing_enabled else list(CATALOG_SECTIONS)),
            "catalog_editing_enabled": self.catalog_editing_enabled,
            "catalog_path": str(self._catalog_path) if self._catalog_path else None,
            "profiles": sorted(PROFILE_NARROWING),
            "profile_info": self.profile_info(),
            "model_options": self.model_options(snapshot),
            "profile_tools": profile_tool_summary(),
            "grant_levels": ["deny", "ask", "auto"],
            "context_modes": ["fresh", "accumulated"],
            **catalogue,
            "presets": [
                bounded_copy(record)
                for _, record in sorted(snapshot.agents.items())
                if record.get("template") and record.get("state") == "active"
            ],
        }

    def search(
        self, *, query: str, limit: int = 25, subject_kind: str | None = None
    ) -> dict[str, Any]:
        """Search history without recording an event."""

        return self.manager().search_history(query, limit=limit, subject_kind=subject_kind)

    def pending_operations(self) -> list[dict[str, Any]]:
        """Live requests waiting on a person and the sessions allowed to answer them."""

        from agent_commons.services.communication import (
            CommunicationRuntimeService,
            _participant_id,
        )

        session_ids = self.writer_session_ids
        manager = self._writer_bound(session_ids[-1]) if session_ids else self.manager()
        try:
            operations = CommunicationRuntimeService(manager, session_lineage=session_ids).inbox()
        except (CommonsError, OSError) as exc:
            _LOG.warning("communication inbox unavailable, live answers disabled: %s", exc)
            operations = ()
        answerable = {_participant_id(str(item)) for item in session_ids}
        found = []
        for record in operations:
            if record.get("state") not in {"open", "replied"}:
                continue
            scope = record.get("scope") or {}
            recipients = {str(item) for item in scope.get("allowed_recipient_session_ids") or ()}
            found.append(
                {
                    "operation_id": record.get("operation_id"),
                    "kind": record.get("kind"),
                    "state": record.get("state"),
                    "delegation_id": scope.get("delegation_id"),
                    "task_id": scope.get("task_id"),
                    "metadata": record.get("metadata"),
                    "deadline": record.get("deadline"),
                    "answerable_here": bool(answerable & recipients),
                    "answer_from_session": sorted(recipients),
                }
            )
        return found

    def attention(self) -> dict[str, Any]:
        """One canonical queue of everything waiting on a person."""

        snapshot = self.manager().snapshot()
        canonical_attention = awaits_human(snapshot)
        answerable_by_delegation: dict[str, Mapping[str, object]] = {}
        for operation in self.pending_operations():
            delegation_id = str(operation.get("delegation_id") or "")
            if delegation_id:
                answerable_by_delegation[delegation_id] = cast(Mapping[str, object], operation)

        items: list[AttentionItem] = []
        for attention_item in canonical_attention.items:
            if attention_item.kind != "run_blocked":
                continue
            delegation_id = attention_item.identifier
            record = attention_item.record
            operation = answerable_by_delegation.get(delegation_id)
            items.append(
                RunBlockedAttention(
                    identifier=delegation_id,
                    agent_id=_attention_json(record.get("agent_id")),
                    target_ref=_attention_json(record.get("target_ref")),
                    run_state=_attention_json(record.get("state")),
                    reason_code=_attention_json(record.get("reason_code")),
                    summary=_attention_json(record.get("summary")),
                    operation_id=_attention_json((operation or {}).get("operation_id")),
                    metadata=_attention_json((operation or {}).get("metadata")),
                    answerable_here=bool((operation or {}).get("answerable_here")),
                    answer_from_session=tuple(
                        cast(list[str], (operation or {}).get("answer_from_session") or [])
                    ),
                    deadline=_attention_json((operation or {}).get("deadline")),
                )
            )
        returned: dict[str, WorkReturnedAttention] = {}
        for attention_item in canonical_attention.items:
            if attention_item.kind != "work_returned":
                continue
            delegation_id = attention_item.identifier
            record = attention_item.record
            target = record.get("target_ref") or {}
            task_id = str(target.get("id") or "")
            task = snapshot.tasks.get(task_id)
            assert task is not None
            agent_id = record.get("agent_id")
            agent = snapshot.agents.get(str(agent_id)) if agent_id else None
            returned[task_id] = WorkReturnedAttention(
                task_id=task_id,
                title=_attention_json(task.get("title")),
                task_state=_attention_json(task.get("state")),
                task_revision=str(task.get("effective_revision") or task.get("revision")),
                delegation_id=delegation_id,
                agent_id=_attention_json(agent_id),
                agent_name=_attention_json(agent.get("name") if agent else None),
            )
        items.extend(returned.values())
        for attention_item in canonical_attention.items:
            if attention_item.kind != "thread":
                continue
            thread_id = attention_item.identifier
            record = attention_item.record
            thread_type = str(record.get("thread_type", ""))
            proposal = (record.get("extensions") or {}).get("staff_proposal")
            is_proposal = isinstance(proposal, Mapping) and proposal.get("action") == "create_role"
            if is_proposal:
                items.append(
                    ProposalAttention(
                        identifier=thread_id,
                        thread_type=thread_type,
                        subject=_attention_json(record.get("subject")),
                        revision=_attention_json(record.get("revision")),
                        proposal=cast(dict[str, JsonValue], dict(proposal)),
                    )
                )
            else:
                items.append(
                    ThreadAttention(
                        identifier=thread_id,
                        thread_type=thread_type,
                        subject=_attention_json(record.get("subject")),
                        revision=_attention_json(record.get("revision")),
                    )
                )
        if self._catalog_path is not None:
            from agent_commons.catalog import catalog_ids

            try:
                known = catalog_ids(
                    load_role_catalog(self._catalog_path, workspace_root=self.repo),
                    "skills",
                )
            except CommonsError:
                known = None
            if known is not None:
                for agent_id, record in sorted(snapshot.agents.items()):
                    if record.get("state") != "active":
                        continue
                    missing = sorted(set(record.get("skills") or ()) - known)
                    if missing:
                        items.append(
                            ConfigBrokenAttention(
                                agent_id=agent_id,
                                name=_attention_json(record.get("name")),
                                missing_skills=tuple(missing),
                            )
                        )
        return AttentionResponse(items=tuple(items), writes_enabled=self.writes_enabled).to_wire()

    def engagements(self) -> list[dict[str, Any]]:
        """The main chats, readable whether or not this server writes."""

        return [bounded_copy(item) for item in self.manager().list_engagements()]

    def agent_proposals(self) -> list[dict[str, Any]]:
        """Open role proposals, readable whether or not this server writes."""

        return [bounded_copy(item) for item in self.manager().list_agent_proposals()]

    def runs(self) -> list[dict[str, Any]]:
        """Live and recent provider runs with bounded local failure diagnostics."""

        from agent_commons.runtime import AttemptStore, TerminalToolAuditStore

        manager = self.manager()
        store = AttemptStore(manager.paths.state_root, read_only=True)
        try:
            attempts = store.list_attempts()
        except (CommonsError, OSError):
            return []
        delegations = manager.snapshot().delegations
        audit_store = TerminalToolAuditStore(
            manager.paths.state_root,
            security_policy=manager.policy,
            read_only=True,
        )
        found: list[dict[str, Any]] = []
        for attempt in attempts:
            record = attempt.as_dict()
            delegation_id = str(record["correlation"]["delegation_id"])
            delegation = delegations.get(delegation_id) or {}
            live = not attempt.state.terminal and store.process_is_live(attempt.pid)
            try:
                audit = audit_store.get(delegation_id)
                rejection_details = [
                    {
                        "ordinal": detail.ordinal,
                        "tool": detail.tool,
                        "error_type": detail.error_type,
                        "message": detail.message,
                        "recorded_at": detail.recorded_at,
                    }
                    for detail in audit.rejection_details
                ]
                rejection_count = audit.terminal_tool_rejections
                rejection_details_truncated = audit.rejection_details_truncated
            except (CommonsError, OSError):
                rejection_details = []
                rejection_count = 0
                rejection_details_truncated = False
            found.append(
                {
                    "delegation_id": delegation_id,
                    "attempt_id": record["attempt_id"],
                    "phase": record["state"],
                    "live": live,
                    "started_at": record.get("created_at"),
                    "updated_at": record.get("updated_at"),
                    "duration_seconds": (
                        None
                        if live
                        else _elapsed_seconds(record.get("created_at"), record.get("updated_at"))
                    ),
                    "profile_id": record.get("profile_id"),
                    "target_kind": record["correlation"].get("target_kind"),
                    "target_id": record["correlation"].get("target_id"),
                    "agent_id": delegation.get("agent_id"),
                    "delegation_state": delegation.get("state"),
                    "purpose": delegation.get("purpose"),
                    "limits": delegation.get("limits") or None,
                    "summary": (
                        str(delegation.get("summary"))[:500] if delegation.get("summary") else None
                    ),
                    "stderr_diagnostic_tail": record.get("stderr_diagnostic_tail"),
                    "stderr_diagnostic_tail_truncated": bool(
                        record.get("stderr_diagnostic_tail_truncated", False)
                    ),
                    "stderr_diagnostic_tail_redacted": bool(
                        record.get("stderr_diagnostic_tail_redacted", False)
                    ),
                    "terminal_tool_rejections": rejection_count,
                    "terminal_tool_rejection_details": rejection_details,
                    "terminal_tool_rejection_details_truncated": rejection_details_truncated,
                }
            )
        found.sort(key=lambda item: (not item["live"], item["attempt_id"]), reverse=False)
        return [bounded_copy(item) for item in found]

    def launch_options(self) -> dict[str, Any]:
        """Active roles, open tasks, and exact current packs offered for a run."""

        snapshot = self.manager().snapshot()
        roles = [
            {
                "id": rid,
                "name": rec.get("name"),
                "profile_id": rec.get("profile_id"),
                "context_mode": rec.get("context_mode", "fresh"),
            }
            for rid, rec in sorted(snapshot.agents.items())
            if rec.get("state") == "active" and not rec.get("template")
        ]
        open_states = {"ready", "assigned", "active", "blocked", "review"}
        tasks = [
            {"id": tid, "title": rec.get("title"), "state": rec.get("state")}
            for tid, rec in sorted(snapshot.tasks.items())
            if rec.get("state") in open_states
        ]
        context_pack_records = [
            record
            for _, record in sorted(snapshot.context_packs.items())
            if record.state == "published"
        ]
        max_context_pack_options = 256
        context_packs = [
            LaunchContextPackDTO(
                context_pack_id=record.context_pack_id,
                revision=record.revision,
                summary=str(record.draft.summary),
                fact_count=len(record.draft.facts),
                open_question_count=len(record.draft.open_questions),
            ).to_wire()
            for record in context_pack_records[:max_context_pack_options]
        ]
        context_packs_truncated = len(context_pack_records) > max_context_pack_options
        return {
            "launch_enabled": self.launch_enabled,
            "roles": roles,
            "tasks": tasks,
            "context_packs": context_packs,
            "context_pack_options_status": {
                "freshness": "current",
                "truncated": context_packs_truncated,
                "refusal": ("context_pack_options_truncated" if context_packs_truncated else None),
            },
        }

    def entity(self, kind: str, entity_id: str) -> Mapping[str, Any] | None:
        """Return the existing bounded entity shape for one supported kind."""

        if kind == "session":
            for session in self.manager().sessions.list_sessions():
                if session.session_id == entity_id:
                    return bounded_copy(session.actor_context() | {"state": session_state(session)})
            return None
        attribute = collection_for(kind)
        if attribute is None:
            return None
        manager = self.manager()
        if kind == "agent":
            if entity_id not in manager.snapshot().agents:
                return None
            return bounded_copy(manager.get_agent(entity_id))
        record = getattr(manager.snapshot(), attribute).get(entity_id)
        return None if record is None else bounded_copy(record)
