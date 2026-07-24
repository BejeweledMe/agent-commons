"""Typed, allowlisted provider launch profiles.

Profiles are operator-owned configuration.  A delegation request selects one of
four built-in profile identifiers; it never supplies argv fragments, environment
variables, or provider configuration overrides.
"""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol

from agent_commons.errors import ConfigurationError, ValidationError

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")
_TRACE_ID = re.compile(r"^[a-f0-9]{32}$")
_TARGET_KIND = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class Provider(StrEnum):
    CODEX = "codex"
    CLAUDE = "claude"


class ExecutableRole(StrEnum):
    PROVIDER = "provider"
    MCP = "mcp"
    GIT = "git"


class ExecutableResolutionError(ConfigurationError):
    """Trusted executable resolution failed for one fixed profile component."""

    def __init__(self, role: ExecutableRole, message: str) -> None:
        super().__init__(message)
        self.role = role


class BuiltinProfileId(StrEnum):
    CODEX_BUILDER = "codex-builder"
    CODEX_INDEPENDENT_REVIEWER = "codex-independent-reviewer"
    CLAUDE_BUILDER = "claude-builder"
    CLAUDE_INDEPENDENT_REVIEWER = "claude-independent-reviewer"

    @property
    def provider(self) -> Provider:
        if self.value.startswith("codex-"):
            return Provider.CODEX
        return Provider.CLAUDE

    @property
    def independent_reviewer(self) -> bool:
        return self.value.endswith("-independent-reviewer")


class CodexSandbox(StrEnum):
    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"


class CodexApprovalPolicy(StrEnum):
    NEVER = "never"


class ClaudePermissionMode(StrEnum):
    ACCEPT_EDITS = "acceptEdits"
    DONT_ASK = "dontAsk"
    PLAN = "plan"


_CLAUDE_COMMONS_READ_TOOLS = (
    "mcp__agent-commons__commons_orient",
    "mcp__agent-commons__commons_inbox",
    "mcp__agent-commons__commons_list_tasks",
    "mcp__agent-commons__commons_list_delegations",
    "mcp__agent-commons__commons_show_delegation",
    "mcp__agent-commons__commons_list_reviews",
    "mcp__agent-commons__commons_show_review",
    "mcp__agent-commons__commons_list_verifications",
    "mcp__agent-commons__commons_show_verification",
    "mcp__agent-commons__commons_show_artifact",
    "mcp__agent-commons__commons_read_artifact",
    "mcp__agent-commons__commons_workspace_files",
    "mcp__agent-commons__commons_workspace_read",
    "mcp__agent-commons__commons_workspace_search",
)
_CLAUDE_COMMONS_OUTCOME_TOOLS = (
    "mcp__agent-commons__commons_delegation_input_needed",
    "mcp__agent-commons__commons_succeed_delegation",
    "mcp__agent-commons__commons_delegation_needs_operator",
)
_CLAUDE_COMMONS_REVIEW_TOOLS = (
    "mcp__agent-commons__commons_complete_review",
    "mcp__agent-commons__commons_record_verification",
)
_CLAUDE_COMMONS_VERIFICATION_TOOLS = ("mcp__agent-commons__commons_record_verification",)
_WORKER_PURPOSES = frozenset({"implementation", "independent_review", "verification"})
_MCP_TOOL_PREFIX = "mcp__agent-commons__"
_CODEX_MCP_SERVER = "agent-commons"


def _profile_worker_purpose(
    profile_id: BuiltinProfileId,
    worker_purpose: str | None,
) -> str:
    purpose = worker_purpose or (
        "independent_review" if profile_id.independent_reviewer else "implementation"
    )
    if purpose not in _WORKER_PURPOSES:
        raise ConfigurationError("runner worker purpose is unsupported")
    if profile_id.independent_reviewer:
        if purpose not in {"independent_review", "verification"}:
            raise ConfigurationError("independent reviewer profile requires a review purpose")
    elif purpose != "implementation":
        raise ConfigurationError("builder profile requires an implementation purpose")
    return purpose


