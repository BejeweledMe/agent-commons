"""Writing workflows for the local UI.

``UIActions`` is deliberately a state-free mixin.  ``UIContext`` keeps the
session, manager factory, cache, and graph; this module owns the workflows that
can mutate an operator file or record a canonical event. Provider-launch
coordination is isolated in :mod:`agent_commons.ui.launch`.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent_commons.catalog import CATALOG_SECTIONS, load_role_catalog, write_role_catalog
from agent_commons.errors import ConfigurationError, LifecycleConflictError, ValidationError
from agent_commons.runtime.model import profile_tool_summary, validate_model_name
from agent_commons.services.manager import CommonsManager
from agent_commons.services.roles import role_model
from agent_commons.ui.launch import LAUNCH_NOT_CONFIGURED as LAUNCH_NOT_CONFIGURED
from agent_commons.views import truncate_utf8

#: A provider was resolved but one of the executables every profile needs beside
#: it was not.  The wave contract froze this code after `ui.setup` found the
#: state reachable and refused to invent a code for it: that module may not
#: extend the frozen table, so the binding lives here, on the writing workflow
#: that turns its answer into an HTTP refusal the first-run screen can draw.
SETUP_SUPPORT_BINARY_UNRESOLVED = "setup_support_binary_unresolved"

#: Why a model name typed into the hire form was refused, in terms the person
#: looking at the field can act on.  The rule itself lives in `runtime.model`.
MODEL_NAME_REFUSED = (
    "that is not a usable model name: it must start with a letter or digit and may "
    "then contain letters, digits, and . _ : / - up to 256 characters. Leave the "
    "field empty to run the model the profile already names"
)

#: What a preflight result means, said once.  `preflight_profile` checks fixed
#: argv and MCP startup and carries no credential at all.
PREFLIGHT_CREDENTIAL_FREE = (
    "this check is structural: it verifies the fixed launch flags and the MCP "
    "handshake without any credential, so it cannot tell a signed-out provider "
    "from an authorized one -- a green result does not mean you are logged in"
)


class UIActions:
    """Panel workflows that write through the operator manager.

    The mixin intentionally depends only on the narrow context capabilities it
    uses (session access, manager factories, cache invalidation, and configured
    paths).  It owns no parallel state and therefore cannot bypass the single
    read-only/writer manager boundary on ``UIContext``.
    """

    @staticmethod
    def _unresolved_support_binaries(discovery: Any) -> tuple[str, ...]:
        """Support executables every generated profile needs, that did not resolve."""

        return tuple(probe.name for probe in (discovery.mcp, discovery.git) if not probe.found)

    def initialize_workspace(self) -> dict[str, Any]:
        """Create the workspace in this directory, through the code `init` runs."""

        from agent_commons.ui import setup

        if setup.setup_state(self.repo) == setup.SETUP_NOT_A_REPOSITORY:
            raise setup.SetupError(
                setup.SETUP_NOT_A_REPOSITORY,
                "this directory is not a git repository, so there is nothing for "
                "a workspace to attach to",
            )
        report = CommonsManager.initialize(self.repo, integrations=("codex", "claude"))
        self.invalidate()
        return {
            "workspace_id": report["workspace_id"],
            "integrations": list(report["integrations"]),
            "changed": bool(report["changed"]),
            "state": setup.setup_state(self.repo, profile_config=self._profile_config),
        }

    def configure_runtime(self) -> dict[str, Any]:
        """Generate the operator runtime config and adopt it without a restart."""

        from agent_commons.ui import setup

        if setup.setup_state(self.repo, profile_config=self._profile_config) == (
            setup.SETUP_CONFIGURED
        ):
            raise setup.SetupError(
                setup.SETUP_CONFIGURED,
                "this environment already has a working runtime config; add profiles for "
                "a newly discovered provider with the configured-profiles action, or edit "
                "the file manually",
            )
        discovery = setup.discover_providers(self.repo)
        missing = self._unresolved_support_binaries(discovery)
        if discovery.providers_found and missing:
            reasons = "; ".join(
                f"{probe.name} [{refusal.candidate}]: {refusal.reason}"
                for probe in (discovery.mcp, discovery.git)
                if not probe.found
                for refusal in probe.refusals
            )
            raise setup.SetupError(
                SETUP_SUPPORT_BINARY_UNRESOLVED,
                "a provider was found but "
                + " and ".join(missing)
                + " could not be resolved, and every generated profile names both"
                + (f": {reasons}" if reasons else ""),
                details={"missing": list(missing), "discovery": discovery.describe()},
            )
        written = setup.generate_runtime_config(
            self.repo,
            state_root=self._state_root,
            state_base=self._state_base,
            discovery=discovery,
        )
        adopted = self.adopt_runtime_config(written["path"])
        return {
            "state": setup.SETUP_CONFIGURED,
            "providers_found": written["providers_found"],
            "providers_missing": written["providers_missing"],
            **adopted,
        }

    def add_discovered_providers(self) -> dict[str, Any]:
        """Add profiles only when the current config still proves machine-made."""

        from agent_commons.ui import setup

        state = setup.setup_state(self.repo, profile_config=self._profile_config)
        if state != setup.SETUP_CONFIGURED:
            raise setup.SetupError(
                state,
                "profiles can be added only to a working configured environment; "
                "use the ordinary setup action for this state",
            )
        default_config = setup.default_runtime_config_path().resolve()
        if self._profile_config is not None and self._profile_config.resolve() != default_config:
            raise setup.SetupError(
                setup.SETUP_CONFIGURED,
                "a custom runtime config is operator-owned; add profiles by editing the "
                "file manually",
            )
        discovery = setup.discover_providers(self.repo)
        missing = self._unresolved_support_binaries(discovery)
        if discovery.providers_found and missing:
            reasons = "; ".join(
                f"{probe.name} [{refusal.candidate}]: {refusal.reason}"
                for probe in (discovery.mcp, discovery.git)
                if not probe.found
                for refusal in probe.refusals
            )
            raise setup.SetupError(
                SETUP_SUPPORT_BINARY_UNRESOLVED,
                "a provider was found but "
                + " and ".join(missing)
                + " could not be resolved, and every generated profile names both"
                + (f": {reasons}" if reasons else ""),
                details={"missing": list(missing), "discovery": discovery.describe()},
            )
        written = setup.add_discovered_provider_profiles(
            self.repo,
            state_root=self._state_root,
            state_base=self._state_base,
            discovery=discovery,
        )
        adopted = self.adopt_runtime_config(written["path"])
        return {
            "state": setup.SETUP_CONFIGURED,
            "added_providers": written["added_providers"],
            "changed": written["changed"],
            **adopted,
        }

    def adopt_runtime_config(self, path: str | Path) -> dict[str, Any]:
        """Adopt a guarded runtime config into a panel that is already serving."""

        from agent_commons.services.delegation_runtime import load_runtime_configuration

        target = Path(path).expanduser()
        configuration = load_runtime_configuration(target, workspace_root=self.repo)
        with self._guard:
            self._profile_config = target
            self._profile_info = None
            if configuration.catalog_path is not None:
                self._catalog_path = configuration.catalog_path
        self.invalidate()
        return {
            "profiles": [str(profile_id) for profile_id in configuration.profiles.profile_ids],
            "launch_enabled": self.launch_enabled,
            "catalog_editing_enabled": self.catalog_editing_enabled,
        }

    def setup_preflight(self) -> dict[str, Any]:
        """Run the credential-free compatibility check over configured profiles."""

        from agent_commons.runtime.preflight import preflight_profile
        from agent_commons.services.delegation_runtime import load_runtime_configuration
        from agent_commons.ui import setup

        if self._profile_config is None:
            raise setup.SetupError(
                setup.SETUP_UNCONFIGURED,
                "there is no operator runtime config to check yet; the check runs "
                "against the profiles the first-run screen writes",
            )
        configuration = load_runtime_configuration(self._profile_config, workspace_root=self.repo)
        state_root = self.paths().state_root
        results = [
            preflight_profile(
                configuration.profiles,
                profile_id,
                workspace_root=self.repo,
                state_root=state_root,
            )
            for profile_id in configuration.profiles.profile_ids
        ]
        return {
            "credential_free": True,
            "note": PREFLIGHT_CREDENTIAL_FREE,
            "ok": all(bool(result["ok"]) for result in results),
            "profiles": results,
        }

    def _require_catalog_editing(self) -> Path:
        if self._catalog_path is None:
            raise ConfigurationError(
                "this panel has no operator catalogue configured, so there is nothing to edit"
            )
        if not self.writes_enabled:
            raise ConfigurationError(
                "this panel holds no operator session; it shows the catalogue and changes nothing"
            )
        return self._catalog_path

    def _catalog_users(self, section: str, entry_id: str) -> list[str]:
        """Active roles that would break if this entry disappeared."""

        field = "skills" if section == "skills" else "tool_allowlist"
        return sorted(
            str(record["id"])
            for record in self.manager().snapshot().agents.values()
            if record.get("state") == "active" and entry_id in (record.get(field) or ())
        )

    def save_catalog_entry(
        self,
        *,
        section: str,
        entry_id: str,
        title: str,
        description: str = "",
        instruction: str | None = None,
    ) -> dict[str, Any]:
        path = self._require_catalog_editing()
        if section not in CATALOG_SECTIONS:
            raise ValidationError(f"unknown catalogue section: {section}")
        catalogue = load_role_catalog(path, workspace_root=self.repo)
        entry: dict[str, str] = {"id": entry_id, "title": title, "description": description}
        if section == "skills":
            entry["instruction"] = instruction or ""
        remaining = [item for item in catalogue[section] if item["id"] != entry_id]
        catalogue[section] = sorted([*remaining, entry], key=lambda item: item["id"])
        write_role_catalog(path, catalogue, workspace_root=self.repo)
        return {"section": section, "entry": entry, "catalog_path": str(path)}

    def remove_catalog_entry(self, *, section: str, entry_id: str) -> dict[str, Any]:
        path = self._require_catalog_editing()
        if section not in CATALOG_SECTIONS:
            raise ValidationError(f"unknown catalogue section: {section}")
        users = self._catalog_users(section, entry_id)
        if users:
            raise ValidationError(f"{entry_id} is required by active roles: " + ", ".join(users))
        catalogue = load_role_catalog(path, workspace_root=self.repo)
        catalogue[section] = [item for item in catalogue[section] if item["id"] != entry_id]
        write_role_catalog(path, catalogue, workspace_root=self.repo)
        return {"section": section, "removed": entry_id, "catalog_path": str(path)}

    def _check_role_selection(
        self,
        profile_id: str | None,
        skills: Any,
        tool_allowlist: Any,
    ) -> None:
        """Refuse a selection the next launch would refuse, at click time."""

        selected_skills = tuple(str(name) for name in skills or ())
        if selected_skills and self._catalog_path is not None:
            from agent_commons.catalog import catalog_ids

            known = catalog_ids(
                load_role_catalog(self._catalog_path, workspace_root=self.repo), "skills"
            )
            missing = sorted(set(selected_skills) - known)
            if missing:
                raise ValidationError(
                    "these skills are not in the operator catalogue: " + ", ".join(missing)
                )
        selected_tools = tuple(str(name) for name in tool_allowlist or ())
        if selected_tools and profile_id:
            summary = profile_tool_summary().get(str(profile_id))
            if summary is not None:
                available = (
                    set(summary["fixed"])
                    | set(summary["narrowable"])
                    | set(summary["grant_tools"].values())
                )
                outside = sorted(set(selected_tools) - available)
                if outside:
                    raise ValidationError(
                        "role tool selection is not part of this profile: " + ", ".join(outside)
                    )

    def create_agent(self, *, from_preset_id: str | None = None, **fields: Any) -> dict[str, Any]:
        """Hire a role, once, with the model it will run on for its whole life."""

        manager = self.writer()
        fields["model"] = self._chosen_model(fields.get("model"))
        if from_preset_id:
            preset = manager.get_agent(from_preset_id)
            if not preset.get("template"):
                raise ValidationError("from_preset_id must name a role preset")
            for key in ("profile_id", "context_mode", "grants", "turnover_budget"):
                if not fields.get(key):
                    fields[key] = preset.get(key)
            for key in ("skills", "tool_allowlist"):
                if not fields.get(key):
                    fields[key] = tuple(preset.get(key) or ())
            if fields["model"] is None:
                fields["model"] = role_model(preset)
        self._check_role_selection(
            fields.get("profile_id"), fields.get("skills"), fields.get("tool_allowlist")
        )
        return manager.create_agent(**fields)

    @staticmethod
    def _chosen_model(value: Any) -> str | None:
        """Normalize an optional form model, with a refusal the operator can act on."""

        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        try:
            return validate_model_name(text)
        except ValidationError as exc:
            raise ValidationError(MODEL_NAME_REFUSED) from exc

    def answer_operation(
        self, *, operation_id: str, answer: Mapping[str, Any], idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Answer a live request. Bounded metadata only; never a secret."""

        from agent_commons.services.communication import CommunicationRuntimeService

        if not isinstance(answer, Mapping) or not answer:
            raise ValidationError("an answer needs at least one field")
        session_ids = self.writer_session_ids
        manager = self._writer_bound(session_ids[-1]) if session_ids else self.writer()
        service = CommunicationRuntimeService(manager, session_lineage=session_ids)
        return service.reply_to_input(
            operation_id,
            idempotency_key=idempotency_key or f"ui-reply-{operation_id}",
            answer=dict(answer),
        )

    def open_engagement(self, **fields: Any) -> dict[str, Any]:
        return self.writer().open_engagement(**fields)

    def say_in_engagement(
        self,
        *,
        thread_id: str,
        expected_revision: str,
        body: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if not body.strip():
            raise ValidationError("a message needs a body")
        return self.writer().reply_thread(
            thread_id, expected_revision, body=body, idempotency_key=idempotency_key
        )

    def approve_agent_proposal(self, *, thread_id: str, **fields: Any) -> dict[str, Any]:
        return self.writer().approve_agent_proposal(thread_id, **fields)

    def decline_agent_proposal(self, *, thread_id: str, **fields: Any) -> dict[str, Any]:
        return self.writer().decline_agent_proposal(thread_id, **fields)

    def reconfigure_agent(self, *, agent_id: str, **fields: Any) -> dict[str, Any]:
        expected_revision = fields.pop("expected_revision")
        manager = self.writer()
        changes = fields.get("changes") or {}
        if "skills" in changes or "tool_allowlist" in changes:
            record = manager.get_agent(agent_id)
            self._check_role_selection(
                record.get("profile_id"),
                changes.get("skills"),
                changes.get("tool_allowlist"),
            )
        return manager.reconfigure_agent(agent_id, expected_revision, **fields)

    def create_task(self, **fields: Any) -> dict[str, Any]:
        """Record one task through the same manager the CLI uses."""

        return self.writer().create_task(**fields)

    def revise_task(self, *, task_id: str, **fields: Any) -> dict[str, Any]:
        """Revise task content through the canonical immutable event path."""

        expected_revision = fields.pop("expected_revision")
        return self.writer().revise_task(task_id, expected_revision, **fields)

    _REVIEW_WALK: dict[str, tuple[str, ...]] = {
        "ready": ("start_task", "complete_task", "submit_task"),
        "assigned": ("start_task", "complete_task", "submit_task"),
        "active": ("complete_task", "submit_task"),
        "completed": ("submit_task",),
        "review": (),
    }
    _WALK_SUMMARY = "the operator sent finished work for review from the panel"
    _WALK_SUMMARY_UNRUN = (
        "the operator judged this work done and sent it for review from the panel; "
        "no run had finished on it"
    )

    @staticmethod
    def _step_key(idempotency_key: str | None, step: str) -> str | None:
        """Derive one idempotency key per recorded review-walk event."""

        return None if not idempotency_key else f"{idempotency_key}:{step}"

    @staticmethod
    def _task_has_finished_run(manager: CommonsManager, task_id: str) -> bool:
        """Whether a delegation on this task actually reached ``succeeded``."""

        target = {"kind": "task", "id": task_id}
        return any(
            record.get("state") == "succeeded" and record.get("target_ref") == target
            for record in manager.snapshot().delegations.values()
        )

    @staticmethod
    def _task_or_refuse(manager: CommonsManager, task_id: str) -> Mapping[str, Any]:
        record = manager.snapshot().tasks.get(task_id)
        if record is None:
            raise ValidationError(f"no such task: {task_id}")
        return record

    def request_task_review(
        self,
        *,
        task_id: str,
        expected_revision: str,
        criteria: Any = (),
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Send finished work for independent review, from its current task state."""

        manager = self.writer()
        record = self._task_or_refuse(manager, task_id)
        state = str(record.get("state", ""))
        if state == "blocked":
            raise ValidationError(
                "this task is blocked; unblock it before sending the work for review"
            )
        walk = self._REVIEW_WALK.get(state)
        if walk is None:
            raise ValidationError(
                f"this task is {state or 'in an unknown state'}; "
                "there is nothing to send for review"
            )
        if not walk:
            current = {str(record.get("revision")), str(record.get("effective_revision") or "")}
            if str(expected_revision) not in current:
                raise LifecycleConflictError(
                    f"stale expected revision {expected_revision}; "
                    f"current revision is {record.get('revision')}"
                )
        steps: list[str] = []
        revision = str(expected_revision)
        summary = (
            self._WALK_SUMMARY
            if self._task_has_finished_run(manager, task_id)
            else self._WALK_SUMMARY_UNRUN
        )
        for step in walk:
            arguments: dict[str, Any] = {"idempotency_key": self._step_key(idempotency_key, step)}
            if step in {"complete_task", "submit_task"}:
                arguments["summary"] = summary
            getattr(manager, step)(task_id, revision, **arguments)
            steps.append(step)
            record = self._task_or_refuse(manager, task_id)
            revision = str(record.get("revision"))
        target_revision = str(record.get("effective_revision") or record.get("revision"))
        chosen = tuple(str(item) for item in criteria or () if str(item).strip())
        if not chosen:
            chosen = tuple(str(item) for item in record.get("acceptance_criteria") or ())
        if not chosen:
            chosen = ("the work meets the task description",)
        review = manager.request_review(
            target_ref={"kind": "task", "id": task_id},
            target_revision=target_revision,
            criteria=chosen,
            independent=True,
            idempotency_key=self._step_key(idempotency_key, "request_review"),
        )
        steps.append("request_review")
        return {
            "task_id": task_id,
            "task_state": str(record.get("state", "")),
            "task_revision": target_revision,
            "review_id": str(review["entity_ref"]["id"]),
            "review_revision": str(review["revision"]),
            "steps": steps,
        }

    def accept_task(
        self,
        *,
        task_id: str,
        expected_revision: str,
        summary: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Accept work; qualifying-review selection stays in the domain."""

        if not summary.strip():
            raise ValidationError("an acceptance needs a summary of what you accepted")
        return self.writer().accept_task(
            task_id,
            expected_revision,
            summary=summary,
            idempotency_key=idempotency_key,
        )

    def reopen_task(
        self,
        *,
        task_id: str,
        expected_revision: str,
        reason: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Send work back through the canonical task transition."""

        if not reason.strip():
            raise ValidationError("sending work back needs a reason the role can act on")
        return self.writer().reopen_task(
            task_id,
            expected_revision,
            reason=reason,
            idempotency_key=idempotency_key,
        )

    def open_agent_link(self, **fields: Any) -> dict[str, Any]:
        return self.writer().open_agent_link(**fields)

    def close_agent_link(self, *, link_id: str, **fields: Any) -> dict[str, Any]:
        expected_revision = fields.pop("expected_revision")
        return self.writer().close_agent_link(link_id, expected_revision, **fields)

    def retire_agent(self, *, agent_id: str, **fields: Any) -> dict[str, Any]:
        expected_revision = fields.pop("expected_revision", None)
        return self.writer().retire_agent(agent_id, expected_revision, **fields)

    def message_agent(
        self,
        *,
        agent_id: str,
        body_text: str,
        subject: str | None = None,
        thread_id: str | None = None,
        expected_revision: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Open or reply to a durable human-to-role decision-request thread."""

        manager = self.writer()
        if not body_text.strip():
            raise ValidationError("a message needs a body")
        if thread_id:
            if not expected_revision:
                raise ValidationError("replying to a thread requires its expected_revision")
            return manager.reply_thread(
                thread_id,
                expected_revision,
                body=body_text,
                idempotency_key=idempotency_key,
            )
        opened = manager.open_thread(
            thread_type="decision_request",
            subject=subject or truncate_utf8(body_text, 200),
            desired_outcome="the role acts on this direction",
            to=(agent_id,),
            related_refs=({"kind": "agent", "id": agent_id},),
            idempotency_key=idempotency_key,
        )
        return manager.reply_thread(
            opened["entity_ref"]["id"],
            opened["revision"],
            body=body_text,
            idempotency_key=f"{opened['idempotency_key']}:body",
        )
