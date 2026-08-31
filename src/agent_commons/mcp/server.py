"""A deliberately small stdio MCP surface over :class:`CommonsManager`.

The adapter owns no persistence and contains no lifecycle rules.  Every write
delegates to ``CommonsManager`` so CLI and MCP clients share exactly one
business-logic boundary.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any, Protocol, TypeVar

from agent_commons.core.refs import parse_ref
from agent_commons.domain.agents import effective_grants
from agent_commons.domain.states import (
    LIVE_WORKER_DELEGATION_STATES,
    NON_TERMINAL_DELEGATION_STATES,
)
from agent_commons.errors import (
    ConfigurationError,
    IntegrityError,
    LifecycleConflictError,
    ValidationError,
)

# Compatibility re-exports: the console script and existing imports keep this facade.
from agent_commons.mcp.entrypoint import _parser as _parser
from agent_commons.mcp.entrypoint import main as main
from agent_commons.mcp.scoped_repo import ScopedRepoReader
from agent_commons.runtime import (
    TERMINAL_TOOL_NAMES,
    ContextBindingRequest,
    TerminalToolAuditStore,
)
from agent_commons.services import CommonsManager
from agent_commons.services.communication import CommunicationRuntimeService


class MCPServer(Protocol):
    """Minimum FastMCP-compatible surface used by this adapter and its tests."""

    def tool(self, *args: Any, **kwargs: Any) -> Callable[[Callable[..., Any]], Any]: ...

    def run(self, *, transport: str) -> None: ...


class RuntimeService(Protocol):
    def profile_summaries(self) -> list[dict[str, Any]]: ...

    def list_attempts(self) -> list[dict[str, Any]]: ...

    def run(
        self,
        delegation_id: str,
        expected_revision: str,
        *,
        idempotency_key: str,
        retry: bool = False,
        context: ContextBindingRequest | None = None,
    ) -> dict[str, Any]: ...

    def reconcile(self) -> list[dict[str, Any]]: ...


class CommunicationService(Protocol):
    def request_input(self, delegation_id: str, **values: Any) -> dict[str, Any]: ...

    def check_input(self, operation_id: str) -> dict[str, Any]: ...

    def reply_to_input(self, operation_id: str, **values: Any) -> dict[str, Any]: ...

    def share_progress(self, delegation_id: str, **values: Any) -> dict[str, Any]: ...

    def report_blocker(self, delegation_id: str, **values: Any) -> dict[str, Any]: ...

    def send_guidance(self, delegation_id: str, **values: Any) -> dict[str, Any]: ...

    def request_checkpoint(self, delegation_id: str, **values: Any) -> dict[str, Any]: ...

    def acknowledge(self, operation_id: str, **values: Any) -> dict[str, Any]: ...

    def acknowledge_control(self, operation_id: str, **values: Any) -> dict[str, Any]: ...

    def inbox(self) -> tuple[dict[str, Any], ...]: ...


ServerT = TypeVar("ServerT", bound=MCPServer)

MCP_INSTRUCTIONS = (
    "Use these tools only for the current Agent Commons workspace. Read orientation and inbox "
    "before requesting work. Delegations must target an exact revision, stay within the supplied "
    "limits, and use stable idempotency keys. A delegation grants no authority to commit, push, "
    "deploy, publish, contact people, expose secrets, or overwrite unrelated work."
)
_READ_ONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
_IDEMPOTENT_WRITE = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
_DESTRUCTIVE_WRITE = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": True,
    "openWorldHint": False,
}
_RUNTIME_WRITE = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": True,
    "openWorldHint": True,
}

# Only these exact fixed strings may be persisted as terminal-tool rejection
# details.  Any other exception text is presumed to quote tool arguments —
# jsonschema embeds the offending value in its message, so a schema-invalid
# `summary` used to write the summary's own text into the audit store, the
# exact content THREAT_MODEL promises that store never holds.  The error type
# is always recorded, so a withheld message still names the refusal class.
_FIXED_REJECTION_DETAILS = frozenset(
    {
        "worker MCP authority ended with its canonical delegation",
        "worker outcome is outside its delegation scope",
        "delegated workspace changed after reviewer snapshot creation",
        "registered review artifact changed after it was inspected",
        "bound artifact revision is stale for review",
        "review evidence is incomplete for the bound subject",
        "partial review finalization does not match the bound operation",
        "result_refs must contain at least one reference",
    }
)
_WITHHELD_REJECTION_DETAIL = "details withheld: the refusal text may quote tool arguments"


_COMMON_WORKER_TOOL_NAMES = frozenset(
    {
        "commons_orient",
        "commons_inbox",
        "commons_list_tasks",
        "commons_list_delegations",
        "commons_show_delegation",
        "commons_list_reviews",
        "commons_show_review",
        "commons_list_verifications",
        "commons_show_verification",
        "commons_show_artifact",
        "commons_read_artifact",
        "commons_delegation_input_needed",
        "commons_delegation_needs_operator",
        "commons_repo_files",
        "commons_repo_read",
        "commons_repo_search",
        "commons_request_input",
        "commons_check_input",
        "commons_share_progress",
        "commons_report_blocker",
        "commons_ack_input",
        "commons_ack_control",
        # The main chat is two-way or it is not a chat.  Both are bounded to
        # threads the acting role is addressed in.
        "commons_list_my_threads",
        "commons_reply_thread",
    }
)
IMPLEMENTATION_WORKER_TOOL_NAMES = _COMMON_WORKER_TOOL_NAMES | {"commons_succeed_delegation"}
VERIFICATION_WORKER_TOOL_NAMES = IMPLEMENTATION_WORKER_TOOL_NAMES | {"commons_record_verification"}
INDEPENDENT_REVIEW_WORKER_TOOL_NAMES = _COMMON_WORKER_TOOL_NAMES | {
    "commons_record_verification",
    "commons_finalize_review",
}


def _fastmcp_factory(name: str) -> MCPServer:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - exercised through the entrypoint
        raise ConfigurationError(
            "MCP support is not installed; install agent-commons[mcp]"
        ) from exc
    return FastMCP(name, instructions=MCP_INSTRUCTIONS)


def _review_matches_worker(review: dict[str, Any], worker: dict[str, Any]) -> bool:
    """Match a review without reading mutable operational state."""

    target = worker.get("target_ref") or {}
    if target == {"kind": "review", "id": review.get("id")}:
        return worker.get("target_revision") in {
            review.get("revision"),
            review.get("effective_revision", review.get("revision")),
            review.get("expected_revision"),
        }
    return target == review.get("target_ref") and worker.get("target_revision") == review.get(
        "target_revision"
    )


def _freeze_worker_artifact_bundles(
    commons: CommonsManager,
    worker: dict[str, Any],
    *,
    snapshot: Any | None = None,
) -> dict[str, dict[str, Any]]:
    """Bind the exact worker artifact set from one canonical snapshot.

    Artifact authorization and manifests are immutable inputs to the launch.
    Freezing them here prevents every artifact read from replaying the entire
    ledger several times. The per-tool live-delegation guard remains fresh,
    and ``ScopedRepoReader`` still hashes the actual file before returning it.
    """

    snapshot = snapshot or commons.snapshot()
    allowed: set[str] = set()
    relevant_task_ids: set[str] = set()
    target = worker.get("target_ref") or {}
    if target.get("kind") == "artifact":
        allowed.add(str(target.get("id")))
    if target.get("kind") == "task":
        relevant_task_ids.add(str(target.get("id")))

    for review_record in snapshot.reviews.values():
        review = dict(review_record)
        if not _review_matches_worker(review, worker):
            continue
        review_target = review.get("target_ref") or {}
        if review_target.get("kind") == "artifact":
            allowed.add(str(review_target.get("id")))
        if review_target.get("kind") == "task":
            relevant_task_ids.add(str(review_target.get("id")))
        for ref in review.get("evidence_refs") or ():
            if ref.get("kind") == "artifact":
                allowed.add(str(ref.get("id")))

    for task_id in relevant_task_ids:
        task_record = snapshot.tasks.get(task_id)
        if task_record is None:
            continue
        for ref in task_record.get("artifact_refs") or ():
            if ref.get("kind") == "artifact":
                allowed.add(str(ref.get("id")))

    bundles: dict[str, dict[str, Any]] = {}
    for artifact_id in sorted(allowed):
        current = snapshot.artifacts.get(artifact_id)
        if current is None:
            raise LifecycleConflictError(f"artifact does not exist: {artifact_id}")
        manifest_ref = str(current.get("manifest_ref", ""))
        try:
            manifest = commons.manifests.get(manifest_ref).manifest
        except FileNotFoundError as exc:
            raise IntegrityError(f"artifact {artifact_id} references a missing manifest") from exc
        if manifest.get("artifact_id") != artifact_id:
            raise IntegrityError("artifact manifest identity does not match its projection")
        bundles[artifact_id] = {
            "artifact": dict(current),
            "manifest": dict(manifest),
        }
    return bundles


def build_server(
    repo_root: str | Path,
    *,
    session_id: str | None = None,
    manager: CommonsManager | None = None,
    runtime: RuntimeService | None = None,
    communication: CommunicationService | None = None,
    enable_controls: bool = True,
    delegation_id: str | None = None,
    catalog_only_purpose: str | None = None,
    binding_wait_seconds: float = 5.0,
    git_executable: str = "/usr/bin/git",
    server_factory: Callable[[str], ServerT] | None = None,
) -> ServerT | MCPServer:
    """Build a local stdio server with an intentionally bounded tool set."""

    commons = manager or CommonsManager(repo_root, session_id=session_id)
    if catalog_only_purpose not in {None, "implementation", "independent_review", "verification"}:
        raise ConfigurationError("MCP catalog-only purpose is invalid")
    if catalog_only_purpose is not None and not commons.read_only:
        raise ConfigurationError("MCP catalog-only handshake requires read-only state")
    communication_service = communication
    if (
        communication_service is None
        and isinstance(commons, CommonsManager)
        and catalog_only_purpose is None
    ):
        communication_service = CommunicationRuntimeService(commons)
    factory = server_factory or _fastmcp_factory
    server = factory("agent-commons")
    active_session_id = getattr(commons, "session_id", None)
    requested_binding = delegation_id or os.environ.get("AGENT_COMMONS_DELEGATION_ID")
    binding_snapshot: Any | None = None

    def live_snapshot() -> Any:
        # The synchronized SQLite projection is disposable operational state:
        # `_read_snapshot` verifies/syncs it against immutable events and
        # rebuilds or falls back when it is stale or missing. This keeps MCP
        # initialization below provider handshake bounds without making the
        # projection authoritative.
        if isinstance(commons, CommonsManager):
            return commons._read_snapshot(fresh=False)[0]
        return commons.snapshot()

    worker: dict[str, Any] | None = (
        {
            "id": "delegation.preflight",
            "purpose": catalog_only_purpose,
            "target_ref": {"kind": "task", "id": "task.preflight"},
            "target_revision": "evt.preflight",
        }
        if catalog_only_purpose is not None
        else None
    )
    if catalog_only_purpose is None and requested_binding is not None:
        if active_session_id is None:
            raise ConfigurationError("delegated MCP binding requires an active child session")
        if binding_wait_seconds < 0 or binding_wait_seconds > 30:
            raise ConfigurationError("delegated MCP binding wait must be between 0 and 30 seconds")
        deadline = time.monotonic() + binding_wait_seconds
        while True:
            binding_snapshot = live_snapshot()
            candidate_record = binding_snapshot.delegations.get(requested_binding)
            if candidate_record is None:
                raise LifecycleConflictError(f"delegation does not exist: {requested_binding}")
            candidate = dict(candidate_record)
            state = candidate.get("state")
            child_session_id = candidate.get("child_session_id")
            if state in LIVE_WORKER_DELEGATION_STATES and child_session_id == active_session_id:
                worker = candidate
                break
            if (
                child_session_id not in {None, active_session_id}
                or state not in NON_TERMINAL_DELEGATION_STATES
            ):
                raise ConfigurationError(
                    "delegated MCP binding does not match its live canonical child"
                )
            if time.monotonic() >= deadline:
                raise ConfigurationError(
                    "delegated MCP binding was not canonically started before the deadline"
                )
            time.sleep(0.01)
    elif catalog_only_purpose is None:
        binding_snapshot = live_snapshot() if isinstance(commons, CommonsManager) else None
        candidates = (
            binding_snapshot.delegations.values()
            if binding_snapshot is not None
            else commons.list_delegations(state=None)
        )
        worker_matches = [
            dict(candidate)
            for candidate in candidates
            if active_session_id is not None
            and candidate.get("child_session_id") == active_session_id
            and candidate.get("state") in LIVE_WORKER_DELEGATION_STATES
        ]
        if len(worker_matches) > 1:
            raise ConfigurationError("one child session cannot own multiple active delegations")
        worker = worker_matches[0] if worker_matches else None
    workspace = (
        ScopedRepoReader(commons, git_executable=git_executable)
        if worker is not None and catalog_only_purpose is None
        else None
    )
    worker_artifact_bundles = (
        _freeze_worker_artifact_bundles(commons, worker, snapshot=binding_snapshot)
        if worker is not None and catalog_only_purpose is None
        else None
    )
    worker_read_artifact_manifests: dict[str, str] = {}
    worker_reviews: tuple[dict[str, Any], ...] = ()
    worker_tasks: tuple[dict[str, Any], ...] = ()
    worker_verifications: tuple[dict[str, Any], ...] = ()
    worker_verification_binding: tuple[dict[str, Any], str] | None = None
    if worker is not None and catalog_only_purpose is None:
        evidence_snapshot = binding_snapshot or live_snapshot()
        worker_reviews = tuple(
            dict(review)
            for review in evidence_snapshot.reviews.values()
            if _review_matches_worker(dict(review), worker)
        )
        purpose = worker.get("purpose")
        if purpose == "verification":
            worker_verification_binding = (
                dict(worker.get("target_ref") or {}),
                str(worker.get("target_revision")),
            )
        elif purpose == "independent_review":
            direct_target = dict(worker.get("target_ref") or {})
            if direct_target.get("kind") != "review":
                worker_verification_binding = (
                    direct_target,
                    str(worker.get("target_revision")),
                )
            elif worker_reviews:
                review = worker_reviews[0]
                worker_verification_binding = (
                    dict(review.get("target_ref") or {}),
                    str(review.get("target_revision")),
                )

        task_ids: set[str] = set()
        direct_target = worker.get("target_ref") or {}
        if direct_target.get("kind") == "task":
            task_ids.add(str(direct_target.get("id")))
        for review in worker_reviews:
            review_target = review.get("target_ref") or {}
            if review_target.get("kind") == "task":
                task_ids.add(str(review_target.get("id")))
        worker_tasks = tuple(
            dict(task)
            for task_id in sorted(task_ids)
            if (task := evidence_snapshot.tasks.get(task_id)) is not None
        )
        if worker_verification_binding is not None:
            verification_target, verification_revision = worker_verification_binding
            worker_verifications = tuple(
                dict(verification)
                for verification in evidence_snapshot.verifications.values()
                if verification.get("target_ref") == verification_target
                and verification.get("target_revision") == verification_revision
            )
    terminal_audit = (
        TerminalToolAuditStore(
            commons.paths.state_root,
            security_policy=commons.policy,
            read_only=commons.read_only,
        )
        if worker is not None and catalog_only_purpose is None
        else None
    )

    acting_agent_id = str((worker or {}).get("agent_id") or "") or None
    # Effective, so a level lowered on any creator above this role also removes
    # the tool from this session rather than only failing when it is called.
    acting_grants = (
        effective_grants(
            (binding_snapshot or commons.snapshot()).agents,
            acting_agent_id,
        )
        if acting_agent_id
        else {}
    )

    def acting_grant(name: str, level: str) -> bool:
        """A staff tool appears only at the exact level that can use it.

        Registering the recording tool at `ask` would hand the role something
        that always refuses; registering the proposing tool at `auto` would ask
        a person for something the role was already trusted to do.
        """

        return acting_grants.get(name, "deny") == level

    def require_live_worker() -> dict[str, Any] | None:
        if worker is None:
            return None
        current_record = live_snapshot().delegations.get(str(worker.get("id")))
        if current_record is None:
            raise LifecycleConflictError("worker MCP authority ended with its canonical delegation")
        current = dict(current_record)
        if (
            current.get("state") not in LIVE_WORKER_DELEGATION_STATES
            or current.get("child_session_id") != active_session_id
        ):
            raise LifecycleConflictError("worker MCP authority ended with its canonical delegation")
        return current

    def require_communication() -> CommunicationService:
        if communication_service is None:
            raise ConfigurationError("task-scoped communication service is unavailable")
        return communication_service

    def register(
        annotations: dict[str, bool],
        *,
        root_only: bool = False,
        worker_only: bool = False,
        worker_purposes: tuple[str, ...] = (),
        enabled: bool = True,
    ) -> Callable[[Callable[..., Any]], Any]:
        def decorator(function: Callable[..., Any]) -> Any:
            if not enabled or (
                (root_only and worker is not None)
                or (worker_only and worker is None)
                or (
                    worker is not None
                    and worker_purposes
                    and worker.get("purpose") not in worker_purposes
                )
            ):
                return function
            registered = function
            if worker is not None:

                @wraps(function)
                def guarded(*args: Any, **kwargs: Any) -> Any:
                    terminal = function.__name__ in TERMINAL_TOOL_NAMES
                    delegation = str(worker.get("id"))
                    if terminal and terminal_audit is not None:
                        try:
                            terminal_audit.record(delegation, function.__name__, "called")
                        except Exception:
                            pass
                    try:
                        require_live_worker()
                        result = function(*args, **kwargs)
                    except Exception as exc:
                        if terminal and terminal_audit is not None:
                            rendered = str(exc)
                            if rendered not in _FIXED_REJECTION_DETAILS:
                                rendered = _WITHHELD_REJECTION_DETAIL
                            try:
                                terminal_audit.record(
                                    delegation,
                                    function.__name__,
                                    "rejected",
                                    error_type=type(exc).__name__,
                                    message=rendered,
                                )
                            except Exception:
                                pass
                        raise
                    if terminal and terminal_audit is not None:
                        try:
                            terminal_audit.record(delegation, function.__name__, "completed")
                        except Exception:
                            pass
                    return result

                registered = guarded
            return server.tool(annotations=annotations)(registered)

        return decorator

    def relevant_review(review: dict[str, Any]) -> bool:
        if worker is None:
            return True
        return _review_matches_worker(review, worker)

    def relevant_artifact_ids() -> set[str]:
        if worker is None:
            return {str(item.get("id")) for item in commons.list_artifacts()}
        if worker_artifact_bundles is not None:
            return set(worker_artifact_bundles)
        allowed: set[str] = set()
        target = worker.get("target_ref") or {}
        if target.get("kind") == "artifact":
            allowed.add(str(target.get("id")))
        relevant_task_ids: set[str] = set()
        if target.get("kind") == "task":
            relevant_task_ids.add(str(target.get("id")))
        for review in commons.list_reviews(state=None):
            if not relevant_review(review):
                continue
            review_target = review.get("target_ref") or {}
            if review_target.get("kind") == "task":
                relevant_task_ids.add(str(review_target.get("id")))
            for ref in review.get("evidence_refs") or ():
                if ref.get("kind") == "artifact":
                    allowed.add(str(ref.get("id")))
        for task in commons.list_tasks(state=None):
            if task.get("id") not in relevant_task_ids:
                continue
            for ref in task.get("artifact_refs") or ():
                if ref.get("kind") == "artifact":
                    allowed.add(str(ref.get("id")))
        return allowed

    def worker_verification_target() -> tuple[dict[str, Any], str] | None:
        return worker_verification_binding

    def relevant_verification(verification: dict[str, Any]) -> bool:
        if worker is None:
            return True
        target = worker_verification_target()
        return target is not None and (
            verification.get("target_ref") == target[0]
            and verification.get("target_revision") == target[1]
        )

    @register(_READ_ONLY)
    def commons_orient(max_items: int = 20) -> dict[str, Any]:
        """Return the current role-filtered workspace brief."""

        if worker is None:
            return commons.orient(max_items=max_items)
        return {
            "session_id": active_session_id,
            "delegation": worker,
            "reviews": list(worker_reviews[:max_items]),
            "verifications": list(worker_verifications[:max_items]),
        }

    @register(_READ_ONLY)
    def commons_inbox(max_items: int = 20) -> dict[str, Any]:
        """Return open discussions and handoffs addressed to this session."""

        if worker is not None:
            operations = (
                list(require_communication().inbox())[:max_items]
                if communication_service is not None
                else []
            )
            return {
                "delegation": worker,
                "threads": [],
                "handoffs": [],
                "operations": operations,
            }
        result = commons.inbox(max_items=max_items)
        return {
            **result,
            "operations": (
                list(require_communication().inbox())[:max_items]
                if communication_service is not None
                else []
            ),
        }

    @register(_IDEMPOTENT_WRITE, worker_only=True)
    def commons_request_input(
        delegation_id: str,
        idempotency_key: str,
        question: str,
        why_needed: str,
        safe_context: dict[str, Any],
        desired_outcome: str,
        blocking: bool = True,
        deadline_seconds: int = 900,
    ) -> dict[str, Any]:
        """Ask the canonical parent one bounded, correlated task question."""

        if worker is not None and delegation_id != worker.get("id"):
            raise LifecycleConflictError("worker input request is outside its delegation scope")
        return require_communication().request_input(
            delegation_id,
            idempotency_key=idempotency_key,
            question=question,
            why_needed=why_needed,
            safe_context=safe_context,
            desired_outcome=desired_outcome,
            blocking=blocking,
            deadline_seconds=deadline_seconds,
        )

    @register(_READ_ONLY)
    def commons_check_input(operation_id: str) -> dict[str, Any]:
        """Check one visible correlated operation without an existence oracle."""

        return require_communication().check_input(operation_id)

    @register(_IDEMPOTENT_WRITE, root_only=True)
    def commons_reply_to_input(
        operation_id: str,
        idempotency_key: str,
        answer: dict[str, Any],
    ) -> dict[str, Any]:
        """Reply as the canonical parent; answer content stays operational-only."""

        return require_communication().reply_to_input(
            operation_id,
            idempotency_key=idempotency_key,
            answer=answer,
        )

    @register(_IDEMPOTENT_WRITE, worker_only=True)
    def commons_share_progress(
        delegation_id: str,
        idempotency_key: str,
        summary: str,
        completed_units: int | None = None,
        total_units: int | None = None,
        deadline_seconds: int = 900,
    ) -> dict[str, Any]:
        """Share a bounded task-relevant progress record with the parent."""

        if worker is not None and delegation_id != worker.get("id"):
            raise LifecycleConflictError("worker progress is outside its delegation scope")
        return require_communication().share_progress(
            delegation_id,
            idempotency_key=idempotency_key,
            summary=summary,
            completed_units=completed_units,
            total_units=total_units,
            deadline_seconds=deadline_seconds,
        )

    @register(_READ_ONLY, worker_only=True)
    def commons_list_my_threads() -> list[dict[str, Any]]:
        """Conversations this role is addressed in, including the main chat."""

        reachable = {"*", active_session_id} | ({acting_agent_id} if acting_agent_id else set())
        return [
            {
                "thread_id": str(thread["id"]),
                "revision": thread.get("revision"),
                "thread_type": thread.get("thread_type"),
                "subject": thread.get("subject"),
                "desired_outcome": thread.get("desired_outcome"),
                "state": thread.get("state"),
                "messages": [dict(item) for item in thread.get("messages") or ()],
            }
            for thread in commons.list_threads(state="open")
            if {str(item) for item in thread.get("to") or ()} & reachable
        ]

    @register(_IDEMPOTENT_WRITE, worker_only=True)
    def commons_reply_thread(
        thread_id: str,
        expected_revision: str,
        body: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Reply in a conversation this role is addressed in.

        This is how feedback reaches the person who started the work.  The
        domain refuses a thread this role was not addressed in, so the tool
        cannot become a way to write into every conversation in the workspace.
        """

        return commons.reply_thread(
            thread_id,
            expected_revision,
            body=body,
            idempotency_key=idempotency_key,
        )

    @register(_IDEMPOTENT_WRITE, worker_only=True)
    def commons_report_blocker(
        delegation_id: str,
        idempotency_key: str,
        summary: str,
        impact: str,
        safe_next_action: str,
        deadline_seconds: int = 900,
    ) -> dict[str, Any]:
        """Report one bounded blocker without creating accepted truth."""

        if worker is not None and delegation_id != worker.get("id"):
            raise LifecycleConflictError("worker blocker is outside its delegation scope")
        return require_communication().report_blocker(
            delegation_id,
            idempotency_key=idempotency_key,
            summary=summary,
            impact=impact,
            safe_next_action=safe_next_action,
            deadline_seconds=deadline_seconds,
        )

    @register(_IDEMPOTENT_WRITE, root_only=True, enabled=enable_controls)
    def commons_send_guidance(
        delegation_id: str,
        idempotency_key: str,
        instruction: str,
        rationale: str,
        expected_effect: str,
        deadline_seconds: int = 900,
    ) -> dict[str, Any]:
        """Send one bounded tactical instruction to the exact delegated child."""

        return require_communication().send_guidance(
            delegation_id,
            idempotency_key=idempotency_key,
            instruction=instruction,
            rationale=rationale,
            expected_effect=expected_effect,
            deadline_seconds=deadline_seconds,
        )

    @register(_IDEMPOTENT_WRITE, root_only=True, enabled=enable_controls)
    def commons_request_checkpoint(
        delegation_id: str,
        idempotency_key: str,
        reason: str,
        safe_boundary: str,
        expected_ack: str,
        deadline_seconds: int = 900,
    ) -> dict[str, Any]:
        """Ask the exact delegated child to acknowledge a safe checkpoint."""

        return require_communication().request_checkpoint(
            delegation_id,
            idempotency_key=idempotency_key,
            reason=reason,
            safe_boundary=safe_boundary,
            expected_ack=expected_ack,
            deadline_seconds=deadline_seconds,
        )

    @register(_IDEMPOTENT_WRITE)
    def commons_ack_input(operation_id: str, idempotency_key: str) -> dict[str, Any]:
        """Acknowledge a visible reply, progress update, or blocker exactly once."""

        return require_communication().acknowledge(
            operation_id,
            idempotency_key=idempotency_key,
        )

    @register(_IDEMPOTENT_WRITE, worker_only=True, enabled=enable_controls)
    def commons_ack_control(operation_id: str, idempotency_key: str) -> dict[str, Any]:
        """Acknowledge one parent guidance or checkpoint exactly once."""

        return require_communication().acknowledge_control(
            operation_id,
            idempotency_key=idempotency_key,
        )

    @register(_READ_ONLY)
    def commons_list_tasks(state: str | None = None) -> list[dict[str, Any]]:
        """List projected tasks, optionally filtered by lifecycle state."""

        if worker is None:
            return commons.list_tasks(state=state)
        return [task for task in worker_tasks if state is None or task.get("state") == state]

    @register(_READ_ONLY)
    def commons_list_delegations(state: str | None = None) -> list[dict[str, Any]]:
        """List canonical delegation records, optionally filtered by state."""

        if worker is not None:
            return [worker] if state is None or worker.get("state") == state else []
        return commons.list_delegations(state=state)

    @register(_READ_ONLY)
    def commons_show_delegation(delegation_id: str) -> dict[str, Any]:
        """Return one canonical delegation projection."""

        if worker is not None and delegation_id != worker.get("id"):
            raise LifecycleConflictError("worker may inspect only its bound delegation")
        if worker is not None:
            current = live_snapshot().delegations.get(delegation_id)
            if current is None:
                raise LifecycleConflictError(f"delegation does not exist: {delegation_id}")
            return dict(current)
        return commons.get_delegation(delegation_id)

    @register(_READ_ONLY)
    def commons_list_reviews(state: str | None = None) -> list[dict[str, Any]]:
        """List revision-bound reviews, optionally filtered by lifecycle state."""

        if worker is None:
            return commons.list_reviews(state=state)
        return [
            review for review in worker_reviews if state is None or review.get("state") == state
        ]

    @register(_READ_ONLY)
    def commons_show_review(review_id: str) -> dict[str, Any]:
        """Return one projected review without exposing an unbounded query surface."""

        reviews = commons.list_reviews(state=None) if worker is None else worker_reviews
        review = next((item for item in reviews if item.get("id") == review_id), None)
        if review is None:
            if worker is not None:
                raise LifecycleConflictError("worker may inspect only its bound review")
            raise LifecycleConflictError(f"review does not exist: {review_id}")
        if not relevant_review(review):
            raise LifecycleConflictError("worker may inspect only its bound review")
        return review

    @register(_READ_ONLY, worker_only=True)
    def commons_list_verifications() -> list[dict[str, Any]]:
        """List reproducible checks relevant to the exact worker target."""

        return list(worker_verifications)

    @register(_READ_ONLY, worker_only=True)
    def commons_show_verification(verification_id: str) -> dict[str, Any]:
        """Return one target-scoped verification without widening worker access."""

        verification = next(
            (item for item in worker_verifications if item.get("id") == verification_id), None
        )
        if verification is None:
            raise LifecycleConflictError(f"verification does not exist: {verification_id}")
        if not relevant_verification(verification):
            raise LifecycleConflictError(
                "worker may inspect only a verification of its exact target"
            )
        return verification

    @register(_READ_ONLY)
    def commons_show_artifact(artifact_id: str) -> dict[str, Any]:
        """Show one in-scope artifact and its integrity-checked manifest metadata."""

        if artifact_id not in relevant_artifact_ids():
            raise LifecycleConflictError("worker may inspect only a bound task artifact")
        if worker_artifact_bundles is not None:
            return worker_artifact_bundles[artifact_id]
        return commons.get_artifact_bundle(artifact_id)

    @register(_READ_ONLY, worker_only=True)
    def commons_read_artifact(artifact_id: str) -> dict[str, Any]:
        """Read one exact UTF-8 evidence artifact after manifest hash verification."""

        if workspace is None:  # pragma: no cover - tool is registered only for workers
            raise LifecycleConflictError("workspace snapshot is unavailable")
        if artifact_id not in relevant_artifact_ids():
            raise LifecycleConflictError("worker may read only a bound task artifact")
        bundle = (
            worker_artifact_bundles[artifact_id]
            if worker_artifact_bundles is not None
            else commons.get_artifact_bundle(artifact_id)
        )
        manifest = bundle["manifest"]
        source = manifest.get("source") or {}
        result = workspace.read_registered_artifact(
            source_path=str(source.get("path", "")),
            expected_revision=str(manifest.get("revision", "")),
            expected_size=int(manifest.get("size_bytes", -1)),
        )
        worker_read_artifact_manifests[artifact_id] = str(bundle["artifact"]["manifest_ref"])
        return result

    # -- staff changes ------------------------------------------------------
    # Each of the three tools below is registered only when the standing role
    # this run acts for holds the matching grant above `deny`.  The grant is the
    # switch, so a run with no role -- or a role that may not change staff --
    # never sees the tool at all.  The domain still refuses on its own; this is
    # least privilege in front of that, not instead of it.

    @register(_IDEMPOTENT_WRITE, worker_only=True, enabled=acting_grant("create_roles", "auto"))
    def commons_create_agent(
        name: str,
        profile_id: str,
        rationale: str,
        idempotency_key: str,
        context_mode: str = "fresh",
        create_roles: str = "deny",
        retire_roles: str = "deny",
        open_links: str = "deny",
        turnover_budget: int | None = None,
        retire_with_task_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a standing role below this one, with strictly narrower authority.

        Records the rationale, the creating role, and the lineage permanently:
        somebody has to be able to ask where a role came from months later.
        """

        lifetime = (
            {"kind": "task_scoped", "task_id": retire_with_task_id}
            if retire_with_task_id
            else {"kind": "persistent"}
        )
        return commons.create_agent(
            name=name,
            profile_id=profile_id,
            rationale=rationale,
            context_mode=context_mode,
            grants={
                "create_roles": create_roles,
                "retire_roles": retire_roles,
                "open_links": open_links,
            },
            turnover_budget=turnover_budget,
            lifetime=lifetime,
            created_by_agent_id=acting_agent_id,
            idempotency_key=idempotency_key,
        )

    @register(_IDEMPOTENT_WRITE, worker_only=True, enabled=acting_grant("create_roles", "ask"))
    def commons_propose_agent(
        name: str,
        profile_id: str,
        rationale: str,
        idempotency_key: str,
        context_mode: str = "fresh",
        create_roles: str = "deny",
        retire_roles: str = "deny",
        open_links: str = "deny",
        turnover_budget: int | None = None,
    ) -> dict[str, Any]:
        """Ask a person for a role this role may not record itself.

        The proposal is an ordinary typed thread: it lands in the same inbox and
        the same panel as everything else needing a human, and it grants nothing
        until somebody confirms it.  Confirming records the proposal's own
        fields and credits this role, so what the ledger says was asked for is
        what was asked for.
        """

        return commons.propose_agent(
            name=name,
            profile_id=profile_id,
            rationale=rationale,
            context_mode=context_mode,
            grants={
                "create_roles": create_roles,
                "retire_roles": retire_roles,
                "open_links": open_links,
            },
            turnover_budget=turnover_budget,
            idempotency_key=idempotency_key,
        )

    @register(_DESTRUCTIVE_WRITE, worker_only=True, enabled=acting_grant("retire_roles", "auto"))
    def commons_retire_agent(
        agent_id: str,
        reason: str,
        idempotency_key: str,
        cascade: bool = False,
    ) -> dict[str, Any]:
        """Take a role below this one out of service; nothing is deleted.

        Refused for a human-created role at any level, and for any role that
        still owes a live delegation or an unfinished review.
        """

        return commons.retire_agent(
            agent_id,
            reason=reason,
            cascade=cascade,
            idempotency_key=idempotency_key,
        )

    @register(_IDEMPOTENT_WRITE, worker_only=True, enabled=acting_grant("open_links", "auto"))
    def commons_open_agent_link(
        to_agent_id: str,
        reason: str,
        idempotency_key: str,
        deadline_seconds: int | None = None,
    ) -> dict[str, Any]:
        """Open a link to another role; it lives until explicitly closed.

        The link records the action it permits -- today, one bounded question --
        rather than an open/closed flag, so a later "hand over work" mode
        extends the enum instead of reshaping the record.  Any deadline is
        recorded intent: no reader has a clock to enforce it against.
        """

        return commons.open_agent_link(
            from_agent_id=str(acting_agent_id),
            to_agent_id=to_agent_id,
            allowed_action="ask",
            deadline_seconds=deadline_seconds,
            reason=reason,
            idempotency_key=idempotency_key,
        )

    @register(_IDEMPOTENT_WRITE, root_only=True)
    def commons_request_delegation(
        target_ref: str,
        target_revision: str,
        target_profile: str,
        purpose: str,
        idempotency_key: str,
        max_depth: int = 0,
        wall_time_seconds: int = 1800,
        max_attempts: int = 1,
        max_concurrency: int = 1,
        budget_unit: str = "provider_units",
        budget_limit: int = 1,
        parent_delegation_id: str | None = None,
    ) -> dict[str, Any]:
        """Request bounded work against an exact target revision.

        This records one leaf only: max_depth must be zero because workers do
        not receive child-delegation tools. Launching remains a separate broker
        action, and the supplied idempotency key must be stable for retries.
        """

        limits = {
            "max_depth": max_depth,
            "wall_time_seconds": wall_time_seconds,
            "max_attempts": max_attempts,
            "max_concurrency": max_concurrency,
            "budget": {"unit": budget_unit, "limit": budget_limit},
        }
        return commons.create_delegation(
            target_ref=parse_ref(target_ref).as_dict(),
            target_revision=target_revision,
            target_profile=target_profile,
            purpose=purpose,
            limits=limits,
            parent_delegation_id=parent_delegation_id,
            idempotency_key=idempotency_key,
        )

    @register(_DESTRUCTIVE_WRITE, root_only=True)
    def commons_cancel_delegation(
        delegation_id: str,
        expected_revision: str,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Cancel a non-terminal delegation using exact revision CAS."""

        if (
            runtime is not None
            and commons.get_delegation(delegation_id).get("state") != "requested"
        ):
            raise LifecycleConflictError(
                "active runtime cancellation is unavailable; stop/classify the provider first"
            )
        return commons.cancel_delegation(
            delegation_id,
            expected_revision,
            reason=reason,
            idempotency_key=idempotency_key,
        )

    @register(_DESTRUCTIVE_WRITE, root_only=True)
    def commons_recover_delegation(
        delegation_id: str,
        expected_revision: str,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Recover requested work whose original requester is unavailable.

        The active root session must declare ``delegation:recover``. The
        manager rejects live requesters and every state beyond ``requested``.
        """

        return commons.recover_delegation(
            delegation_id,
            expected_revision,
            reason=reason,
            idempotency_key=idempotency_key,
        )

    @register(_IDEMPOTENT_WRITE, root_only=True)
    def commons_complete_review(
        review_id: str,
        expected_revision: str,
        target_revision: str,
        verdict: str,
        summary: str,
        idempotency_key: str,
        evidence_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        """Complete one exact-revision review from a root/manual recovery client."""

        if worker is not None:
            if workspace is None:  # pragma: no cover - worker construction guarantees it
                raise LifecycleConflictError("workspace snapshot is unavailable")
            workspace.assert_unchanged()
            review = next(
                (item for item in commons.list_reviews(state=None) if item.get("id") == review_id),
                None,
            )
            if (
                worker.get("purpose") != "independent_review"
                or review is None
                or not relevant_review(review)
            ):
                raise LifecycleConflictError("worker review write is outside its delegation scope")
        return commons.complete_review(
            review_id,
            expected_revision,
            target_revision=target_revision,
            verdict=verdict,
            summary=summary,
            evidence_refs=tuple(parse_ref(value).as_dict() for value in evidence_refs or ()),
            idempotency_key=idempotency_key,
        )

    @register(
        _IDEMPOTENT_WRITE,
        worker_only=True,
        worker_purposes=("independent_review",),
    )
    def commons_finalize_review(
        verdict: str,
        summary: str,
    ) -> dict[str, Any]:
        """Convergently record a review and its terminal delegation result.

        Review identity, both exact revisions, and evidence are derived from
        the immutable worker binding. The model supplies no canonical identity
        or authority-bearing reference. The operation identity is derived from
        the bound delegation. Stop after its successful response.
        """

        if worker is None:  # pragma: no cover - registration guarantees worker scope
            raise LifecycleConflictError("review finalization requires a bound worker")
        if workspace is None:  # pragma: no cover - worker construction guarantees it
            raise LifecycleConflictError("workspace snapshot is unavailable")
        workspace.assert_unchanged()
        bound_review = worker_reviews[0] if len(worker_reviews) == 1 else None
        if bound_review is None or worker.get("purpose") != "independent_review":
            raise LifecycleConflictError("worker review write is outside its delegation scope")
        review_id = str(bound_review.get("id"))
        current_review = live_snapshot().reviews.get(review_id)
        if current_review is None or not _review_matches_worker(dict(current_review), worker):
            raise LifecycleConflictError("worker review write is outside its delegation scope")
        review = dict(current_review)
        expected_revision = str(worker.get("target_revision"))
        target_revision = str(bound_review.get("target_revision"))
        required_artifact_ids: list[str] = []
        review_target = dict(bound_review.get("target_ref") or {})
        if review_target.get("kind") == "artifact":
            required_artifact_ids.append(str(review_target.get("id")))
        for task in worker_tasks:
            for binding in task.get("artifact_bindings") or ():
                ref = binding.get("ref") or {}
                artifact_id = str(ref.get("id"))
                if ref.get("kind") != "artifact" or not artifact_id:
                    continue
                bundle = (worker_artifact_bundles or {}).get(artifact_id)
                artifact = (bundle or {}).get("artifact") or {}
                if str(artifact.get("effective_revision") or artifact.get("revision")) != str(
                    binding.get("revision")
                ):
                    raise LifecycleConflictError("bound artifact revision is stale for review")
                if artifact_id not in required_artifact_ids:
                    required_artifact_ids.append(artifact_id)
        delegation_id = str(worker.get("id"))
        operation_key = f"review-finalizer:{delegation_id}"
        if review.get("state") == "requested":
            missing = [
                artifact_id
                for artifact_id in required_artifact_ids
                if artifact_id not in worker_read_artifact_manifests
            ]
            if verdict == "approved" and missing:
                raise LifecycleConflictError("review evidence is incomplete for the bound subject")
            parsed_evidence = tuple(
                {
                    "kind": "manifest",
                    "id": worker_read_artifact_manifests[artifact_id],
                }
                for artifact_id in required_artifact_ids
                if artifact_id in worker_read_artifact_manifests
            )
            completed = commons.complete_review(
                review_id,
                expected_revision,
                target_revision=target_revision,
                verdict=verdict,
                summary=summary,
                evidence_refs=parsed_evidence,
                idempotency_key=f"{operation_key}:review",
            )
        else:
            if (
                review.get("state") != verdict
                or review.get("expected_revision") != expected_revision
                or review.get("target_revision") != target_revision
                or review.get("summary") != summary
                or (review.get("actor") or {}).get("session_id") != active_session_id
            ):
                raise LifecycleConflictError(
                    "partial review finalization does not match the bound operation"
                )
            completed = {
                "event_id": str(review.get("revision")),
                "event_type": "review.completed",
                "entity_ref": {"kind": "review", "id": review_id},
                "revision": str(review.get("effective_revision") or review.get("revision")),
            }

        # Canonical ledger churn from the review is excluded from the subject
        # snapshot, but a real subject/artifact mutation in the gap must leave
        # an honest partial result rather than a false delegation success.
        workspace.assert_unchanged()
        current = live_snapshot().delegations.get(delegation_id)
        if current is None:
            raise LifecycleConflictError(f"delegation does not exist: {delegation_id}")
        outcome = commons.succeed_delegation(
            delegation_id,
            str(current.get("effective_revision") or current.get("revision")),
            summary="Independent review completed through its bounded terminal operation.",
            result_refs=({"kind": "review", "id": review_id},),
            idempotency_key=f"{operation_key}:delegation",
        )
        return {**completed, "delegation_outcome": outcome}

    @register(
        _IDEMPOTENT_WRITE,
        worker_purposes=("verification", "independent_review"),
    )
    def commons_record_verification(
        target_ref: str,
        target_revision: str,
        claim: str,
        method: str,
        outcome: str,
        evidence_refs: list[str],
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Record a reproducible claim backed by existing canonical evidence."""

        parsed_target = parse_ref(target_ref).as_dict()
        if worker is not None:
            allowed_target = worker_verification_target()
            if allowed_target != (parsed_target, target_revision):
                raise LifecycleConflictError(
                    "worker verification write is outside its exact target scope"
                )
        if worker is not None and workspace is not None:
            workspace.assert_unchanged()
        return commons.record_verification(
            target_ref=parsed_target,
            target_revision=target_revision,
            claim=claim,
            method=method,
            outcome=outcome,
            evidence_refs=tuple(parse_ref(value).as_dict() for value in evidence_refs),
            idempotency_key=idempotency_key,
        )

    @register(_IDEMPOTENT_WRITE)
    def commons_delegation_input_needed(
        delegation_id: str,
        expected_revision: str,
        summary: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Pause active delegated work with a bounded, non-secret summary."""

        if worker is not None and delegation_id != worker.get("id"):
            raise LifecycleConflictError("worker outcome is outside its delegation scope")
        # The unchanged-workspace assertion belongs to a REVIEW, whose verdict is
        # only trustworthy if the reviewer judged the tree it was handed and did
        # not edit it -- the refusal even says "after reviewer snapshot
        # creation".  Applying it to every worker made the implementation path
        # impossible in production: a builder's entire job is to change the
        # workspace, so its terminal tool refused, and the run fell to
        # `needs_operator` with the work already on disk.  Found by the first
        # real provider run this project ever made; every earlier test used a
        # fake runner that called the manager directly and never crossed this
        # tool.
        if (
            worker is not None
            and workspace is not None
            and str(worker.get("purpose")) != "implementation"
        ):
            workspace.assert_unchanged()
        return commons.mark_delegation_input_needed(
            delegation_id,
            expected_revision,
            summary=summary,
            idempotency_key=idempotency_key,
        )

    @register(_IDEMPOTENT_WRITE, worker_purposes=("implementation", "verification"))
    def commons_succeed_delegation(
        delegation_id: str,
        expected_revision: str,
        summary: str,
        result_refs: list[str],
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Complete active delegated work with existing typed result references."""

        if worker is not None and delegation_id != worker.get("id"):
            raise LifecycleConflictError("worker outcome is outside its delegation scope")
        # The unchanged-workspace assertion belongs to a REVIEW, whose verdict is
        # only trustworthy if the reviewer judged the tree it was handed and did
        # not edit it -- the refusal even says "after reviewer snapshot
        # creation".  Applying it to every worker made the implementation path
        # impossible in production: a builder's entire job is to change the
        # workspace, so its terminal tool refused, and the run fell to
        # `needs_operator` with the work already on disk.  Found by the first
        # real provider run this project ever made; every earlier test used a
        # fake runner that called the manager directly and never crossed this
        # tool.
        if (
            worker is not None
            and workspace is not None
            and str(worker.get("purpose")) != "implementation"
        ):
            workspace.assert_unchanged()
        return commons.succeed_delegation(
            delegation_id,
            expected_revision,
            summary=summary,
            result_refs=tuple(parse_ref(value).as_dict() for value in result_refs),
            idempotency_key=idempotency_key,
        )

    @register(_IDEMPOTENT_WRITE)
    def commons_delegation_needs_operator(
        delegation_id: str,
        expected_revision: str,
        reason_code: str,
        summary: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Stop an ambiguous delegation without claiming success or retry safety."""

        if worker is not None and delegation_id != worker.get("id"):
            raise LifecycleConflictError("worker outcome is outside its delegation scope")
        return commons.mark_delegation_needs_operator(
            delegation_id,
            expected_revision,
            reason_code=reason_code,
            summary=summary,
            idempotency_key=idempotency_key,
        )

    @register(_READ_ONLY, worker_only=True)
    def commons_repo_files(prefix: str = "", max_items: int = 200) -> list[dict[str, Any]]:
        """List immutable UTF-8 review-snapshot paths with hashes and sizes."""

        if workspace is None:  # pragma: no cover - tool is registered only for workers
            raise LifecycleConflictError("workspace snapshot is unavailable")
        return workspace.list_files(prefix=prefix, max_items=max_items)

    @register(_READ_ONLY, worker_only=True)
    def commons_repo_read(path: str, expected_sha256: str | None = None) -> dict[str, Any]:
        """Read one unchanged, bounded UTF-8 file from the reviewer snapshot."""

        if workspace is None:  # pragma: no cover - tool is registered only for workers
            raise LifecycleConflictError("workspace snapshot is unavailable")
        return workspace.read(path, expected_sha256=expected_sha256)

    @register(_READ_ONLY, worker_only=True)
    def commons_repo_search(
        query: str, prefix: str = "", max_matches: int = 100
    ) -> list[dict[str, Any]]:
        """Literal-search unchanged snapshot text without exposing native filesystem tools."""

        if workspace is None:  # pragma: no cover - tool is registered only for workers
            raise LifecycleConflictError("workspace snapshot is unavailable")
        return workspace.search(query, prefix=prefix, max_matches=max_matches)

    if runtime is not None and worker is None:

        @register(_READ_ONLY, root_only=True)
        def commons_runtime_profiles() -> list[dict[str, Any]]:
            """List configured local broker profile capabilities."""

            return runtime.profile_summaries()

        @register(_READ_ONLY, root_only=True)
        def commons_runtime_attempts() -> list[dict[str, Any]]:
            """List metadata-only operational attempts without provider content."""

            return runtime.list_attempts()

        @register(_RUNTIME_WRITE, root_only=True)
        def commons_run_delegation(
            delegation_id: str,
            expected_revision: str,
            idempotency_key: str,
            retry: bool = False,
            context_pack_id: str | None = None,
            context_pack_revision: str | None = None,
        ) -> dict[str, Any]:
            """Launch one exact requested delegation through its selected fixed profile."""

            if (context_pack_id is None) != (context_pack_revision is None):
                raise ValidationError(
                    "context_pack_id and context_pack_revision must be supplied together"
                )
            try:
                context = (
                    ContextBindingRequest.accumulated(
                        context_pack_id=context_pack_id,
                        context_pack_revision=context_pack_revision,
                    )
                    if context_pack_id is not None and context_pack_revision is not None
                    else None
                )
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc

            values: dict[str, Any] = {
                "idempotency_key": idempotency_key,
                "retry": retry,
            }
            if context is not None:
                values["context"] = context
            return runtime.run(delegation_id, expected_revision, **values)

        @register(_DESTRUCTIVE_WRITE, root_only=True)
        def commons_reconcile_runtime() -> list[dict[str, Any]]:
            """Fail ambiguous broker attempts closed without blind relaunch."""

            return runtime.reconcile()

    return server


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