def _worker_tools(
    profile_id: BuiltinProfileId,
    purpose: str,
) -> tuple[str, ...]:
    tools = _CLAUDE_COMMONS_READ_TOOLS + _CLAUDE_COMMONS_OUTCOME_TOOLS
    if profile_id.independent_reviewer:
        tools += (
            _CLAUDE_COMMONS_REVIEW_TOOLS
            if purpose == "independent_review"
            else _CLAUDE_COMMONS_VERIFICATION_TOOLS
        )
    return tools


def _resolved_worker_mcp(
    *,
    workspace_root: Path,
    state_root: Path | None,
    delegation_id: str,
    mcp_executable: str,
    git_executable: str,
) -> tuple[str, tuple[str, ...]]:
    resolved_mcp = resolve_trusted_executable(
        mcp_executable,
        workspace_root=workspace_root,
        role=ExecutableRole.MCP,
    )
    resolved_git = resolve_trusted_executable(
        git_executable,
        workspace_root=workspace_root,
        role=ExecutableRole.GIT,
    )
    effective_state_root = (
        Path(state_root if state_root is not None else workspace_root / ".agent-commons")
        .expanduser()
        .resolve()
    )
    return resolved_mcp, (
        "--repo",
        str(workspace_root.resolve()),
        "--state-root",
        str(effective_state_root),
        "--delegation-id",
        delegation_id,
        "--git-executable",
        resolved_git,
    )


