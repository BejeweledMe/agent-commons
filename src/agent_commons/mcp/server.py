"""A deliberately small stdio MCP surface over :class:`CommonsManager`.

The adapter owns no persistence and contains no lifecycle rules.  Every write
delegates to ``CommonsManager`` so CLI and MCP clients share exactly one
business-logic boundary.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import stat
import subprocess
import sys
import time
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any, Protocol, TypeVar

from agent_commons import __version__
from agent_commons.core.refs import parse_ref
from agent_commons.errors import (
    CommonsError,
    ConfigurationError,
    LifecycleConflictError,
    SecurityPolicyError,
    ValidationError,
)
from agent_commons.runtime import (
    TERMINAL_TOOL_NAMES,
    TerminalToolAuditStore,
    resolve_trusted_executable,
)
from agent_commons.runtime.source_contract import agent_commons_source_sha256
from agent_commons.services import CommonsManager
from agent_commons.services.delegation_runtime import (
    DelegationRuntimeService,
    load_runtime_configuration,
    telemetry_sink,
)


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
    ) -> dict[str, Any]: ...

    def reconcile(self) -> list[dict[str, Any]]: ...


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

_SENSITIVE_NAMES = {".env", ".env.local", "credentials", "credentials.json"}


class _OversizedScopedFile(ConfigurationError):
    pass


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
        "commons_succeed_delegation",
        "commons_delegation_needs_operator",
        "commons_workspace_files",
        "commons_workspace_read",
        "commons_workspace_search",
    }
)
IMPLEMENTATION_WORKER_TOOL_NAMES = _COMMON_WORKER_TOOL_NAMES
VERIFICATION_WORKER_TOOL_NAMES = _COMMON_WORKER_TOOL_NAMES | {"commons_record_verification"}
INDEPENDENT_REVIEW_WORKER_TOOL_NAMES = VERIFICATION_WORKER_TOOL_NAMES | {"commons_complete_review"}


class ScopedWorkspaceReader:
    """Immutable, bounded, no-symlink text view for delegated reviewers."""

    def __init__(self, manager: CommonsManager, *, git_executable: str = "/usr/bin/git") -> None:
        self.manager = manager
        self.root = manager.repo_root.resolve()
        self.policy = manager.policy
        self.files: dict[str, tuple[str, int]] = {}
        self.registered_files: dict[str, tuple[str, int]] = {}
        total = 0
        self.git_executable = resolve_trusted_executable(
            git_executable,
            workspace_root=self.root,
        )
        try:
            result = subprocess.run(
                (
                    self.git_executable,
                    "-C",
                    str(self.root),
                    "ls-files",
                    "--cached",
                    "--others",
                    "--exclude-standard",
                    "-z",
                ),
                check=True,
                capture_output=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ConfigurationError("scoped reviewer could not enumerate Git files") from exc
        if len(result.stdout) > 4 * 1024 * 1024:
            raise ConfigurationError("scoped reviewer Git file list exceeds 4 MiB")
        try:
            names = sorted(item for item in result.stdout.decode("utf-8").split("\0") if item)
        except UnicodeDecodeError as exc:
            raise ConfigurationError("scoped reviewer requires UTF-8 Git paths") from exc
        for relative in names:
            normalized = Path(relative)
            if (
                normalized.is_absolute()
                or ".." in normalized.parts
                or normalized.name in _SENSITIVE_NAMES
                or normalized.name.startswith(".env.")
                or normalized.suffix.lower() in {".key", ".pem", ".p12", ".pfx"}
            ):
                continue
            try:
                digest, size = self._digest(normalized)
            except _OversizedScopedFile:
                continue
            except LifecycleConflictError:
                # A tracked path may have been replaced with a final or parent
                # symlink after it entered the Git index. Exclude it from the
                # immutable reviewer snapshot without ever following it.
                continue
            total += size
            if len(self.files) >= 5_000 or total > 64 * 1024 * 1024:
                raise ConfigurationError("scoped reviewer workspace exceeds safe snapshot limits")
            self.files[normalized.as_posix()] = (digest, size)

    def assert_unchanged(self) -> None:
        """Fail before a canonical result if any visible subject file moved."""

        current = ScopedWorkspaceReader(
            self.manager,
            git_executable=self.git_executable,
        )
        if current.files != self.files:
            raise LifecycleConflictError(
                "delegated workspace changed after reviewer snapshot creation"
            )
        for relative, frozen in self.registered_files.items():
            if self._digest(self._safe_candidate(relative)) != frozen:
                raise LifecycleConflictError(
                    "registered review artifact changed after it was inspected"
                )

    def _safe_candidate(self, relative: str) -> Path:
        normalized = Path(relative)
        if (
            normalized.is_absolute()
            or ".." in normalized.parts
            or normalized.name in _SENSITIVE_NAMES
            or normalized.name.startswith(".env.")
            or normalized.suffix.lower() in {".key", ".pem", ".p12", ".pfx"}
        ):
            raise ValidationError("artifact source path is outside the safe review scope")
        return normalized

    def _open_regular(self, relative: Path) -> int:
        """Open one repository-relative file without following any path symlink."""

        if (
            not relative.parts
            or relative.is_absolute()
            or ".." in relative.parts
            or not hasattr(os, "O_DIRECTORY")
            or not hasattr(os, "O_NOFOLLOW")
            or os.open not in os.supports_dir_fd
        ):
            raise LifecycleConflictError(
                "scoped reviewer path cannot be opened with no-symlink guarantees"
            )
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        file_flags = os.O_RDONLY | os.O_NOFOLLOW
        descriptors: list[int] = []
        try:
            current = os.open(self.root, directory_flags)
            descriptors.append(current)
            for component in relative.parts[:-1]:
                current = os.open(component, directory_flags, dir_fd=current)
                descriptors.append(current)
            descriptor = os.open(relative.parts[-1], file_flags, dir_fd=current)
            return descriptor
        except OSError as exc:
            raise LifecycleConflictError("scoped reviewer path must not traverse symlinks") from exc
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    def _read_bytes(self, relative: Path) -> bytes:
        descriptor = self._open_regular(relative)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise ConfigurationError("scoped reviewer files must be regular")
            if metadata.st_size > 1_048_576:
                raise _OversizedScopedFile("scoped reviewer files must be at most 1 MiB")
            chunks: list[bytes] = []
            remaining = metadata.st_size + 1
            while remaining:
                chunk = os.read(descriptor, min(remaining, 64 * 1024))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            body = b"".join(chunks)
            if len(body) != metadata.st_size or len(body) > 1_048_576:
                raise LifecycleConflictError("scoped reviewer file changed while it was read")
            return body
        finally:
            os.close(descriptor)

    def _digest(self, relative: Path) -> tuple[str, int]:
        body = self._read_bytes(relative)
        return hashlib.sha256(body).hexdigest(), len(body)

    def _review_content(self, content: str, *, context: str) -> tuple[str, list[dict[str, Any]]]:
        """Redact unsafe lines without quarantining the remaining review surface."""

        blocked_by_line: dict[int, list[Any]] = {}
        for start_line, end_line, finding in self.policy.scan_text_lines(content):
            if finding.classification not in self.policy.blocked_classifications:
                continue
            for line_number in range(start_line, end_line + 1):
                blocked_by_line.setdefault(line_number, []).append(finding)

        rendered: list[str] = []
        redactions: list[dict[str, Any]] = []
        for line_number, line in enumerate(content.splitlines(keepends=True), start=1):
            blocked = blocked_by_line.get(line_number, [])
            if not blocked:
                rendered.append(line)
                continue
            ending = (
                "\r\n"
                if line.endswith("\r\n")
                else "\n"
                if line.endswith("\n")
                else "\r"
                if line.endswith("\r")
                else ""
            )
            rendered.append("[agent-commons redacted source line]" + ending)
            redactions.append(
                {
                    "line": line_number,
                    "categories": sorted({finding.category for finding in blocked}),
                    "classifications": sorted(
                        {finding.classification.value for finding in blocked}
                    ),
                }
            )
        safe_content = "".join(rendered)
        # Retain the whole-document fail-closed check for any pattern that spans
        # line boundaries or survives the bounded line redactions.
        self.policy.assert_safe(safe_content, context=context)
        return safe_content, redactions

    def list_files(self, *, prefix: str = "", max_items: int = 200) -> list[dict[str, Any]]:
        if (
            not isinstance(max_items, int)
            or isinstance(max_items, bool)
            or not 1 <= max_items <= 500
        ):
            raise ValidationError("max_items must be between 1 and 500")
        normalized = prefix.strip().replace("\\", "/")
        if normalized.startswith("/") or ".." in Path(normalized).parts:
            raise ValidationError("workspace prefix must remain relative")
        return [
            {"path": path, "sha256": digest, "size_bytes": size}
            for path, (digest, size) in sorted(self.files.items())
            if path.startswith(normalized)
        ][:max_items]

    def read(self, path: str, *, expected_sha256: str | None = None) -> dict[str, Any]:
        normalized = Path(path)
        if normalized.is_absolute() or ".." in normalized.parts:
            raise ValidationError("workspace path must remain relative")
        relative = normalized.as_posix()
        frozen = self.files.get(relative)
        if frozen is None:
            raise LifecycleConflictError("workspace file is outside the delegated snapshot")
        body = self._read_bytes(normalized)
        digest, size = hashlib.sha256(body).hexdigest(), len(body)
        if (digest, size) != frozen:
            raise LifecycleConflictError("workspace file changed after reviewer snapshot creation")
        if expected_sha256 is not None and expected_sha256 != digest:
            raise LifecycleConflictError("workspace file does not match expected_sha256")
        try:
            content = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError("scoped reviewer can read UTF-8 text files only") from exc
        content, redactions = self._review_content(content, context="scoped reviewer file content")
        return {
            "path": relative,
            "sha256": digest,
            "content": content,
            "redactions": redactions,
        }

    def search(
        self, query: str, *, prefix: str = "", max_matches: int = 100
    ) -> list[dict[str, Any]]:
        if not isinstance(query, str) or not query or len(query) > 256 or "\x00" in query:
            raise ValidationError("search query must contain 1 to 256 safe characters")
        if (
            not isinstance(max_matches, int)
            or isinstance(max_matches, bool)
            or not 1 <= max_matches <= 200
        ):
            raise ValidationError("max_matches must be between 1 and 200")
        results: list[dict[str, Any]] = []
        for item in self.list_files(prefix=prefix, max_items=500):
            try:
                content = self.read(item["path"])["content"]
            except (SecurityPolicyError, ValidationError):
                continue
            for line_number, line in enumerate(content.splitlines(), start=1):
                if query in line:
                    results.append(
                        {"path": item["path"], "line": line_number, "text": line[:1_000]}
                    )
                    if len(results) >= max_matches:
                        return results
        return results

    def read_registered_artifact(
        self,
        *,
        source_path: str,
        expected_revision: str,
        expected_size: int,
    ) -> dict[str, Any]:
        """Read one exact task artifact, including an otherwise ignored evidence file."""

        if not expected_revision.startswith("sha256:"):
            raise ValidationError("registered artifact revision must use sha256")
        expected_digest = expected_revision.removeprefix("sha256:")
        if len(expected_digest) != 64 or any(
            character not in "0123456789abcdef" for character in expected_digest
        ):
            raise ValidationError("registered artifact revision is invalid")
        if (
            isinstance(expected_size, bool)
            or not isinstance(expected_size, int)
            or not 0 <= expected_size <= 1_048_576
        ):
            raise ValidationError("registered artifact size exceeds the review limit")
        relative_path = self._safe_candidate(source_path)
        body = self._read_bytes(relative_path)
        digest, size = hashlib.sha256(body).hexdigest(), len(body)
        if digest != expected_digest or size != expected_size:
            raise LifecycleConflictError(
                "registered artifact bytes do not match their immutable manifest"
            )
        try:
            content = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError("scoped reviewer can read UTF-8 artifacts only") from exc
        content, redactions = self._review_content(
            content, context="scoped registered artifact content"
        )
        relative = Path(source_path).as_posix()
        self.registered_files[relative] = (digest, size)
        return {
            "path": relative,
            "sha256": digest,
            "content": content,
            "redactions": redactions,
        }


def _fastmcp_factory(name: str) -> MCPServer:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - exercised through the entrypoint
        raise ConfigurationError(
            "MCP support is not installed; install agent-commons[mcp]"
        ) from exc
    return FastMCP(name, instructions=MCP_INSTRUCTIONS)


def build_server(
    repo_root: str | Path,
    *,
    session_id: str | None = None,
    manager: CommonsManager | None = None,
    runtime: RuntimeService | None = None,
    delegation_id: str | None = None,
    binding_wait_seconds: float = 5.0,
    git_executable: str = "/usr/bin/git",
    server_factory: Callable[[str], ServerT] | None = None,
) -> ServerT | MCPServer:
    """Build a local stdio server with an intentionally bounded tool set."""

    commons = manager or CommonsManager(repo_root, session_id=session_id)
    factory = server_factory or _fastmcp_factory
    server = factory("agent-commons")
    active_session_id = getattr(commons, "session_id", None)
    requested_binding = delegation_id or os.environ.get("AGENT_COMMONS_DELEGATION_ID")
    worker: dict[str, Any] | None = None
    if requested_binding is not None:
        if active_session_id is None:
            raise ConfigurationError("delegated MCP binding requires an active child session")
        if binding_wait_seconds < 0 or binding_wait_seconds > 30:
            raise ConfigurationError("delegated MCP binding wait must be between 0 and 30 seconds")
        deadline = time.monotonic() + binding_wait_seconds
        while True:
            candidate = commons.get_delegation(requested_binding)
            state = candidate.get("state")
            child_session_id = candidate.get("child_session_id")
            if state in {"active", "input_needed"} and child_session_id == active_session_id:
                worker = candidate
                break
            if child_session_id not in {None, active_session_id} or state not in {
                "requested",
                "active",
                "input_needed",
            }:
                raise ConfigurationError(
                    "delegated MCP binding does not match its live canonical child"
                )
            if time.monotonic() >= deadline:
                raise ConfigurationError(
                    "delegated MCP binding was not canonically started before the deadline"
                )
            time.sleep(0.01)
    else:
        worker_matches = [
            candidate
            for candidate in commons.list_delegations(state=None)
            if active_session_id is not None
            and candidate.get("child_session_id") == active_session_id
            and candidate.get("state") in {"active", "input_needed"}
        ]
        if len(worker_matches) > 1:
            raise ConfigurationError("one child session cannot own multiple active delegations")
        worker = worker_matches[0] if worker_matches else None
    workspace = (
        ScopedWorkspaceReader(commons, git_executable=git_executable)
        if worker is not None
        else None
    )
    terminal_audit = (
        TerminalToolAuditStore(
            commons.paths.state_root,
            security_policy=commons.policy,
            read_only=commons.read_only,
        )
        if worker is not None
        else None
    )

    def require_live_worker() -> dict[str, Any] | None:
        if worker is None:
            return None
        current = commons.get_delegation(str(worker.get("id")))
        if (
            current.get("state") not in {"active", "input_needed"}
            or current.get("child_session_id") != active_session_id
        ):
            raise LifecycleConflictError("worker MCP authority ended with its canonical delegation")
        return current

    def register(
        annotations: dict[str, bool],
        *,
        root_only: bool = False,
        worker_only: bool = False,
        worker_purposes: tuple[str, ...] = (),
    ) -> Callable[[Callable[..., Any]], Any]:
        def decorator(function: Callable[..., Any]) -> Any:
            if (
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
                    except Exception:
                        if terminal and terminal_audit is not None:
                            try:
                                terminal_audit.record(delegation, function.__name__, "rejected")
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

    def relevant_artifact_ids() -> set[str]:
        if worker is None:
            return {str(item.get("id")) for item in commons.list_artifacts()}
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
        if worker is None:
            return None
        purpose = worker.get("purpose")
        if purpose == "verification":
            return dict(worker.get("target_ref") or {}), str(worker.get("target_revision"))
        if purpose == "independent_review":
            for review in commons.list_reviews(state=None):
                if relevant_review(review):
                    return dict(review.get("target_ref") or {}), str(review.get("target_revision"))
        return None

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
            "reviews": [
                review for review in commons.list_reviews(state=None) if relevant_review(review)
            ][:max_items],
            "verifications": [
                verification
                for verification in commons.list_verifications()
                if relevant_verification(verification)
            ][:max_items],
        }

    @register(_READ_ONLY)
    def commons_inbox(max_items: int = 20) -> dict[str, Any]:
        """Return open discussions and handoffs addressed to this session."""

        if worker is not None:
            return {"delegation": worker, "threads": [], "handoffs": []}
        return commons.inbox(max_items=max_items)

    @register(_READ_ONLY)
    def commons_list_tasks(state: str | None = None) -> list[dict[str, Any]]:
        """List projected tasks, optionally filtered by lifecycle state."""

        tasks = commons.list_tasks(state=state)
        if worker is None:
            return tasks
        target = worker.get("target_ref") or {}
        allowed_ids = {str(target.get("id"))} if target.get("kind") == "task" else set()
        for review in commons.list_reviews(state=None):
            if relevant_review(review) and (review.get("target_ref") or {}).get("kind") == "task":
                allowed_ids.add(str((review.get("target_ref") or {}).get("id")))
        return [task for task in tasks if task.get("id") in allowed_ids]

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
        return commons.get_delegation(delegation_id)

    @register(_READ_ONLY)
    def commons_list_reviews(state: str | None = None) -> list[dict[str, Any]]:
        """List revision-bound reviews, optionally filtered by lifecycle state."""

        reviews = commons.list_reviews(state=state)
        return [review for review in reviews if relevant_review(review)]

    @register(_READ_ONLY)
    def commons_show_review(review_id: str) -> dict[str, Any]:
        """Return one projected review without exposing an unbounded query surface."""

        review = next(
            (item for item in commons.list_reviews(state=None) if item.get("id") == review_id),
            None,
        )
        if review is None:
            raise LifecycleConflictError(f"review does not exist: {review_id}")
        if not relevant_review(review):
            raise LifecycleConflictError("worker may inspect only its bound review")
        return review

    @register(_READ_ONLY, worker_only=True)
    def commons_list_verifications() -> list[dict[str, Any]]:
        """List reproducible checks relevant to the exact worker target."""

        return [
            verification
            for verification in commons.list_verifications()
            if relevant_verification(verification)
        ]

    @register(_READ_ONLY, worker_only=True)
    def commons_show_verification(verification_id: str) -> dict[str, Any]:
        """Return one target-scoped verification without widening worker access."""

        verification = next(
            (item for item in commons.list_verifications() if item.get("id") == verification_id),
            None,
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
        return commons.get_artifact_bundle(artifact_id)

    @register(_READ_ONLY, worker_only=True)
    def commons_read_artifact(artifact_id: str) -> dict[str, Any]:
        """Read one exact UTF-8 evidence artifact after manifest hash verification."""

        if workspace is None:  # pragma: no cover - tool is registered only for workers
            raise LifecycleConflictError("workspace snapshot is unavailable")
        if artifact_id not in relevant_artifact_ids():
            raise LifecycleConflictError("worker may read only a bound task artifact")
        bundle = commons.get_artifact_bundle(artifact_id)
        manifest = bundle["manifest"]
        source = manifest.get("source") or {}
        return workspace.read_registered_artifact(
            source_path=str(source.get("path", "")),
            expected_revision=str(manifest.get("revision", "")),
            expected_size=int(manifest.get("size_bytes", -1)),
        )

    @register(_IDEMPOTENT_WRITE, root_only=True)
    def commons_request_delegation(
        target_ref: str,
        target_revision: str,
        target_profile: str,
        purpose: str,
        idempotency_key: str,
        max_depth: int = 1,
        wall_time_seconds: int = 1800,
        max_attempts: int = 1,
        max_concurrency: int = 1,
        budget_unit: str = "provider_units",
        budget_limit: int = 1,
        parent_delegation_id: str | None = None,
    ) -> dict[str, Any]:
        """Request bounded work against an exact target revision.

        This records intent only.  Launching remains a separate broker action,
        and the supplied idempotency key must be stable for identical retries.
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

    @register(_IDEMPOTENT_WRITE, worker_purposes=("independent_review",))
    def commons_complete_review(
        review_id: str,
        expected_revision: str,
        target_revision: str,
        verdict: str,
        summary: str,
        idempotency_key: str,
        evidence_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        """Complete one existing exact-revision review through manager validation."""

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
        if worker is not None and workspace is not None:
            workspace.assert_unchanged()
        return commons.mark_delegation_input_needed(
            delegation_id,
            expected_revision,
            summary=summary,
            idempotency_key=idempotency_key,
        )

    @register(_IDEMPOTENT_WRITE)
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
        if worker is not None and workspace is not None:
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
    def commons_workspace_files(prefix: str = "", max_items: int = 200) -> list[dict[str, Any]]:
        """List immutable UTF-8 review-snapshot paths with hashes and sizes."""

        if workspace is None:  # pragma: no cover - tool is registered only for workers
            raise LifecycleConflictError("workspace snapshot is unavailable")
        return workspace.list_files(prefix=prefix, max_items=max_items)

    @register(_READ_ONLY, worker_only=True)
    def commons_workspace_read(path: str, expected_sha256: str | None = None) -> dict[str, Any]:
        """Read one unchanged, bounded UTF-8 file from the reviewer snapshot."""

        if workspace is None:  # pragma: no cover - tool is registered only for workers
            raise LifecycleConflictError("workspace snapshot is unavailable")
        return workspace.read(path, expected_sha256=expected_sha256)

    @register(_READ_ONLY, worker_only=True)
    def commons_workspace_search(
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
        ) -> dict[str, Any]:
            """Launch one exact requested delegation through its selected fixed profile."""

            return runtime.run(
                delegation_id,
                expected_revision,
                idempotency_key=idempotency_key,
                retry=retry,
            )

        @register(_DESTRUCTIVE_WRITE, root_only=True)
        def commons_reconcile_runtime() -> list[dict[str, Any]]:
            """Fail ambiguous broker attempts closed without blind relaunch."""

            return runtime.reconcile()

    return server


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-commons-mcp",
        description="Run the optional local Agent Commons MCP server over stdio.",
    )
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument(
        "--state-root",
        type=Path,
        default=os.environ.get("AGENT_COMMONS_STATE_ROOT"),
        help="Explicit operator-authorized operational state directory.",
    )
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="Validate imports and the root tool catalog without opening stdio or writing state.",
    )
    parser.add_argument(
        "--session-id",
        default=os.environ.get("AGENT_COMMONS_SESSION_ID"),
        help="Active writer session; defaults to AGENT_COMMONS_SESSION_ID.",
    )
    parser.add_argument(
        "--delegation-id",
        default=os.environ.get("AGENT_COMMONS_DELEGATION_ID"),
        help="Broker-bound delegation; defaults to AGENT_COMMONS_DELEGATION_ID.",
    )
    parser.add_argument(
        "--git-executable",
        default="/usr/bin/git",
        help="Operator-selected trusted Git executable for scoped workspace reads.",
    )
    parser.add_argument(
        "--enable-runtime",
        action="store_true",
        help="Expose bounded broker run/status/reconcile tools to this MCP client.",
    )
    parser.add_argument(
        "--profile-config",
        type=Path,
        help="Operator-owned strict YAML profile configuration.",
    )
    parser.add_argument(
        "--telemetry",
        choices=("none", "local", "otel"),
        default="none",
        help="Optional metadata-only runtime telemetry sink.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Console entry point.  Stdout is reserved exclusively for MCP frames."""

    arguments = _parser().parse_args(argv)
    try:
        manager = CommonsManager(
            arguments.repo.expanduser().resolve(),
            session_id=arguments.session_id,
            state_root=arguments.state_root,
            read_only=arguments.preflight,
        )
        if arguments.preflight:
            git = resolve_trusted_executable(
                arguments.git_executable,
                workspace_root=manager.repo_root,
            )
            server = build_server(
                arguments.repo.expanduser().resolve(),
                manager=manager,
                git_executable=git,
            )
            if not hasattr(server, "list_tools"):
                raise ConfigurationError("FastMCP server does not expose its tool catalog")
            tools = asyncio.run(server.list_tools())  # type: ignore[attr-defined]
            names = sorted(tool.name for tool in tools)
            worker_catalogs = {
                "implementation": sorted(IMPLEMENTATION_WORKER_TOOL_NAMES),
                "independent_review": sorted(INDEPENDENT_REVIEW_WORKER_TOOL_NAMES),
                "verification": sorted(VERIFICATION_WORKER_TOOL_NAMES),
            }
            body = {
                "schema": "agent_commons.mcp_preflight.v2",
                "agent_commons_version": __version__,
                "agent_commons_source_sha256": agent_commons_source_sha256(),
                "tool_count": len(names),
                "tool_catalog_sha256": hashlib.sha256("\n".join(names).encode("utf-8")).hexdigest(),
                "worker_catalogs": {
                    purpose: {
                        "tool_names": catalog,
                        "tool_catalog_sha256": hashlib.sha256(
                            "\n".join(catalog).encode("utf-8")
                        ).hexdigest(),
                    }
                    for purpose, catalog in worker_catalogs.items()
                },
            }
            print(json.dumps(body, sort_keys=True, separators=(",", ":")))
            return 0
        runtime = None
        if arguments.enable_runtime:
            runtime_config = load_runtime_configuration(
                arguments.profile_config,
                workspace_root=manager.repo_root,
            )
            runtime = DelegationRuntimeService(
                manager,
                profiles=runtime_config.profiles,
                operator_limits=runtime_config.limits,
                telemetry=telemetry_sink(arguments.telemetry, manager),
            )
        server = build_server(
            arguments.repo.expanduser().resolve(),
            session_id=arguments.session_id,
            manager=manager,
            runtime=runtime,
            delegation_id=arguments.delegation_id,
            git_executable=arguments.git_executable,
        )
        server.run(transport="stdio")
    except (CommonsError, FileNotFoundError) as exc:
        print(f"agent-commons-mcp: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
