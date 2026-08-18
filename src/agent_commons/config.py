"""Filesystem layout for one project-local Agent Commons workspace."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from agent_commons.core.canonical import canonical_json_file_bytes, loads_json_strict
from agent_commons.core.ids import is_typed_id
from agent_commons.core.schema_registry import SchemaRegistry
from agent_commons.errors import ConfigurationError, ValidationError

STATE_OWNER_SCHEMA = "agent_commons.state_owner.v1"
STATE_OWNER_FILENAME = "workspace-owner.json"
_STATE_DIAGNOSTIC_ENTRY_LIMIT = 20
_LEGACY_STATE_ENTRY_NAMES = frozenset(
    {
        STATE_OWNER_FILENAME,
        "canonical-write.lock",
        "claims",
        "idempotency",
        "idempotency-abandonments",
        "idempotency-v2",
        "index.sqlite3",
        "runtime",
        "sessions",
    }
)


def _bounded_state_material(root: Path) -> tuple[list[str], bool]:
    names: list[str] = []
    truncated = False
    for item in root.iterdir():
        if item.name == "workspaces":
            continue
        if len(names) == _STATE_DIAGNOSTIC_ENTRY_LIMIT:
            truncated = True
            break
        names.append(item.name)
    return sorted(names), truncated


@lru_cache(maxsize=1)
def _packaged_schemas() -> SchemaRegistry:
    """Load immutable packaged schemas lazily for legacy ownership proof."""

    return SchemaRegistry()


def _configuration_error(
    message: str,
    *,
    code: str,
    details: dict[str, Any],
    safe_next_actions: tuple[str, ...],
) -> ConfigurationError:
    error = ConfigurationError(message)
    error.code = code  # type: ignore[attr-defined]
    error.details = details  # type: ignore[attr-defined]
    error.safe_next_actions = safe_next_actions  # type: ignore[attr-defined]
    return error


def _canonical_bytes(value: dict[str, str]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _publish_owner_marker(path: Path, value: dict[str, str]) -> None:
    data = _canonical_bytes(value)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        descriptor = -1
        published = False
        try:
            os.link(temporary, path)
            published = True
        except FileExistsError:
            descriptor = -1
            try:
                descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise OSError("owner marker is not a regular file")
                with os.fdopen(descriptor, "rb", closefd=True) as handle:
                    existing = handle.read()
                descriptor = -1
            except OSError:
                existing = None
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
            if existing != data:
                raise _configuration_error(
                    "operational state ownership changed while it was being established",
                    code="state_owner_race",
                    details={"expected_workspace_id": value["workspace_id"]},
                    safe_next_actions=(
                        "Stop concurrent Agent Commons processes and inspect support --show-paths.",
                    ),
                ) from None
        if published:
            directory_descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _git_common_directory(repo_root: Path) -> Path | None:
    try:
        raw = subprocess.check_output(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=repo_root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    path = Path(raw)
    return path.resolve() if path.is_absolute() else (repo_root / path).resolve()


def _git_value(repo_root: Path, *arguments: str) -> str | None:
    try:
        value = subprocess.check_output(
            ["git", *arguments],
            cwd=repo_root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return value or None


def receipt_scope_descriptor(repo_root: str | Path, workspace_id: str) -> dict[str, str]:
    """Return the stable worktree/ref identity used by receipt recovery."""

    repo = Path(repo_root).expanduser().resolve()
    raw_git_dir = _git_value(repo, "rev-parse", "--git-dir")
    if raw_git_dir is None:
        checkout_id = str(repo)
        ref_kind = "non-git"
        ref_value = "non-git"
    else:
        git_dir = Path(raw_git_dir)
        checkout_id = str(
            git_dir.resolve() if git_dir.is_absolute() else (repo / git_dir).resolve()
        )
        symbolic_ref = _git_value(repo, "symbolic-ref", "--quiet", "HEAD")
        if symbolic_ref is not None:
            ref_kind = "symbolic"
            ref_value = symbolic_ref
        else:
            ref_kind = "detached"
            ref_value = _git_value(repo, "rev-parse", "HEAD") or "unborn"
    identity = {
        "workspace_id": workspace_id,
        "checkout_id": checkout_id,
        "ref_kind": ref_kind,
        "ref_value": ref_value,
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {**identity, "scope_id": hashlib.sha256(encoded).hexdigest()}


def _ensure_real_directory(path: Path, *, label: str) -> None:
    if path.is_symlink():
        raise ConfigurationError(f"refusing to use symlinked {label}: {path}")
    if path.exists() and not path.is_dir():
        raise ConfigurationError(f"expected a directory for {label}: {path}")
    path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():  # defensive race check
        raise ConfigurationError(f"unsafe directory for {label}: {path}")


def _resolve_override(value: str | Path, repo_root: Path) -> Path:
    path = Path(value).expanduser()
    # Keep the final path component unresolved so ``ensure_layout`` can detect
    # and reject an operator-supplied symlink instead of silently following it.
    return path.absolute() if path.is_absolute() else (repo_root / path).absolute()


@dataclass(frozen=True)
class CommonsPaths:
    repo_root: Path
    commons_root: Path
    state_root: Path
    state_base: Path | None = None
    state_mode: str = "exact"
    state_source: str = "default"
    workspace_id: str | None = None

    @classmethod
    def for_workspace(
        cls,
        repo_root: str | Path,
        *,
        commons_root: str | Path | None = None,
        state_root: str | Path | None = None,
        state_base: str | Path | None = None,
        state_source: str | None = None,
        workspace_id: str | None = None,
    ) -> CommonsPaths:
        repo = Path(repo_root).expanduser().resolve()
        canonical = (
            _resolve_override(commons_root, repo) if commons_root else repo / ".agent-commons"
        )
        if state_root is not None and state_base is not None:
            raise ConfigurationError("state_root and state_base are mutually exclusive")
        environment_root = os.environ.get("AGENT_COMMONS_STATE_ROOT")
        environment_base = os.environ.get("AGENT_COMMONS_STATE_BASE")
        if state_root is not None:
            state = _resolve_override(state_root, repo)
            base = None
            mode = "exact"
            source = state_source or "argument:state-root"
        elif state_base is not None:
            base = _resolve_override(state_base, repo)
            state = base
            mode = "base"
            source = state_source or "argument:state-base"
        elif environment_root:
            state = _resolve_override(environment_root, repo)
            base = None
            mode = "exact"
            source = state_source or "env:AGENT_COMMONS_STATE_ROOT"
        elif environment_base:
            base = _resolve_override(environment_base, repo)
            state = base
            mode = "base"
            source = state_source or "env:AGENT_COMMONS_STATE_BASE"
        else:
            git_common = _git_common_directory(repo)
            base = (
                git_common / "agent-commons-state"
                if git_common is not None
                else canonical / ".state"
            )
            state = base
            mode = "base"
            source = state_source or "default"
        paths = cls(repo, canonical, state, base, mode, source, None)
        return paths.for_workspace_id(workspace_id) if workspace_id is not None else paths

    def for_workspace_id(self, workspace_id: str) -> CommonsPaths:
        if self.workspace_id is not None and self.workspace_id != workspace_id:
            raise ConfigurationError("operational paths are already bound to another workspace")
        if self.state_mode == "exact":
            return CommonsPaths(
                self.repo_root,
                self.commons_root,
                self.state_root,
                None,
                "exact",
                self.state_source,
                workspace_id,
            )
        assert self.state_base is not None
        namespaced = self.state_base / "workspaces" / workspace_id
        legacy_owner = self._legacy_owner_id(self.state_base)
        base_is_unsafe = self.state_base.is_symlink() or (
            self.state_base.exists() and not self.state_base.is_dir()
        )
        base_has_legacy_material = False
        if self.state_base.is_dir() and not self.state_base.is_symlink():
            material, _truncated = _bounded_state_material(self.state_base)
            base_has_legacy_material = bool(material)
        if base_is_unsafe or legacy_owner is not None or base_has_legacy_material:
            effective = self.state_base
            mode = "legacy-exact"
        else:
            effective = namespaced
            mode = "base"
        return CommonsPaths(
            self.repo_root,
            self.commons_root,
            effective,
            self.state_base,
            mode,
            self.state_source,
            workspace_id,
        )

    @classmethod
    def discover(cls, start: str | Path | None = None) -> CommonsPaths:
        current = Path(start or Path.cwd()).expanduser().resolve()
        for candidate in (current, *current.parents):
            if (candidate / ".agent-commons").is_dir() or (candidate / ".git").exists():
                return cls.for_workspace(candidate)
        raise ConfigurationError(f"could not discover a workspace from {current}")

    @property
    def events(self) -> Path:
        return self.commons_root / "events"

    @property
    def manifests(self) -> Path:
        return self.commons_root / "manifests"

    @property
    def blobs(self) -> Path:
        return self.commons_root / "blobs" / "sha256"

    @property
    def cache(self) -> Path:
        return self.commons_root / "cache"

    @property
    def idempotency(self) -> Path:
        """Legacy v1 receipt root retained for migration and rollback."""

        return self.state_root / "idempotency"

    @property
    def legacy_abandonments(self) -> Path:
        return self.state_root / "idempotency-abandonments"

    @property
    def idempotency_v2(self) -> Path:
        return self.state_root / "idempotency-v2"

    @property
    def index_db(self) -> Path:
        return self.state_root / "index.sqlite3"

    @property
    def owner_marker(self) -> Path:
        return self.state_root / STATE_OWNER_FILENAME

    @staticmethod
    def _legacy_owner_id(root: Path) -> str | None:
        directory = root / "idempotency-v2"
        migration = directory / "migration.json"
        if directory.is_symlink() or not directory.is_dir() or migration.is_symlink():
            return None
        descriptor = -1
        try:
            descriptor = os.open(migration, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                return None
            with os.fdopen(descriptor, "rb", closefd=True) as handle:
                raw = handle.read()
            descriptor = -1
            value = loads_json_strict(raw)
            if not isinstance(value, dict) or raw != canonical_json_file_bytes(value):
                return None
            _packaged_schemas().validate("commons.idempotency_migration.v2", value)
        except (OSError, ValidationError):
            return None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        workspace_id = value.get("workspace_id")
        return workspace_id if is_typed_id(workspace_id, "workspace") else None

    def ownership_report(self, workspace_id: str | None = None) -> dict[str, Any]:
        expected = workspace_id or self.workspace_id
        report: dict[str, Any] = {
            "mode": self.state_mode,
            "source": self.state_source,
            "workspace_id": expected,
            "state_exists": self.state_root.is_dir(),
            "match": None,
            "status": "absent",
        }
        if not self.state_root.exists():
            return report
        if self.state_root.is_symlink() or not self.state_root.is_dir():
            report.update(status="unsafe", match=False)
            return report
        marker = self.owner_marker
        if marker.is_file() and not marker.is_symlink():
            try:
                raw = marker.read_bytes()
                value = json.loads(raw)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                report.update(status="invalid-marker", match=False)
                return report
            canonical = isinstance(value, dict) and raw == _canonical_bytes(value)
            valid = (
                canonical
                and set(value) == {"schema", "workspace_id"}
                and value.get("schema") == STATE_OWNER_SCHEMA
                and isinstance(value.get("workspace_id"), str)
                and bool(value["workspace_id"])
            )
            owner = value.get("workspace_id") if valid else None
            report.update(
                owner_workspace_id=owner,
                status="owned" if valid else "invalid-marker",
                match=bool(valid and expected is not None and owner == expected),
            )
            return report
        legacy_owner = self._legacy_owner_id(self.state_root)
        if legacy_owner is not None:
            report.update(
                owner_workspace_id=legacy_owner,
                status="legacy-owned",
                match=bool(expected is not None and legacy_owner == expected),
            )
            return report
        material, material_truncated = _bounded_state_material(self.state_root)
        report.update(status="empty" if not material else "ambiguous-legacy")
        report["match"] = True if not material and expected is not None else None
        if material:
            report["ambiguous_entries"] = material
            report["ambiguous_entries_truncated"] = material_truncated
        return report

    def validate_state_ownership(self, *, read_only: bool = False) -> dict[str, Any]:
        if self.workspace_id is None:
            raise ConfigurationError("workspace_id is required before opening operational state")
        report = self.ownership_report()
        if report["status"] in {"absent", "empty"}:
            if read_only:
                return report
            _ensure_real_directory(self.state_root, label="operational state")
            _publish_owner_marker(
                self.owner_marker,
                {"schema": STATE_OWNER_SCHEMA, "workspace_id": self.workspace_id},
            )
            return self.ownership_report()
        if report["status"] == "legacy-owned" and report["match"] is True:
            if not read_only:
                _publish_owner_marker(
                    self.owner_marker,
                    {"schema": STATE_OWNER_SCHEMA, "workspace_id": self.workspace_id},
                )
            return report
        if report["status"] == "owned" and report["match"] is True:
            return report
        if report["status"] in {"owned", "legacy-owned"}:
            raise _configuration_error(
                "operational state belongs to a different Agent Commons workspace",
                code="state_owner_mismatch",
                details={
                    "expected_workspace_id": self.workspace_id,
                    "owner_workspace_id": report.get("owner_workspace_id"),
                    "mode": self.state_mode,
                    "source": self.state_source,
                    "resolved_repo_root": str(self.repo_root),
                    "resolved_state_root": str(self.state_root),
                },
                safe_next_actions=(
                    "Use AGENT_COMMONS_STATE_BASE for automatic per-workspace isolation.",
                    "Choose an empty exact AGENT_COMMONS_STATE_ROOT; do not move or delete "
                    "state automatically.",
                ),
            )
        details = {
            "expected_workspace_id": self.workspace_id,
            "status": report["status"],
            "mode": self.state_mode,
            "source": self.state_source,
            "resolved_state_root": str(self.state_root),
        }
        message = "operational state ownership is missing or invalid"
        safe_next_actions = (
            "Inspect support --show-paths and choose an empty exact state root.",
            "Do not move, delete, or adopt legacy operational state without operator review.",
        )
        if (
            report["status"] == "ambiguous-legacy"
            and self.state_mode == "legacy-exact"
            and self.state_base == self.state_root
        ):
            entries = list(report.get("ambiguous_entries") or ())
            truncated = bool(report.get("ambiguous_entries_truncated"))
            details["legacy_mode_trigger_entries"] = entries
            details["legacy_mode_trigger_entries_truncated"] = truncated
            rendered = ", ".join(entries)
            message = (
                "AGENT_COMMONS_STATE_BASE contains material that switched it to legacy-exact "
                f"mode: {rendered}"
            )
            non_state_entries = [name for name in entries if name not in _LEGACY_STATE_ENTRY_NAMES]
            if non_state_entries:
                details["non_state_entries"] = non_state_entries
            if non_state_entries == entries and not truncated:
                safe_next_actions = (
                    "Move the listed non-state files out of AGENT_COMMONS_STATE_BASE, then rerun "
                    "doctor or broker preflight.",
                    "Keep runtime configuration in a separate operator-owned directory outside "
                    "the state base and delegated workspace.",
                )
        raise _configuration_error(
            message,
            code="state_owner_unproven",
            details=details,
            safe_next_actions=safe_next_actions,
        )

    def ensure_layout(self, *, read_only: bool = False) -> None:
        if read_only:
            for label, path in (
                ("repository root", self.repo_root),
                ("canonical workspace", self.commons_root),
                ("event directory", self.events),
                ("manifest directory", self.manifests),
            ):
                if path.is_symlink() or not path.is_dir():
                    raise ConfigurationError(f"read-only {label} is unavailable: {path}")
            if self.workspace_id is not None:
                self.validate_state_ownership(read_only=True)
            return
        _ensure_real_directory(self.repo_root, label="repository root")
        _ensure_real_directory(self.commons_root, label="canonical workspace")
        for label, path in (
            ("event directory", self.events),
            ("manifest directory", self.manifests),
            ("blob root directory", self.commons_root / "blobs"),
            ("blob directory", self.blobs),
            ("cache directory", self.cache),
        ):
            _ensure_real_directory(path, label=label)
        if self.workspace_id is None:
            _ensure_real_directory(self.state_root, label="operational state")
        else:
            self.validate_state_ownership()
        _ensure_real_directory(self.idempotency_v2, label="idempotency v2 directory")

    def canonical_relative(self, path: str | Path) -> str:
        try:
            return Path(path).resolve().relative_to(self.commons_root.resolve()).as_posix()
        except ValueError as exc:
            raise ConfigurationError(f"path is outside the canonical workspace: {path}") from exc
