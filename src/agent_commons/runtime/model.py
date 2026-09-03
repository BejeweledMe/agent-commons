"""Typed, allowlisted provider launch profiles.

Profiles are operator-owned configuration.  A delegation request selects one of
six built-in profile identifiers; it never supplies argv fragments, environment
variables, or provider configuration overrides.
"""

from __future__ import annotations

import json
import os
import re
import stat
import tomllib
from collections.abc import Mapping, Sequence
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
    GROK = "grok"


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
    GROK_BUILDER = "grok-builder"
    GROK_INDEPENDENT_REVIEWER = "grok-independent-reviewer"

    @property
    def provider(self) -> Provider:
        return _provider_from_profile_value(self.value)

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


class GrokSandbox(StrEnum):
    WORKSPACE = "workspace"
    READ_ONLY = "read-only"
    STRICT = "strict"


class GrokPermissionMode(StrEnum):
    ALWAYS_APPROVE = "always-approve"


def _provider_from_profile_value(value: str) -> Provider:
    """Map every built-in prefix explicitly; a new prefix never becomes Claude."""

    if value.startswith("codex-"):
        return Provider.CODEX
    if value.startswith("claude-"):
        return Provider.CLAUDE
    if value.startswith("grok-"):
        return Provider.GROK
    raise ConfigurationError(f"runner profile has an unsupported provider prefix: {value}")


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
    "mcp__agent-commons__commons_repo_files",
    "mcp__agent-commons__commons_repo_read",
    "mcp__agent-commons__commons_repo_search",
    "mcp__agent-commons__commons_check_input",
)
_CLAUDE_COMMONS_OUTCOME_TOOLS = (
    "mcp__agent-commons__commons_delegation_input_needed",
    "mcp__agent-commons__commons_finalize_review",
    "mcp__agent-commons__commons_succeed_delegation",
    "mcp__agent-commons__commons_delegation_needs_operator",
    "mcp__agent-commons__commons_request_input",
    "mcp__agent-commons__commons_share_progress",
    "mcp__agent-commons__commons_report_blocker",
    "mcp__agent-commons__commons_ack_input",
    "mcp__agent-commons__commons_ack_control",
)
#: The main chat, from the worker's side.  Narrowable, unlike the outcome
#: tools: a role configured not to talk is a narrower role, while a role that
#: cannot report a result is a broken one.
_CLAUDE_COMMONS_CHAT_TOOLS = (
    "mcp__agent-commons__commons_list_my_threads",
    "mcp__agent-commons__commons_reply_thread",
)
_CLAUDE_COMMONS_REVIEW_TOOLS = ("mcp__agent-commons__commons_record_verification",)
_CLAUDE_COMMONS_VERIFICATION_TOOLS = ("mcp__agent-commons__commons_record_verification",)
#: Staff-changing tools, keyed by the standing grant *and its level*.  A run
#: acting for no role, or for a role at `deny`, receives none of them: the grant
#: is the switch, so least privilege is the default rather than a check each
#: tool performs on itself.
#:
#: `ask` gets the propose tool, not the record tool.  Handing a role at `ask` the
#: tool that records directly produces a tool that always refuses -- a surface
#: that reads as working and cannot be.
_CLAUDE_COMMONS_GOVERNANCE_TOOLS: dict[tuple[str, str], str] = {
    ("create_roles", "auto"): "mcp__agent-commons__commons_create_agent",
    ("create_roles", "ask"): "mcp__agent-commons__commons_propose_agent",
    ("retire_roles", "auto"): "mcp__agent-commons__commons_retire_agent",
    ("open_links", "auto"): "mcp__agent-commons__commons_open_agent_link",
}
_WORKER_PURPOSES = frozenset({"implementation", "independent_review", "verification"})
_MCP_TOOL_PREFIX = "mcp__agent-commons__"
_CODEX_MCP_SERVER = "agent-commons"
_GROK_MCP_SERVER = "agent-commons"
_GROK_MACOS_READ_ONLY_SANDBOX = "agent-commons-read-only-macos-v1"
_GROK_MACOS_READ_ONLY_POLICY = {
    "extends": "read-only",
    "restrict_network": False,
}
_GROK_EXTRA_ENVIRONMENT = MappingProxyType(
    {
        # Grok 1.0.13 accepts --no-auto-update, but the environment backstop
        # also covers builds where that documented flag is hidden or drifts.
        "GROK_DISABLE_AUTOUPDATER": "1",
        # Agent Commons performs a stricter pre-launch discovery check than
        # Grok's interactive folder-trust prompt: exactly one broker-owned MCP
        # server, no hooks/plugins/LSP servers, and no invalid MCP entries.
        # Disable the native prompt/gate only for this sanitized child process
        # so a fresh isolated canary can load the project-scoped managed MCP
        # block without persisting a broad trust grant in Grok's user store.
        "GROK_FOLDER_TRUST": "0",
        "GROK_MEMORY": "0",
        "GROK_WORKFLOWS": "0",
        # Provider-compat discovery is ambient host configuration.  Native
        # Grok MCP/hooks/plugins are checked by the fixed `inspect --json`
        # initialization probe immediately before every launch.
        "GROK_CLAUDE_AGENTS_ENABLED": "false",
        "GROK_CLAUDE_HOOKS_ENABLED": "false",
        "GROK_CLAUDE_MCPS_ENABLED": "false",
        "GROK_CLAUDE_RULES_ENABLED": "false",
        "GROK_CLAUDE_SKILLS_ENABLED": "false",
        "GROK_CURSOR_AGENTS_ENABLED": "false",
        "GROK_CURSOR_HOOKS_ENABLED": "false",
        "GROK_CURSOR_MCPS_ENABLED": "false",
        "GROK_CURSOR_RULES_ENABLED": "false",
        "GROK_CURSOR_SKILLS_ENABLED": "false",
        # The model-facing shell receives only broker-safe host names.  Broker
        # control-plane bindings intentionally stay out of native shell
        # commands: worker outcomes must travel through the audited MCP
        # terminal tools, never through an agent-commons CLI write from a
        # model-started process.
        "GROK_CONFIG": json.dumps(
            {
                "shell_environment_policy": {
                    "include_only": [
                        "HOME",
                        "LANG",
                        "LC_ALL",
                        "LC_CTYPE",
                        "PATH",
                        "SHELL",
                        "TEMP",
                        "TMP",
                        "TMPDIR",
                        "USER",
                    ]
                }
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
    }
)
_GROK_AGENT_COMMONS_RULES = (
    "Agent Commons worker rule: use Grok MCP integrations only through the native "
    "search_tool then use_tool transport. Before any Agent Commons MCP operation, "
    "search for the exact fully-qualified agent-commons__commons_* tool name; then "
    "call use_tool with that exact tool_name and schema-matching tool_input. Your "
    "first Agent Commons action must be search_tool for "
    "agent-commons__commons_show_delegation followed by use_tool for the same tool. "
    "A prose answer or process exit without a completed terminal Agent Commons "
    "use_tool outcome is invalid."
)
_RUNNER_EXTRA_ENV_KEYS = frozenset(
    {
        *_GROK_EXTRA_ENVIRONMENT,
        "AGENT_COMMONS_GIT_EXECUTABLE",
        "AGENT_COMMONS_GROK_MCP_COMMAND",
        "AGENT_COMMONS_REPO_ROOT",
    }
)


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
    role_tools: Sequence[str] | None = None,
    role_grants: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """The profile's fixed tool set, optionally narrowed by a standing role.

    Narrowing only.  A role selection is intersected with what the profile
    already grants, so no role setting can turn a delegation tool into a wider
    capability -- the confused-deputy boundary from ADR 0004 is untouched.

    The outcome tools are exempt.  Narrowing away the ability to report a
    terminal result would produce a role that consumes its budget and exits
    without ever closing its delegation, which is a broken role rather than a
    narrower one.
    """

    generic_success = "mcp__agent-commons__commons_succeed_delegation"
    review_finalizer = "mcp__agent-commons__commons_finalize_review"
    outcome_tools = tuple(
        tool
        for tool in _CLAUDE_COMMONS_OUTCOME_TOOLS
        if tool not in {generic_success, review_finalizer}
    )
    outcome_tools += (
        (review_finalizer,)
        if profile_id.independent_reviewer and purpose == "independent_review"
        else (generic_success,)
    )
    tools = _CLAUDE_COMMONS_READ_TOOLS + outcome_tools
    tools += _CLAUDE_COMMONS_CHAT_TOOLS
    if profile_id.independent_reviewer:
        tools += (
            _CLAUDE_COMMONS_REVIEW_TOOLS
            if purpose == "independent_review"
            else _CLAUDE_COMMONS_VERIFICATION_TOOLS
        )
    tools += tuple(
        tool
        for (grant, level), tool in sorted(_CLAUDE_COMMONS_GOVERNANCE_TOOLS.items())
        if str((role_grants or {}).get(grant, "deny")) == level
    )
    if not role_tools:
        return tools
    allowed = {str(name) for name in role_tools}
    unknown = sorted(allowed - {tool.removeprefix(_MCP_TOOL_PREFIX) for tool in tools})
    if unknown:
        raise ConfigurationError(
            "role tool selection is not part of this profile: " + ", ".join(unknown)
        )
    return tuple(
        tool
        for tool in tools
        if tool in outcome_tools or tool.removeprefix(_MCP_TOOL_PREFIX) in allowed
    )


def validate_worker_scope(
    profile_id: BuiltinProfileId,
    worker_purpose: str,
    role_tools: Sequence[str] | None = None,
    role_grants: Mapping[str, str] | None = None,
) -> None:
    """Purely validate purpose and role scope before a child is allocated."""

    purpose = _profile_worker_purpose(profile_id, worker_purpose)
    _worker_tools(profile_id, purpose, role_tools, role_grants)


def profile_tool_summary() -> dict[str, dict[str, Any]]:
    """Per-profile tool sets for read-only display, in short (unprefixed) names.

    Built by the same composition as ``_worker_tools`` so the panel's Tools
    reference can never drift from what a launch actually receives.  The
    outcome tools are listed as ``fixed`` -- they are how a role hands work
    back and cannot be narrowed away -- everything else is ``narrowable``, and
    the governance tools appear under the exact ``grant:level`` that switches
    them on.
    """

    values: dict[str, dict[str, Any]] = {}
    for profile_id in BuiltinProfileId:
        purpose = "independent_review" if profile_id.independent_reviewer else "implementation"
        tools = _worker_tools(profile_id, purpose)
        values[profile_id.value] = {
            "purpose": purpose,
            "fixed": [
                tool.removeprefix(_MCP_TOOL_PREFIX)
                for tool in tools
                if tool in _CLAUDE_COMMONS_OUTCOME_TOOLS
            ],
            "narrowable": [
                tool.removeprefix(_MCP_TOOL_PREFIX)
                for tool in tools
                if tool not in _CLAUDE_COMMONS_OUTCOME_TOOLS
            ],
            "grant_tools": {
                grant + ":" + level: tool.removeprefix(_MCP_TOOL_PREFIX)
                for (grant, level), tool in sorted(_CLAUDE_COMMONS_GOVERNANCE_TOOLS.items())
            },
        }
    return values


def _resolved_worker_mcp(
    *,
    workspace_root: Path,
    state_root: Path | None,
    delegation_id: str,
    child_session_id: str | None,
    mcp_executable: str,
    git_executable: str,
    demo_unresolved_placeholder: bool = False,
) -> tuple[str, tuple[str, ...]]:
    resolved_mcp = _resolve_or_demo_placeholder(
        mcp_executable,
        workspace_root=workspace_root,
        role=ExecutableRole.MCP,
        demo_unresolved_placeholder=demo_unresolved_placeholder,
    )
    resolved_git = _resolve_or_demo_placeholder(
        git_executable,
        workspace_root=workspace_root,
        role=ExecutableRole.GIT,
        demo_unresolved_placeholder=demo_unresolved_placeholder,
    )
    effective_state_root = (
        Path(state_root if state_root is not None else workspace_root / ".agent-commons")
        .expanduser()
        .resolve()
    )
    arguments = [
        "--repo",
        str(workspace_root.resolve()),
        "--state-root",
        str(effective_state_root),
        "--delegation-id",
        delegation_id,
        "--git-executable",
        resolved_git,
    ]
    if child_session_id is not None:
        arguments.extend(("--session-id", _safe_identifier("child_session_id", child_session_id)))
    return resolved_mcp, tuple(arguments)


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


def validate_model_name(value: object) -> str | None:
    """The one check a model name passes, wherever a model is chosen.

    Exported because a model is no longer only an operator-config field: it can
    now be picked when a role is hired, and a bad pick must be refused *at that
    form* rather than an hour later from inside a launch, where the operator
    has neither the field nor the context to fix it.

    It is literally the check both runner profiles run in ``__post_init__``, so
    a name accepted here is one ``dataclasses.replace(profile, model=...)``
    will accept too -- there is one rule and no second opinion of it.  What the
    rule buys is stated plainly: the pattern requires an alphanumeric first
    character, so a leading ``-`` is not a model name, and nothing chosen on
    this path can arrive at a provider as a flag.  ``None`` passes through, and
    means the profile's own model stands.
    """

    return _safe_optional_identifier("model", value)


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


#: Argv stand-in a demo build records for an executable that failed trusted
#: resolution.  Nothing can exist below ``/dev/null``, so even if such an
#: invocation ever leaked to a real runner the launch would fail instantly
#: instead of executing something unintended.
DEMO_UNRESOLVED_EXECUTABLE = "/dev/null/agent-commons-demo-unresolved"


def _resolve_or_demo_placeholder(
    value: str,
    *,
    workspace_root: Path,
    role: ExecutableRole,
    demo_unresolved_placeholder: bool,
) -> str:
    """Resolve strictly; a demo build alone substitutes an inert placeholder.

    Exactly ``ExecutableResolutionError`` is absorbed, and only when the caller
    explicitly opted in for a run whose bound runner never starts a process.
    Every other refusal keeps its exact exception, and the default leg is a
    plain re-raise, byte-identical to calling ``resolve_trusted_executable``.
    """

    try:
        return resolve_trusted_executable(value, workspace_root=workspace_root, role=role)
    except ExecutableResolutionError:
        if not demo_unresolved_placeholder:
            raise
        return DEMO_UNRESOLVED_EXECUTABLE


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
    #: Identifies the delegation tree this launch belongs to.  Budget is charged
    #: against the tree, so a child session cannot start a fresh allowance by
    #: virtue of being new.  Optional so stored documents keep their shape.
    root_delegation_id: str | None = None

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
        if self.root_delegation_id is not None:
            _safe_identifier("root_delegation_id", self.root_delegation_id)

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
        if self.root_delegation_id is not None:
            result["root_delegation_id"] = self.root_delegation_id
        return result

    @property
    def budget_scope(self) -> str:
        """The tree a launch is charged against; a lone delegation is its own root."""

        return self.root_delegation_id or self.delegation_id


@dataclass(frozen=True, slots=True)
class RunnerInvocation:
    """A closed process invocation; prompt material remains ephemeral."""

    provider: Provider
    profile_id: BuiltinProfileId
    argv: tuple[str, ...]
    stdin: bytes
    extra_env: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        if not self.argv or any(not isinstance(item, str) or "\x00" in item for item in self.argv):
            raise ValidationError("runner argv is invalid")
        if not isinstance(self.stdin, bytes):
            raise TypeError("runner stdin must be bytes")
        values = dict(self.extra_env or {})
        unknown = sorted(set(values) - _RUNNER_EXTRA_ENV_KEYS)
        if unknown:
            raise ValidationError(
                "runner environment contains unsupported keys: " + ", ".join(unknown)
            )
        if any(not isinstance(value, str) or "\x00" in value for value in values.values()):
            raise ValidationError("runner environment contains an invalid value")
        object.__setattr__(self, "extra_env", MappingProxyType(dict(sorted(values.items()))))


def fixed_profile_environment(profile: RunnerProfile) -> Mapping[str, str]:
    """Return provider-owned process settings, never caller-owned overrides."""

    return _GROK_EXTRA_ENVIRONMENT if profile.provider is Provider.GROK else MappingProxyType({})


def invocation_instruction_bytes(invocation: RunnerInvocation) -> bytes:
    """Recover the exact prompt bytes from the provider's fixed transport."""

    if invocation.provider is not Provider.GROK:
        return invocation.stdin
    try:
        position = invocation.argv.index("-p")
        prompt = invocation.argv[position + 1]
    except (ValueError, IndexError) as exc:
        raise ConfigurationError("Grok invocation has no fixed headless prompt") from exc
    if invocation.argv.count("-p") != 1 or invocation.stdin:
        raise ConfigurationError("Grok invocation has an ambiguous instruction transport")
    return prompt.encode("utf-8")


class RunnerProfile(Protocol):
    profile_id: BuiltinProfileId
    provider: Provider
    trusted_workspace: bool

    @property
    def supports_budget(self) -> bool: ...

    def build_invocation(
        self,
        instruction: str,
        *,
        workspace_root: Path,
        state_root: Path | None = None,
        delegation_id: str | None = None,
        child_session_id: str | None = None,
        max_budget_microusd: int | None = None,
        worker_purpose: str | None = None,
        role_tools: Sequence[str] | None = None,
        role_grants: Mapping[str, str] | None = None,
    ) -> RunnerInvocation: ...


def validate_profile_launch_boundary(profile: RunnerProfile) -> None:
    """Validate the provider profile's fixed host-isolation requirement.

    This check is intentionally reusable by static launch planning and final
    invocation construction.  The former keeps an invalid host boundary ahead
    of auth/initialization probes and durable launch state; the latter remains
    the fail-closed last line of defence for direct profile callers.
    """

    if profile.trusted_workspace:
        return
    if profile.provider is Provider.CODEX:
        raise ConfigurationError(
            "Codex runtime requires explicit trusted_workspace opt-in or external isolation"
        )
    if profile.provider is Provider.GROK:
        if not profile.profile_id.independent_reviewer:
            raise ConfigurationError(
                "writable Grok runtime requires explicit trusted_workspace opt-in or external "
                "isolation"
            )
        return
    if profile.provider is Provider.CLAUDE:
        if not profile.profile_id.independent_reviewer:
            raise ConfigurationError(
                "writable Claude runtime requires explicit trusted_workspace opt-in or "
                "external isolation"
            )
        return
    raise ConfigurationError("runner profile provider is unsupported")  # pragma: no cover


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
        child_session_id: str | None = None,
        max_budget_microusd: int | None = None,
        worker_purpose: str | None = None,
        role_tools: Sequence[str] | None = None,
        role_grants: Mapping[str, str] | None = None,
        demo_unresolved_placeholder: bool = False,
    ) -> RunnerInvocation:
        validate_profile_launch_boundary(self)
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
            child_session_id=child_session_id,
            mcp_executable=self.mcp_executable,
            git_executable=self.git_executable,
            demo_unresolved_placeholder=demo_unresolved_placeholder,
        )
        enabled_tools = tuple(
            tool.removeprefix(_MCP_TOOL_PREFIX)
            for tool in _worker_tools(self.profile_id, purpose, role_tools, role_grants)
        )
        config_prefix = f"mcp_servers.{_CODEX_MCP_SERVER}"
        argv = [
            _resolve_or_demo_placeholder(
                self.executable,
                workspace_root=workspace_root,
                role=ExecutableRole.PROVIDER,
                demo_unresolved_placeholder=demo_unresolved_placeholder,
            ),
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
        child_session_id: str | None = None,
        max_budget_microusd: int | None = None,
        worker_purpose: str | None = None,
        role_tools: Sequence[str] | None = None,
        role_grants: Mapping[str, str] | None = None,
        demo_unresolved_placeholder: bool = False,
    ) -> RunnerInvocation:
        if delegation_id is None:
            raise ConfigurationError("Claude runtime requires an exact delegation binding")
        _safe_identifier("delegation_id", delegation_id)
        purpose = _profile_worker_purpose(self.profile_id, worker_purpose)
        validate_profile_launch_boundary(self)
        effective_budget = self.max_budget_microusd
        if max_budget_microusd is not None:
            effective_budget = (
                max_budget_microusd
                if effective_budget is None
                else min(effective_budget, max_budget_microusd)
            )
        provider_executable = _resolve_or_demo_placeholder(
            self.executable,
            workspace_root=workspace_root,
            role=ExecutableRole.PROVIDER,
            demo_unresolved_placeholder=demo_unresolved_placeholder,
        )
        mcp_executable, mcp_args = _resolved_worker_mcp(
            workspace_root=workspace_root,
            state_root=state_root,
            delegation_id=delegation_id,
            child_session_id=child_session_id,
            mcp_executable=self.mcp_executable,
            git_executable=self.git_executable,
            demo_unresolved_placeholder=demo_unresolved_placeholder,
        )
        # Pass the sole MCP server as immutable argv material.  Strict mode
        # excludes ambient user/project MCP configuration.  The broker-selected
        # child session is also carried explicitly in argv because providers do
        # not promise to forward their own environment to MCP children.
        mcp_config = json.dumps(
            {
                "mcpServers": {
                    "agent-commons": {
                        "type": "stdio",
                        "command": mcp_executable,
                        "args": list(mcp_args),
                        # Claude Code otherwise starts non-interactive model
                        # work while a local stdio server is still pending.
                        # This sole fixed server is mandatory for canonical
                        # completion, so its tools must be present in the first
                        # prompt rather than discovered after a prose-only exit.
                        "alwaysLoad": True,
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
        allowed_tools = _worker_tools(self.profile_id, purpose, role_tools, role_grants)
        if self.profile_id.independent_reviewer:
            argv.extend(
                (
                    "--tools",
                    # Claude Code 2.1.220 treats an empty built-in tool pool as
                    # disabling MCP discovery too, even when the exact MCP
                    # tools are present in --allowed-tools.  ToolSearch is the
                    # narrow discovery gateway: the allowlist below still
                    # decides which worker-scoped MCP tools can be called, and
                    # the native read/write/shell/web/subagent tools remain
                    # explicitly denied.
                    "ToolSearch",
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


@dataclass(frozen=True, slots=True)
class GrokRunnerProfile:
    profile_id: BuiltinProfileId
    executable: str = "grok"
    mcp_executable: str = "agent-commons-mcp"
    git_executable: str = "/usr/bin/git"
    model: str | None = None
    sandbox: GrokSandbox = GrokSandbox.WORKSPACE
    permission_mode: GrokPermissionMode = GrokPermissionMode.ALWAYS_APPROVE
    max_turns: int | None = None
    trusted_workspace: bool = False

    def __post_init__(self) -> None:
        if self.profile_id.provider is not Provider.GROK:
            raise ConfigurationError("Grok profile requires a Grok profile identifier")
        object.__setattr__(self, "executable", _safe_executable(self.executable))
        object.__setattr__(self, "mcp_executable", _safe_executable(self.mcp_executable))
        object.__setattr__(self, "git_executable", _safe_executable(self.git_executable))
        object.__setattr__(self, "model", validate_model_name(self.model))
        try:
            object.__setattr__(self, "sandbox", GrokSandbox(self.sandbox))
            object.__setattr__(
                self,
                "permission_mode",
                GrokPermissionMode(self.permission_mode),
            )
        except ValueError as exc:
            raise ConfigurationError("Grok profile has an unsupported launch mode") from exc
        if self.max_turns is not None and (
            isinstance(self.max_turns, bool)
            or not isinstance(self.max_turns, int)
            or not 1 <= self.max_turns <= 10_000
        ):
            raise ConfigurationError("Grok max_turns must be an integer between 1 and 10000")
        if not isinstance(self.trusted_workspace, bool):
            raise ConfigurationError("trusted_workspace must be boolean")
        if self.profile_id.independent_reviewer and self.sandbox is not GrokSandbox.READ_ONLY:
            raise ConfigurationError("independent Grok reviewer must use read-only sandbox")

    @property
    def provider(self) -> Provider:
        return Provider.GROK

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
        child_session_id: str | None = None,
        max_budget_microusd: int | None = None,
        worker_purpose: str | None = None,
        role_tools: Sequence[str] | None = None,
        role_grants: Mapping[str, str] | None = None,
        demo_unresolved_placeholder: bool = False,
    ) -> RunnerInvocation:
        validate_profile_launch_boundary(self)
        if delegation_id is None:
            raise ConfigurationError("Grok runtime requires an exact delegation binding")
        _safe_identifier("delegation_id", delegation_id)
        purpose = _profile_worker_purpose(self.profile_id, worker_purpose)
        if max_budget_microusd is not None:
            raise ConfigurationError("Grok Build cannot enforce a monetary launch budget")
        provider_executable = _resolve_or_demo_placeholder(
            self.executable,
            workspace_root=workspace_root,
            role=ExecutableRole.PROVIDER,
            demo_unresolved_placeholder=demo_unresolved_placeholder,
        )
        mcp_executable, mcp_args = _resolved_worker_mcp(
            workspace_root=workspace_root,
            state_root=state_root,
            delegation_id=delegation_id,
            child_session_id=child_session_id,
            mcp_executable=self.mcp_executable,
            git_executable=self.git_executable,
            demo_unresolved_placeholder=demo_unresolved_placeholder,
        )
        allowed_tools = _worker_tools(
            self.profile_id,
            purpose,
            role_tools,
            role_grants,
        )
        sandbox = _grok_launch_sandbox(self)
        if sandbox == _GROK_MACOS_READ_ONLY_SANDBOX:
            _validate_grok_macos_read_only_profile(workspace_root)
        argv = [
            provider_executable,
            "--no-auto-update",
            "--no-alt-screen",
            "--output-format",
            "json",
            "--always-approve",
            "--sandbox",
            sandbox,
            "--cwd",
            str(workspace_root.resolve()),
            "--no-plan",
            "--no-subagents",
            "--disable-web-search",
            "--rules",
            _GROK_AGENT_COMMONS_RULES,
            "--verbatim",
        ]
        if self.model is not None:
            argv.extend(("--model", self.model))
        if self.max_turns is not None:
            argv.extend(("--max-turns", str(self.max_turns)))
        if self.profile_id.independent_reviewer:
            native_tools = "read_file,grep,list_dir,search_tool,use_tool"
            denied_tools = (
                "run_terminal_cmd,search_replace,write_file,task,Agent,web_search,web_fetch"
            )
        else:
            native_tools = "grep,read_file,search_replace,write_file,list_dir,search_tool,use_tool"
            denied_tools = "run_terminal_cmd,task,Agent,web_search,web_fetch"
        argv.extend(("--tools", native_tools, "--disallowed-tools", denied_tools))
        for tool in allowed_tools:
            short_name = tool.removeprefix(_MCP_TOOL_PREFIX)
            argv.extend(("--allow", f"MCPTool({_GROK_MCP_SERVER}__{short_name})"))

        # Grok Build 1.0.13 explicitly does not read piped stdin as a prompt.
        # Its documented --prompt-file cannot consume '-', and the broker has
        # no pre-existing owned prompt-file lifecycle.  The fixed -p argument
        # is therefore the only real headless transport.  The one-megabyte
        # product limit still applies, though a host with a smaller ARG_MAX may
        # refuse a very large prompt before Grok starts.
        prompt = _instruction_bytes(instruction).decode("utf-8")
        argv.extend(("-p", prompt))
        extra_env = {
            **_GROK_EXTRA_ENVIRONMENT,
            "AGENT_COMMONS_GIT_EXECUTABLE": str(mcp_args[mcp_args.index("--git-executable") + 1]),
            "AGENT_COMMONS_GROK_MCP_COMMAND": mcp_executable,
            "AGENT_COMMONS_REPO_ROOT": str(workspace_root.resolve()),
        }
        return RunnerInvocation(
            provider=self.provider,
            profile_id=self.profile_id,
            argv=tuple(argv),
            stdin=b"",
            extra_env=extra_env,
        )


def _grok_launch_sandbox(
    profile: GrokRunnerProfile,
    *,
    platform_name: str | None = None,
) -> str:
    """Project Grok's read-only policy around a macOS 1.0.x startup defect.

    Grok's child-network restriction is documented as a no-op on macOS, but
    1.0.x still resolves container-runtime socket denies when the flag is set.
    A symlinked system socket then aborts before the model or MCP transport can
    start.  The managed custom profile inherits every filesystem grant and
    deny from ``read-only`` and disables only that unavailable macOS network
    mechanism.  Linux keeps the built-in profile and its seccomp restriction.
    """

    host = os.sys.platform if platform_name is None else platform_name
    if (
        host == "darwin"
        and profile.profile_id.independent_reviewer
        and profile.sandbox is GrokSandbox.READ_ONLY
    ):
        return _GROK_MACOS_READ_ONLY_SANDBOX
    return profile.sandbox.value


def _sandbox_profile(path: Path, name: str) -> object:
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise ConfigurationError("Grok sandbox policy must be a regular file")
    try:
        value = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError("Grok sandbox policy is unreadable or invalid") from exc
    profiles = value.get("profiles")
    if not isinstance(profiles, dict):
        return None
    return profiles.get(name)


def _validate_grok_macos_read_only_profile(workspace_root: Path) -> None:
    project_policy = _sandbox_profile(
        workspace_root / ".grok" / "sandbox.toml",
        _GROK_MACOS_READ_ONLY_SANDBOX,
    )
    if project_policy != _GROK_MACOS_READ_ONLY_POLICY:
        raise ConfigurationError(
            "managed Grok macOS read-only sandbox policy is missing or invalid"
        )

    # Grok gives a same-named user profile precedence over the project profile.
    # Reject a conflicting definition before launch rather than trusting the
    # provider's startup warning or silently accepting a broader policy.
    user_policy = _sandbox_profile(
        Path.home() / ".grok" / "sandbox.toml",
        _GROK_MACOS_READ_ONLY_SANDBOX,
    )
    if user_policy is not None and user_policy != _GROK_MACOS_READ_ONLY_POLICY:
        raise ConfigurationError("user Grok sandbox policy conflicts with managed reviewer policy")


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
_GROK_FIELDS = frozenset(
    {
        "executable",
        "mcp_executable",
        "git_executable",
        "model",
        "sandbox",
        "permission_mode",
        "max_turns",
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
            elif profile_id.provider is Provider.CLAUDE:
                _reject_unknown_fields(profile_value, _CLAUDE_FIELDS, profile_id.value)
                profiles[profile_id] = ClaudeRunnerProfile(profile_id=profile_id, **profile_value)
            elif profile_id.provider is Provider.GROK:
                _reject_unknown_fields(profile_value, _GROK_FIELDS, profile_id.value)
                profiles[profile_id] = GrokRunnerProfile(profile_id=profile_id, **profile_value)
            else:  # pragma: no cover - Provider is a closed enum
                raise ConfigurationError("runner profile provider is unsupported")
        return cls(profiles)


def default_profile_registry(
    *,
    codex_executable: str = "codex",
    claude_executable: str = "claude",
    grok_executable: str = "grok",
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
            BuiltinProfileId.GROK_BUILDER: GrokRunnerProfile(
                profile_id=BuiltinProfileId.GROK_BUILDER,
                executable=grok_executable,
                mcp_executable=mcp_executable,
                git_executable=git_executable,
                sandbox=GrokSandbox.WORKSPACE,
                trusted_workspace=trusted_workspace,
            ),
            BuiltinProfileId.GROK_INDEPENDENT_REVIEWER: GrokRunnerProfile(
                profile_id=BuiltinProfileId.GROK_INDEPENDENT_REVIEWER,
                executable=grok_executable,
                mcp_executable=mcp_executable,
                git_executable=git_executable,
                sandbox=GrokSandbox.READ_ONLY,
            ),
        }
    )
