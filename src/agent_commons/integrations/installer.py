"""Safe, idempotent installation of project-local agent guidance.

The installer owns only its marked blocks in client instruction files.  It
never invokes Git and never rewrites project-authored content outside those
blocks.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import tempfile
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Literal

import yaml

from agent_commons.core.ids import is_typed_id, new_sortable_id
from agent_commons.errors import ConfigurationError

MANAGED_BLOCK_START = "<!-- agent-commons:managed:start -->"
MANAGED_BLOCK_END = "<!-- agent-commons:managed:end -->"
SUPPORTED_INTEGRATIONS = ("codex", "claude")

_INTEGRATION_TARGETS = {
    "codex": ("AGENTS.md", "AGENTS_BLOCK.md"),
    "claude": ("CLAUDE.md", "CLAUDE_BLOCK.md"),
}
_INTEGRATION_SKILL_ROOTS = {
    "codex": Path(".agents") / "skills",
    "claude": Path(".claude") / "skills",
}
_COMMON_SKILLS = (
    "commons-start",
    "commons-coordinate",
    "commons-share",
    "commons-review",
    "commons-record",
    "commons-handoff",
    "commons-delegate",
)
_SKILL_FILES = (Path("SKILL.md"), Path("agents") / "openai.yaml")
_WORKSPACE_DIRECTORIES = ("events", "manifests", "blobs", "cache")


@dataclass(frozen=True, slots=True)
class FileChange:
    """One planned or completed project-relative file operation."""

    path: str
    status: Literal["created", "updated", "unchanged"]


@dataclass(frozen=True, slots=True)
class InstallationReport:
    """Deterministic summary returned by :func:`initialize_workspace`."""

    workspace: str
    workspace_id: str
    integrations: tuple[str, ...]
    changes: tuple[FileChange, ...]

    @property
    def changed(self) -> bool:
        return any(change.status != "unchanged" for change in self.changes)


@dataclass(frozen=True, slots=True)
class _PlannedWrite:
    path: Path
    content: str
    relative_path: str
    status: Literal["created", "updated", "unchanged"]
    expected_content: str | None


def _template_text(name: str) -> str:
    template = resources.files("agent_commons").joinpath("resources", "templates", name)
    try:
        value = template.read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError) as exc:
        raise ConfigurationError(f"packaged integration template is missing: {name}") from exc
    return value if value.endswith("\n") else value + "\n"


def _skill_text(skill_name: str, relative_path: Path) -> str:
    skill = resources.files("agent_commons").joinpath(
        "resources", "skills", skill_name, *relative_path.parts
    )
    try:
        value = skill.read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError) as exc:
        location = (Path("resources") / "skills" / skill_name / relative_path).as_posix()
        raise ConfigurationError(f"packaged integration skill is missing: {location}") from exc
    return value if value.endswith("\n") else value + "\n"


def _normalize_integrations(integrations: Iterable[str] | str) -> tuple[str, ...]:
    requested = (integrations,) if isinstance(integrations, str) else tuple(integrations)
    normalized: list[str] = []
    for raw_name in requested:
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise ConfigurationError("integration names must be non-empty strings")
        name = raw_name.strip().lower()
        if name not in _INTEGRATION_TARGETS:
            supported = ", ".join(SUPPORTED_INTEGRATIONS)
            raise ConfigurationError(
                f"unsupported integration {raw_name!r}; choose from {supported}"
            )
        if name not in normalized:
            normalized.append(name)
    return tuple(normalized)


def _normalize_workspace_name(value: object) -> str:
    if not isinstance(value, str):
        raise ConfigurationError("workspace_name must be a string")
    normalized = value.strip()
    if not normalized:
        raise ConfigurationError("workspace_name must not be empty")
    if len(normalized) > 256 or any(ord(character) < 32 for character in normalized):
        raise ConfigurationError("workspace_name contains unsupported control characters")
    return normalized


def _validate_regular_or_missing(path: Path, *, label: str) -> None:
    if path.is_symlink():
        raise ConfigurationError(f"refusing to manage symlinked {label}: {path.name}")
    if path.exists() and not path.is_file():
        raise ConfigurationError(f"expected a regular file for {label}: {path.name}")


def _validate_directory_chain(root: Path, directory: Path) -> None:
    try:
        relative = directory.relative_to(root)
    except ValueError as exc:
        raise ConfigurationError("managed integration path escapes the project root") from exc
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ConfigurationError(
                f"refusing to use symlinked integration directory: "
                f"{current.relative_to(root).as_posix()}"
            )
        if current.exists() and not current.is_dir():
            raise ConfigurationError(
                f"integration parent is not a directory: {current.relative_to(root).as_posix()}"
            )


def _ensure_directory_chain(root: Path, directory: Path) -> None:
    _validate_directory_chain(root, directory)
    current = root
    for part in directory.relative_to(root).parts:
        current = current / part
        if not current.exists():
            current.mkdir(mode=0o755)
        _validate_directory_chain(root, current)


def _validate_workspace_path(root: Path, workspace: Path) -> None:
    if workspace.is_symlink():
        raise ConfigurationError("refusing to initialize a symlinked .agent-commons directory")
    if workspace.exists() and not workspace.is_dir():
        raise ConfigurationError(".agent-commons exists but is not a directory")
    for name in _WORKSPACE_DIRECTORIES:
        child = workspace / name
        if child.is_symlink():
            raise ConfigurationError(f"refusing to use symlinked workspace directory: {name}")
        if child.exists() and not child.is_dir():
            raise ConfigurationError(f"workspace path is not a directory: {name}")
    try:
        workspace.relative_to(root)
    except ValueError as exc:  # defensive: callers never supply this path directly
        raise ConfigurationError("workspace must remain inside the project root") from exc


def _read_utf8(path: Path) -> str:
    try:
        # ``newline=""`` is intentional: project-authored CRLF/LF bytes outside
        # the managed block must survive a replacement unchanged.
        with path.open("r", encoding="utf-8", newline="") as handle:
            return handle.read()
    except UnicodeDecodeError as exc:
        raise ConfigurationError(f"managed instruction file is not UTF-8: {path.name}") from exc


def _managed_block(body: str) -> str:
    return f"{MANAGED_BLOCK_START}\n{body.rstrip()}\n{MANAGED_BLOCK_END}"


def _merge_managed_block(original: str, body: str, *, filename: str) -> str:
    start_count = original.count(MANAGED_BLOCK_START)
    end_count = original.count(MANAGED_BLOCK_END)
    if start_count == 0 and end_count == 0:
        separator = "" if not original else ("\n" if original.endswith("\n") else "\n\n")
        return f"{original}{separator}{_managed_block(body)}\n"
    if start_count != 1 or end_count != 1:
        raise ConfigurationError(
            f"{filename} contains malformed or duplicate Agent Commons managed markers"
        )

    start = original.index(MANAGED_BLOCK_START)
    end = original.index(MANAGED_BLOCK_END)
    if end < start:
        raise ConfigurationError(f"{filename} has Agent Commons managed markers in reverse order")
    end += len(MANAGED_BLOCK_END)
    return f"{original[:start]}{_managed_block(body)}{original[end:]}"


def _plan_owned_file(
    path: Path,
    content: str,
    *,
    relative_path: str,
    replace_existing: bool,
    replacement_option: str = "replace_onboarding",
) -> _PlannedWrite:
    _validate_regular_or_missing(path, label=relative_path)
    if not path.exists():
        return _PlannedWrite(path, content, relative_path, "created", None)
    current = _read_utf8(path)
    if current == content:
        return _PlannedWrite(path, content, relative_path, "unchanged", current)
    if not replace_existing:
        raise ConfigurationError(
            f"refusing to replace locally modified {relative_path}; "
            f"pass {replacement_option}=True only after reviewing the canonical content"
        )
    return _PlannedWrite(path, content, relative_path, "updated", current)


def _plan_integration_file(
    root: Path,
    *,
    integration: str,
    target_name: str,
    template_name: str,
) -> _PlannedWrite:
    target = root / target_name
    _validate_regular_or_missing(target, label=target_name)
    target_exists = target.exists()
    original = _read_utf8(target) if target_exists else ""
    merged = _merge_managed_block(original, _template_text(template_name), filename=target_name)
    status: Literal["created", "updated", "unchanged"]
    if not target_exists:
        status = "created"
    elif merged == original:
        status = "unchanged"
    else:
        status = "updated"
    expected = original if target_exists else None
    return _PlannedWrite(target, merged, target_name, status, expected)


def _validate_existing_workspace_config(content: str) -> str:
    try:
        value = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ConfigurationError("existing .agent-commons/workspace.yaml is invalid YAML") from exc
    if not isinstance(value, dict):
        raise ConfigurationError("existing workspace configuration must be a mapping")
    if value.get("schema") != "agent-commons.workspace.v1":
        raise ConfigurationError("existing workspace configuration has an unsupported schema")
    workspace_id = value.get("workspace_id")
    if not is_typed_id(workspace_id, "workspace"):
        raise ConfigurationError("existing workspace configuration has an invalid workspace_id")
    workspace = value.get("workspace")
    if not isinstance(workspace, dict) or not isinstance(workspace.get("name"), str):
        raise ConfigurationError("existing workspace configuration has no workspace name")
    _normalize_workspace_name(workspace["name"])
    return workspace_id


def _verify_preimage(item: _PlannedWrite, *, phase: str) -> None:
    if item.expected_content is None:
        if item.path.exists() or item.path.is_symlink():
            raise ConfigurationError(
                f"installation target appeared during {phase}: {item.relative_path}"
            )
        return
    _validate_regular_or_missing(item.path, label=item.relative_path)
    if not item.path.exists() or _read_utf8(item.path) != item.expected_content:
        raise ConfigurationError(
            f"installation target changed during {phase}: {item.relative_path}"
        )


def _atomic_write(item: _PlannedWrite) -> None:
    path = item.path
    content = item.content
    _verify_preimage(item, phase="publication")
    encoded = content.encode("utf-8")
    previous_mode = None
    if path.exists():
        previous_mode = stat.S_IMODE(path.stat().st_mode)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, previous_mode if previous_mode is not None else 0o644)
        # Recheck after the temporary file is durable and immediately before
        # publication. The outer installer lock makes this an interprocess CAS
        # for every cooperating Agent Commons initializer.
        _verify_preimage(item, phase="publication")
        if item.expected_content is None:
            try:
                os.link(temporary_path, path, follow_symlinks=False)
            except FileExistsError as exc:
                raise ConfigurationError(
                    f"installation target appeared during publication: {item.relative_path}"
                ) from exc
            temporary_path.unlink()
        else:
            os.replace(temporary_path, path)
        directory_descriptor = os.open(
            path.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


@contextmanager
def _installation_lock(root: Path):
    """Serialize complete installer transactions for one resolved project root."""

    user_id = os.getuid() if hasattr(os, "getuid") else 0
    lock_root = Path(tempfile.gettempdir()) / f"agent-commons-installer-locks-{user_id}"
    if lock_root.is_symlink():
        raise ConfigurationError("installer lock directory must not be a symlink")
    lock_root.mkdir(mode=0o700, exist_ok=True)
    lock_stat = lock_root.stat()
    if not lock_root.is_dir() or (hasattr(lock_stat, "st_uid") and lock_stat.st_uid != user_id):
        raise ConfigurationError("installer lock directory is not owned safely")
    try:
        lock_root.chmod(0o700)
    except OSError:
        pass
    digest = hashlib.sha256(os.fsencode(root)).hexdigest()
    lock_path = lock_root / f"{digest}.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise ConfigurationError("installer lock file cannot be opened safely") from exc
    try:
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _verify_preconditions(planned: Iterable[_PlannedWrite]) -> None:
    """Fail before publication if a target changed after the initial read."""

    for item in planned:
        _verify_preimage(item, phase="preflight")


def _initialize_workspace_locked(
    project_root: str | Path,
    *,
    integrations: Iterable[str] | str = SUPPORTED_INTEGRATIONS,
    workspace_name: str | None = None,
    replace_onboarding: bool = False,
    replace_skills: bool = False,
) -> InstallationReport:
    """Plan and publish one initialization while holding its project lock.

    Existing ``AGENTS.md`` and ``CLAUDE.md`` content is preserved byte for byte
    outside one idempotent managed block.  Workspace configuration is created
    once and remains operator-owned.  A locally modified canonical onboarding
    file is never overwritten unless ``replace_onboarding`` is explicitly set.
    Project-local skills are installed for each selected client. Locally changed
    skill files fail closed unless ``replace_skills`` is explicitly set.

    The function performs a complete conflict preflight before publishing any
    file.  It does not invoke Git or alter the Git index, branches, or remotes.
    """

    root = Path(project_root).expanduser().resolve()
    if not root.exists():
        raise ConfigurationError("project root does not exist")
    if not root.is_dir():
        raise ConfigurationError("project root is not a directory")

    selected = _normalize_integrations(integrations)
    workspace = root / ".agent-commons"
    _validate_workspace_path(root, workspace)

    name = _normalize_workspace_name(root.name if workspace_name is None else workspace_name)

    onboarding = _template_text("ONBOARDING.md")
    workspace_template = _template_text("workspace.yaml")
    if workspace_template.count("{{WORKSPACE_NAME_JSON}}") != 1:
        raise ConfigurationError("workspace template must contain exactly one name placeholder")
    if workspace_template.count("{{WORKSPACE_ID_JSON}}") != 1:
        raise ConfigurationError("workspace template must contain exactly one ID placeholder")
    planned: list[_PlannedWrite] = [
        _plan_owned_file(
            workspace / "ONBOARDING.md",
            onboarding,
            relative_path=".agent-commons/ONBOARDING.md",
            replace_existing=replace_onboarding,
        ),
        _plan_owned_file(
            workspace / ".gitignore",
            _template_text("WORKSPACE_GITIGNORE"),
            relative_path=".agent-commons/.gitignore",
            replace_existing=replace_onboarding,
        ),
    ]

    workspace_config_path = workspace / "workspace.yaml"
    _validate_regular_or_missing(workspace_config_path, label=".agent-commons/workspace.yaml")
    if workspace_config_path.exists():
        existing_workspace_config = _read_utf8(workspace_config_path)
        workspace_id = _validate_existing_workspace_config(existing_workspace_config)
        planned.append(
            _PlannedWrite(
                workspace_config_path,
                existing_workspace_config,
                ".agent-commons/workspace.yaml",
                "unchanged",
                existing_workspace_config,
            )
        )
    else:
        workspace_id = new_sortable_id("workspace")
        workspace_config = workspace_template.replace(
            "{{WORKSPACE_NAME_JSON}}", json.dumps(name)
        ).replace("{{WORKSPACE_ID_JSON}}", json.dumps(workspace_id))
        planned.append(
            _PlannedWrite(
                workspace_config_path,
                workspace_config,
                ".agent-commons/workspace.yaml",
                "created",
                None,
            )
        )

    for integration in selected:
        target_name, template_name = _INTEGRATION_TARGETS[integration]
        planned.append(
            _plan_integration_file(
                root,
                integration=integration,
                target_name=target_name,
                template_name=template_name,
            )
        )
        skill_root = _INTEGRATION_SKILL_ROOTS[integration]
        for skill_name in _COMMON_SKILLS:
            for skill_file in _SKILL_FILES:
                relative = skill_root / skill_name / skill_file
                target = root / relative
                _validate_directory_chain(root, target.parent)
                planned.append(
                    _plan_owned_file(
                        target,
                        _skill_text(skill_name, skill_file),
                        relative_path=relative.as_posix(),
                        replace_existing=replace_skills,
                        replacement_option="replace_skills",
                    )
                )

    workspace.mkdir(mode=0o755, parents=False, exist_ok=True)
    for directory in _WORKSPACE_DIRECTORIES:
        (workspace / directory).mkdir(mode=0o755, exist_ok=True)
    _verify_preconditions(planned)
    for item in planned:
        if item.status != "unchanged":
            _ensure_directory_chain(root, item.path.parent)
            _atomic_write(item)

    return InstallationReport(
        workspace=".agent-commons",
        workspace_id=workspace_id,
        integrations=selected,
        changes=tuple(FileChange(item.relative_path, item.status) for item in planned),
    )


def initialize_workspace(
    project_root: str | Path,
    *,
    integrations: Iterable[str] | str = SUPPORTED_INTEGRATIONS,
    workspace_name: str | None = None,
    replace_onboarding: bool = False,
    replace_skills: bool = False,
) -> InstallationReport:
    """Initialize shared guidance and selected client integrations safely.

    The complete plan, preflight, and publication transaction is serialized
    across Agent Commons processes for the resolved project root. Every file is
    also compared with its planned preimage immediately before atomic replace,
    so a concurrent project edit is rejected rather than overwritten.
    """

    root = Path(project_root).expanduser().resolve()
    if not root.exists():
        raise ConfigurationError("project root does not exist")
    if not root.is_dir():
        raise ConfigurationError("project root is not a directory")
    with _installation_lock(root):
        return _initialize_workspace_locked(
            root,
            integrations=integrations,
            workspace_name=workspace_name,
            replace_onboarding=replace_onboarding,
            replace_skills=replace_skills,
        )