def _toml_literal(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _safe_identifier(name: str, value: str, *, pattern: re.Pattern[str] = _SAFE_IDENTIFIER) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValidationError(f"{name} is not a safe identifier")
    return value


def _safe_optional_identifier(name: str, value: object) -> str | None:
    if value is None:
        return None
    return _safe_identifier(name, str(value))


def _safe_executable(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise ConfigurationError("profile executable must be a non-empty string")
    if any(ord(character) < 32 for character in value):
        raise ConfigurationError("profile executable contains control characters")
    path = Path(value)
    if not path.is_absolute() and path.name != value:
        raise ConfigurationError("profile executable must be a basename or an absolute path")
    return value


def resolve_trusted_executable(
    value: str,
    *,
    workspace_root: Path,
    role: ExecutableRole = ExecutableRole.PROVIDER,
) -> str:
    """Resolve a provider once, rejecting workspace/PATH and mode hijacks."""

    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        matches: list[Path] = []
        for entry in os.environ.get("PATH", os.defpath).split(os.pathsep):
            directory = Path(entry or ".").expanduser()
            if not directory.is_absolute():
                continue
            path = directory / value
            if path.is_file() and os.access(path, os.X_OK):
                matches.append(path)
        if not matches:
            raise ExecutableResolutionError(
                role,
                f"profile executable is unavailable: {value}",
            )
        candidate = matches[0]
    try:
        resolved = candidate.resolve(strict=True)
        root = workspace_root.expanduser().resolve(strict=True)
        metadata = resolved.stat()
    except OSError as exc:
        raise ExecutableResolutionError(
            role,
            "profile executable cannot be resolved safely",
        ) from exc
    if resolved == root or root in resolved.parents:
        raise ExecutableResolutionError(
            role,
            "profile executable must be outside the delegated workspace",
        )
    if not stat.S_ISREG(metadata.st_mode) or not os.access(resolved, os.X_OK):
        raise ExecutableResolutionError(
            role,
            "profile executable must be an executable regular file",
        )
    if metadata.st_mode & 0o022:
        raise ExecutableResolutionError(
            role,
            "profile executable must not be group/world writable",
        )
    if hasattr(os, "getuid") and metadata.st_uid not in {0, os.getuid()}:
        raise ExecutableResolutionError(
            role,
            "profile executable must be owned by the operator or root",
        )
    return str(resolved)


def _instruction_bytes(instruction: str) -> bytes:
    if not isinstance(instruction, str) or not instruction.strip():
        raise ValidationError("runner instruction must be non-empty")
    encoded = instruction.encode("utf-8")
    if len(encoded) > 1_000_000:
        raise ValidationError("runner instruction exceeds the one-megabyte limit")
    return encoded


def _budget_usd(microusd: int) -> str:
    if isinstance(microusd, bool) or not isinstance(microusd, int) or microusd < 1:
        raise ValidationError("budget must be a positive integer number of micro-USD")
    value = Decimal(microusd) / Decimal(1_000_000)
    return format(value.normalize(), "f")


@dataclass(frozen=True, slots=True)
class CorrelationIds:
    """Safe metadata joining canonical work, broker state, and provider execution."""

    delegation_id: str
    target_kind: str
    target_id: str
    target_revision: str
    parent_session_id: str
    child_session_id: str
    trace_id: str | None = None

    def __post_init__(self) -> None:
        _safe_identifier("delegation_id", self.delegation_id)
        _safe_identifier("target_kind", self.target_kind, pattern=_TARGET_KIND)
        _safe_identifier("target_id", self.target_id)
        _safe_identifier("target_revision", self.target_revision)
        _safe_identifier("parent_session_id", self.parent_session_id)
        _safe_identifier("child_session_id", self.child_session_id)
        if self.parent_session_id == self.child_session_id:
            raise ValidationError("delegated work requires a distinct child session")
        if self.trace_id is not None:
            _safe_identifier("trace_id", self.trace_id, pattern=_TRACE_ID)

    def as_dict(self) -> dict[str, str]:
        result = {
            "delegation_id": self.delegation_id,
            "target_kind": self.target_kind,
            "target_id": self.target_id,
            "target_revision": self.target_revision,
            "parent_session_id": self.parent_session_id,
            "child_session_id": self.child_session_id,
        }
        if self.trace_id is not None:
            result["trace_id"] = self.trace_id
        return result


@dataclass(frozen=True, slots=True)
class RunnerInvocation:
    """An argv-only process invocation; stdin is ephemeral task content."""

    provider: Provider
    profile_id: BuiltinProfileId
    argv: tuple[str, ...]
    stdin: bytes

    def __post_init__(self) -> None:
        if not self.argv or any(not isinstance(item, str) or "\x00" in item for item in self.argv):
            raise ValidationError("runner argv is invalid")
        if not isinstance(self.stdin, bytes):
            raise TypeError("runner stdin must be bytes")


class RunnerProfile(Protocol):
    profile_id: BuiltinProfileId
    provider: Provider

    @property
    def supports_budget(self) -> bool: ...

    def build_invocation(
        self,
        instruction: str,
        *,
        workspace_root: Path,
        state_root: Path | None = None,
        delegation_id: str | None = None,
        max_budget_microusd: int | None = None,
        worker_purpose: str | None = None,
    ) -> RunnerInvocation: ...


@dataclass(frozen=True, slots=True)
class CodexRunnerProfile:
    profile_id: BuiltinProfileId
    executable: str = "codex"
    mcp_executable: str = "agent-commons-mcp"
    git_executable: str = "/usr/bin/git"
    model: str | None = None
    sandbox: CodexSandbox = CodexSandbox.WORKSPACE_WRITE
    approval_policy: CodexApprovalPolicy = CodexApprovalPolicy.NEVER
    trusted_workspace: bool = False

    def __post_init__(self) -> None:
        if self.profile_id.provider is not Provider.CODEX:
            raise ConfigurationError("Codex profile requires a Codex profile identifier")
        object.__setattr__(self, "executable", _safe_executable(self.executable))
        object.__setattr__(self, "mcp_executable", _safe_executable(self.mcp_executable))
        object.__setattr__(self, "git_executable", _safe_executable(self.git_executable))
        object.__setattr__(self, "model", _safe_optional_identifier("model", self.model))
        try:
            object.__setattr__(self, "sandbox", CodexSandbox(self.sandbox))
            object.__setattr__(self, "approval_policy", CodexApprovalPolicy(self.approval_policy))
        except ValueError as exc:
            raise ConfigurationError("Codex profile has an unsupported launch mode") from exc
        if not isinstance(self.trusted_workspace, bool):
            raise ConfigurationError("trusted_workspace must be boolean")
        if self.profile_id.independent_reviewer and self.sandbox is not CodexSandbox.READ_ONLY:
            raise ConfigurationError("independent Codex reviewer must use read-only sandbox")

    @property
    def provider(self) -> Provider:
        return Provider.CODEX

    @property
    def supports_budget(self) -> bool:
        return False

    def build_invocation(
        self,
        instruction: str,
        *,
        workspace_root: Path,
        state_root: Path | None = None,
        delegation_id: str | None = None,
        max_budget_microusd: int | None = None,
        worker_purpose: str | None = None,
    ) -> RunnerInvocation:
        if not self.trusted_workspace:
            raise ConfigurationError(
                "Codex runtime requires explicit trusted_workspace opt-in or external isolation"
            )
        if delegation_id is None:
            raise ConfigurationError("Codex runtime requires an exact delegation binding")
        _safe_identifier("delegation_id", delegation_id)
        purpose = _profile_worker_purpose(self.profile_id, worker_purpose)
        if max_budget_microusd is not None:
            raise ConfigurationError("Codex CLI cannot enforce a monetary launch budget")
        mcp_executable, mcp_args = _resolved_worker_mcp(
            workspace_root=workspace_root,
            state_root=state_root,
            delegation_id=delegation_id,
            mcp_executable=self.mcp_executable,
            git_executable=self.git_executable,
        )
        enabled_tools = tuple(
            tool.removeprefix(_MCP_TOOL_PREFIX) for tool in _worker_tools(self.profile_id, purpose)
        )
        config_prefix = f"mcp_servers.{_CODEX_MCP_SERVER}"
        argv = [
            resolve_trusted_executable(self.executable, workspace_root=workspace_root),
            "--ask-for-approval",
            self.approval_policy.value,
            "--sandbox",
            self.sandbox.value,
        ]
        if self.model is not None:
            argv.extend(("--model", self.model))
        argv.extend(
            (
                "exec",
                "--ignore-user-config",
                "--strict-config",
                "-c",
                f"{config_prefix}.command={_toml_literal(mcp_executable)}",
                "-c",
                f"{config_prefix}.args={_toml_literal(mcp_args)}",
                "-c",
                f"{config_prefix}.enabled_tools={_toml_literal(enabled_tools)}",
                "-c",
                f"{config_prefix}.required=true",
                "--json",
                "--color",
                "never",
                "-",
            )
        )
        return RunnerInvocation(
            provider=self.provider,
            profile_id=self.profile_id,
            argv=tuple(argv),
            stdin=_instruction_bytes(instruction),
        )


@dataclass(frozen=True, slots=True)
class ClaudeRunnerProfile:
    profile_id: BuiltinProfileId
    executable: str = "claude"
    mcp_executable: str = "agent-commons-mcp"
    git_executable: str = "/usr/bin/git"
    model: str | None = None
    permission_mode: ClaudePermissionMode = ClaudePermissionMode.ACCEPT_EDITS
    max_budget_microusd: int | None = None
    trusted_workspace: bool = False

    def __post_init__(self) -> None:
        if self.profile_id.provider is not Provider.CLAUDE:
            raise ConfigurationError("Claude profile requires a Claude profile identifier")
        object.__setattr__(self, "executable", _safe_executable(self.executable))
        object.__setattr__(self, "mcp_executable", _safe_executable(self.mcp_executable))
        object.__setattr__(self, "git_executable", _safe_executable(self.git_executable))
        object.__setattr__(self, "model", _safe_optional_identifier("model", self.model))
        try:
            object.__setattr__(self, "permission_mode", ClaudePermissionMode(self.permission_mode))
        except ValueError as exc:
            raise ConfigurationError("Claude profile has an unsupported permission mode") from exc
        if self.max_budget_microusd is not None:
            _budget_usd(self.max_budget_microusd)
        if not isinstance(self.trusted_workspace, bool):
            raise ConfigurationError("trusted_workspace must be boolean")
        if (
            self.profile_id.independent_reviewer
            and self.permission_mode is not ClaudePermissionMode.DONT_ASK
        ):
            raise ConfigurationError(
                "independent Claude reviewer must use dontAsk with fixed allowed tools"
            )

    @property
    def provider(self) -> Provider:
        return Provider.CLAUDE

    @property
    def supports_budget(self) -> bool:
        return True

    def build_invocation(
        self,
        instruction: str,
        *,
        workspace_root: Path,
        state_root: Path | None = None,
        delegation_id: str | None = None,
        max_budget_microusd: int | None = None,
        worker_purpose: str | None = None,
    ) -> RunnerInvocation:
        if delegation_id is None:
            raise ConfigurationError("Claude runtime requires an exact delegation binding")
        _safe_identifier("delegation_id", delegation_id)
        purpose = _profile_worker_purpose(self.profile_id, worker_purpose)
        if not self.profile_id.independent_reviewer and not self.trusted_workspace:
            raise ConfigurationError(
                "writable Claude runtime requires explicit trusted_workspace opt-in or "
                "external isolation"
            )
        effective_budget = self.max_budget_microusd
        if max_budget_microusd is not None:
            effective_budget = (
                max_budget_microusd
                if effective_budget is None
                else min(effective_budget, max_budget_microusd)
            )
        provider_executable = resolve_trusted_executable(
            self.executable,
            workspace_root=workspace_root,
            role=ExecutableRole.PROVIDER,
        )
        mcp_executable, mcp_args = _resolved_worker_mcp(
            workspace_root=workspace_root,
            state_root=state_root,
            delegation_id=delegation_id,
            mcp_executable=self.mcp_executable,
            git_executable=self.git_executable,
        )
        # Pass the sole MCP server as immutable argv material.  Strict mode
        # excludes ambient user/project MCP configuration, while the server
        # inherits only the broker-selected child session identity.
        mcp_config = json.dumps(
            {
                "mcpServers": {
                    "agent-commons": {
                        "type": "stdio",
                        "command": mcp_executable,
                        "args": list(mcp_args),
                    }
                }
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        argv = [
            provider_executable,
            "--print",
            "--verbose",
            "--output-format",
            "stream-json",
            "--permission-mode",
            self.permission_mode.value,
            "--no-session-persistence",
            "--disable-slash-commands",
            "--setting-sources",
            "",
            "--strict-mcp-config",
            "--mcp-config",
            mcp_config,
        ]
        if self.model is not None:
            argv.extend(("--model", self.model))
        if effective_budget is not None:
            argv.extend(("--max-budget-usd", _budget_usd(effective_budget)))
        # A provider worker may close only its already-bound delegation.  Root
        # request/cancel tools and provider-internal subagents would bypass the
        # canonical parent/depth lineage, so neither worker profile receives
        # them.  Interactive parent sessions remain free to use those MCP tools.
        allowed_tools = _worker_tools(self.profile_id, purpose)
        if self.profile_id.independent_reviewer:
            argv.extend(
                (
                    "--tools",
                    "",
                    "--disallowed-tools",
                    "Bash,Read,Glob,Grep,Edit,Write,NotebookEdit,Agent,WebFetch,WebSearch",
                )
            )
        else:
            argv.extend(("--disallowed-tools", "Agent,WebFetch,WebSearch"))
        argv.extend(("--allowed-tools", ",".join(allowed_tools)))
        return RunnerInvocation(
            provider=self.provider,
            profile_id=self.profile_id,
            argv=tuple(argv),
            stdin=_instruction_bytes(instruction),
        )


_CODEX_FIELDS = frozenset(
    {
        "executable",
        "mcp_executable",
        "git_executable",
        "model",
        "sandbox",
        "approval_policy",
        "trusted_workspace",
    }
)
_CLAUDE_FIELDS = frozenset(
    {
        "executable",
        "mcp_executable",
        "git_executable",
        "model",
        "permission_mode",
        "max_budget_microusd",
        "trusted_workspace",
    }
)


def _reject_unknown_fields(value: Mapping[str, Any], allowed: frozenset[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ConfigurationError(f"{label} has unsupported fields: {', '.join(unknown)}")


@dataclass(frozen=True, slots=True)
class ProfileRegistry:
    _profiles: Mapping[BuiltinProfileId, RunnerProfile]

    def __post_init__(self) -> None:
        normalized: dict[BuiltinProfileId, RunnerProfile] = {}
        for raw_id, profile in self._profiles.items():
            profile_id = BuiltinProfileId(raw_id)
            if profile.profile_id is not profile_id:
                raise ConfigurationError("profile registry key does not match profile body")
            normalized[profile_id] = profile
        if not normalized:
            raise ConfigurationError("at least one runner profile must be configured")
        object.__setattr__(self, "_profiles", MappingProxyType(normalized))

    def get(self, profile_id: str | BuiltinProfileId) -> RunnerProfile:
        try:
            normalized = BuiltinProfileId(profile_id)
            return self._profiles[normalized]
        except (KeyError, ValueError) as exc:
            raise ConfigurationError(f"runner profile is not configured: {profile_id}") from exc

    @property
    def profile_ids(self) -> tuple[BuiltinProfileId, ...]:
        return tuple(sorted(self._profiles, key=lambda item: item.value))

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> ProfileRegistry:
        if set(config) != {"profiles"}:
            raise ConfigurationError("runtime profile config requires exactly the 'profiles' key")
        raw_profiles = config["profiles"]
        if not isinstance(raw_profiles, Mapping):
            raise ConfigurationError("runtime profiles must be a mapping")
        profiles: dict[BuiltinProfileId, RunnerProfile] = {}
        for raw_id, raw_profile in raw_profiles.items():
            try:
                profile_id = BuiltinProfileId(str(raw_id))
            except ValueError as exc:
                raise ConfigurationError(
                    f"unsupported runner profile identifier: {raw_id}"
                ) from exc
            if not isinstance(raw_profile, Mapping):
                raise ConfigurationError(f"profile {profile_id.value} must be a mapping")
            profile_value = dict(raw_profile)
            if profile_id.provider is Provider.CODEX:
                _reject_unknown_fields(profile_value, _CODEX_FIELDS, profile_id.value)
                profiles[profile_id] = CodexRunnerProfile(profile_id=profile_id, **profile_value)
            else:
                _reject_unknown_fields(profile_value, _CLAUDE_FIELDS, profile_id.value)
                profiles[profile_id] = ClaudeRunnerProfile(profile_id=profile_id, **profile_value)
        return cls(profiles)


def default_profile_registry(
    *,
    codex_executable: str = "codex",
    claude_executable: str = "claude",
    mcp_executable: str = "agent-commons-mcp",
    git_executable: str = "/usr/bin/git",
    trusted_workspace: bool = False,
) -> ProfileRegistry:
    """Return conservative built-in builder and reviewer launch profiles."""

    return ProfileRegistry(
        {
            BuiltinProfileId.CODEX_BUILDER: CodexRunnerProfile(
                profile_id=BuiltinProfileId.CODEX_BUILDER,
                executable=codex_executable,
                mcp_executable=mcp_executable,
                git_executable=git_executable,
                sandbox=CodexSandbox.WORKSPACE_WRITE,
                trusted_workspace=trusted_workspace,
            ),
            BuiltinProfileId.CODEX_INDEPENDENT_REVIEWER: CodexRunnerProfile(
                profile_id=BuiltinProfileId.CODEX_INDEPENDENT_REVIEWER,
                executable=codex_executable,
                mcp_executable=mcp_executable,
                git_executable=git_executable,
                sandbox=CodexSandbox.READ_ONLY,
                trusted_workspace=trusted_workspace,
            ),
            BuiltinProfileId.CLAUDE_BUILDER: ClaudeRunnerProfile(
                profile_id=BuiltinProfileId.CLAUDE_BUILDER,
                executable=claude_executable,
                mcp_executable=mcp_executable,
                git_executable=git_executable,
                permission_mode=ClaudePermissionMode.ACCEPT_EDITS,
                trusted_workspace=trusted_workspace,
            ),
            BuiltinProfileId.CLAUDE_INDEPENDENT_REVIEWER: ClaudeRunnerProfile(
                profile_id=BuiltinProfileId.CLAUDE_INDEPENDENT_REVIEWER,
                executable=claude_executable,
                mcp_executable=mcp_executable,
                git_executable=git_executable,
                permission_mode=ClaudePermissionMode.DONT_ASK,
            ),
        }
    )
