"""Filesystem layout for one project-local Agent Commons workspace."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from agent_commons.errors import ConfigurationError


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

    @classmethod
    def for_workspace(
        cls,
        repo_root: str | Path,
        *,
        commons_root: str | Path | None = None,
        state_root: str | Path | None = None,
    ) -> CommonsPaths:
        repo = Path(repo_root).expanduser().resolve()
        canonical = (
            _resolve_override(commons_root, repo) if commons_root else repo / ".agent-commons"
        )
        if state_root is not None:
            state = _resolve_override(state_root, repo)
        else:
            git_common = _git_common_directory(repo)
            state = (
                git_common / "agent-commons-state"
                if git_common is not None
                else canonical / ".state"
            )
        return cls(repo, canonical, state)

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
        _ensure_real_directory(self.state_root, label="operational state")
        _ensure_real_directory(self.idempotency_v2, label="idempotency v2 directory")

    def canonical_relative(self, path: str | Path) -> str:
        try:
            return Path(path).resolve().relative_to(self.commons_root.resolve()).as_posix()
        except ValueError as exc:
            raise ConfigurationError(f"path is outside the canonical workspace: {path}") from exc
