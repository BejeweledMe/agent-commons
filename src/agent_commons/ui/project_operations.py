"""Inspectable, confirmed project creation and attachment operations."""

from __future__ import annotations

import os
import stat
import subprocess
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_commons.errors import ConfigurationError, IntegrityError
from agent_commons.services.manager import CommonsManager
from agent_commons.ui.project_registry import (
    ProjectRegistry,
    ProjectRegistryRefusal,
    _git_environment,
    _safe_text,
    _valid_key,
    open_project_manager,
)

_INSPECTION_TTL_SECONDS = 120
_GIT = "/usr/bin/git"


@dataclass(frozen=True, slots=True)
class _Inspection:
    id: str
    mode: str
    checkout: Path
    name: str
    initialization_required: bool
    expires_at: float
    anchor_identity: str
    git_identity: str | None


class ProjectOperations:
    """Confirm a short-lived inspected intent before any checkout is changed."""

    def __init__(
        self,
        registry: ProjectRegistry,
        *,
        state_binding: str | Path | None = None,
        forbidden_roots: Iterable[str | Path] = (),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.registry = registry
        self._state_binding = Path(state_binding).expanduser().resolve() if state_binding else None
        self._forbidden = tuple(
            Path(item).expanduser().resolve() for item in (registry.root, *forbidden_roots)
        )
        self._clock = clock
        self._inspections: dict[str, _Inspection] = {}

    def list_projects(self) -> dict[str, object]:
        return self.registry.list_projects()

    def inspect(self, mode: object, path: object, name: object) -> dict[str, object]:
        label = _safe_text(name)
        if mode not in ("new", "existing"):
            raise ProjectRegistryRefusal("project_invalid", "Project mode is invalid.")
        if type(path) is not str or not Path(path).is_absolute():
            raise ProjectRegistryRefusal("project_invalid", "Project path is invalid.")
        target = Path(path)
        self._refuse_overlap(target)
        if target.is_symlink():
            raise ProjectRegistryRefusal("project_path_unsafe", "Project path is unavailable.")
        if mode == "new":
            self._validate_new_target(target)
            required = True
            anchor = target.parent
            git_identity = None
        else:
            target = self._validate_existing_target(target)
            workspace = target / ".agent-commons" / "workspace.yaml"
            required = not workspace.is_file() or workspace.is_symlink()
            anchor = target
            git_identity = self._git_identity(target)
        anchor_identity = self._directory_identity(anchor)
        inspection_id = "inspection." + os.urandom(16).hex()
        expiry = self._clock() + _INSPECTION_TTL_SECONDS
        self._inspections[inspection_id] = _Inspection(
            inspection_id,
            mode,
            target,
            label,
            required,
            expiry,
            anchor_identity,
            git_identity,
        )
        self._prune()
        return {
            "inspection_id": inspection_id,
            "name": label,
            "mode": mode,
            "initialization_required": required,
            "expires_at": (datetime.now(tz=UTC) + timedelta(seconds=_INSPECTION_TTL_SECONDS))
            .isoformat()
            .replace("+00:00", "Z"),
        }

    def create(self, inspection_id: object, idempotency_key: object) -> dict[str, object]:
        if type(inspection_id) is not str or not _valid_key(idempotency_key):
            raise ProjectRegistryRefusal("project_invalid", "Project request is invalid.")
        inspection = self._inspections.get(inspection_id)
        recovered = False
        if inspection is None:
            durable = self.registry.recover_create(inspection_id, idempotency_key)
            if isinstance(durable, dict):
                if "project" in durable:
                    return durable
                inspection = self._inspection_from_durable(inspection_id, durable)
                recovered = True
            elif durable is not None:
                return durable
        if inspection is None or (not recovered and inspection.expires_at < self._clock()):
            self._inspections.pop(inspection_id, None)
            raise ProjectRegistryRefusal(
                "project_inspection_expired", "Project inspection expired; inspect it again.", 409
            )
        create_intent = {
            "inspection_id": inspection.id,
            "mode": inspection.mode,
            "checkout": str(inspection.checkout),
            "name": inspection.name,
            "state_binding": str(self._state_binding) if self._state_binding else None,
            "initialization_required": inspection.initialization_required,
            "anchor_identity": inspection.anchor_identity,
            "git_identity": inspection.git_identity,
        }
        previous = self.registry.reserve_create(idempotency_key, create_intent)
        if previous is not None:
            return previous
        progress = self.registry.create_progress(idempotency_key, create_intent)
        self._refuse_overlap(inspection.checkout)
        if inspection.mode == "new":
            self._require_anchor(inspection.checkout.parent, inspection.anchor_identity)
            self._initialize_new(
                inspection.checkout, inspection.name, idempotency_key, create_intent, progress
            )
        else:
            self._validate_existing_target(inspection.checkout)
            self._require_anchor(inspection.checkout, inspection.anchor_identity)
            if inspection.git_identity != self._git_identity(inspection.checkout):
                raise ProjectRegistryRefusal(
                    "project_path_changed", "Project path changed after inspection.", 409
                )
            if inspection.initialization_required and self._needs_initialization(
                inspection.checkout
            ):
                CommonsManager.initialize(
                    inspection.checkout, integrations=(), workspace_name=inspection.name
                )
        binding = self._state_binding if inspection.mode == "existing" else None
        try:
            manager = open_project_manager(inspection.checkout, binding)
        except (ConfigurationError, IntegrityError) as exc:
            raise ProjectRegistryRefusal(
                "project_unavailable", "Project is unavailable.", 409
            ) from exc
        result = self.registry.register(
            checkout=inspection.checkout,
            workspace_id=manager.workspace_id,
            state_binding=manager.paths.state_root,
            name=inspection.name,
            idempotency_key=idempotency_key,
            create_intent=create_intent,
        )
        return result

    def update(
        self,
        project_id: object,
        expected_revision: object,
        name: object = None,
        archived: object = None,
        idempotency_key: object = None,
    ) -> dict[str, object]:
        if (
            type(project_id) is not str
            or type(expected_revision) is not str
            or type(idempotency_key) is not str
        ):
            raise ProjectRegistryRefusal("project_invalid", "Project request is invalid.")
        return self.registry.update(
            project_id,
            expected_revision,
            name=name,
            archived=archived,
            idempotency_key=idempotency_key,
        )

    def _refuse_overlap(self, target: Path) -> None:
        try:
            resolved = target.resolve()
        except OSError as exc:
            raise ProjectRegistryRefusal(
                "project_path_unsafe", "Project path is unavailable."
            ) from exc
        for forbidden in self._forbidden:
            if (
                resolved == forbidden
                or forbidden in resolved.parents
                or resolved in forbidden.parents
            ):
                raise ProjectRegistryRefusal(
                    "project_path_refused", "Project path overlaps protected application storage."
                )
        if self.registry.overlaps_registered_checkout(resolved):
            raise ProjectRegistryRefusal(
                "project_path_refused", "Project path overlaps a registered project."
            )

    def _validate_new_target(self, target: Path) -> None:
        if target.exists() or target.is_symlink() or target.name in ("", ".", ".."):
            raise ProjectRegistryRefusal("project_path_exists", "New project path must not exist.")
        self._safe_owned_directory(target.parent)

    def _revalidate_new(self, target: Path, *, allow_initialized: bool = False) -> None:
        if target.is_symlink():
            raise ProjectRegistryRefusal(
                "project_path_changed", "New project path changed after inspection.", 409
            )
        if not target.exists():
            self._safe_owned_directory(target.parent)
            return
        # A request that lost its response after `git init` may retry.  Never
        # turn an arbitrary populated directory into a project on that retry.
        workspace = target / ".agent-commons" / "workspace.yaml"
        completed = workspace.is_file() and not workspace.is_symlink()
        if not target.is_dir() or (not self._is_empty_git_shell(target) and not completed):
            raise ProjectRegistryRefusal(
                "project_path_changed", "New project path changed after inspection.", 409
            )
        if completed and not allow_initialized and not self.registry.is_registered_checkout(target):
            raise ProjectRegistryRefusal(
                "project_path_changed", "New project path changed after inspection.", 409
            )
        self._safe_owned_directory(target)

    def _validate_existing_target(self, target: Path) -> Path:
        try:
            resolved = target.resolve(strict=True)
        except OSError as exc:
            raise ProjectRegistryRefusal(
                "project_not_repository", "Project repository is unavailable."
            ) from exc
        self._safe_owned_directory(resolved)
        if target.is_symlink() or not self._is_git_repository(resolved):
            raise ProjectRegistryRefusal(
                "project_not_repository", "Project repository is unavailable."
            )
        return resolved

    @staticmethod
    def _needs_initialization(target: Path) -> bool:
        workspace = target / ".agent-commons" / "workspace.yaml"
        return not workspace.is_file() or workspace.is_symlink()

    @staticmethod
    def _directory_identity(directory: Path) -> str:
        try:
            metadata = directory.lstat()
        except OSError as exc:
            raise ProjectRegistryRefusal(
                "project_path_unsafe", "Project path is unavailable."
            ) from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise ProjectRegistryRefusal("project_path_unsafe", "Project path is unavailable.")
        return f"{metadata.st_dev}:{metadata.st_ino}"

    def _require_anchor(self, directory: Path, expected: str) -> None:
        if self._directory_identity(directory) != expected:
            raise ProjectRegistryRefusal(
                "project_path_changed", "Project path changed after inspection.", 409
            )

    def _safe_owned_directory(self, directory: Path) -> None:
        try:
            metadata = directory.lstat()
        except OSError as exc:
            raise ProjectRegistryRefusal(
                "project_path_unsafe", "Project path is unavailable."
            ) from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise ProjectRegistryRefusal("project_path_unsafe", "Project path is unavailable.")
        if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
            raise ProjectRegistryRefusal("project_path_unsafe", "Project path is unavailable.")

    @staticmethod
    def _is_git_repository(path: Path) -> bool:
        try:
            result = subprocess.run(
                [_GIT, "rev-parse", "--show-toplevel"],
                cwd=path,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                check=True,
                timeout=5,
                env=_git_environment(),
            )
        except (OSError, subprocess.SubprocessError):
            return False
        try:
            return Path(result.stdout.strip()).resolve(strict=True) == path.resolve(strict=True)
        except OSError:
            return False

    @staticmethod
    def _git_identity(path: Path) -> str | None:
        try:
            result = subprocess.run(
                [_GIT, "rev-parse", "--git-common-dir"],
                cwd=path,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                check=True,
                timeout=5,
                env=_git_environment(),
            )
        except (OSError, subprocess.SubprocessError):
            return None
        candidate = Path(result.stdout.strip())
        if not result.stdout.strip():
            return None
        return str((candidate if candidate.is_absolute() else path / candidate).resolve())

    @staticmethod
    def _is_empty_git_shell(path: Path) -> bool:
        try:
            return (
                {item.name for item in path.iterdir()} == {".git"}
                and (path / ".git").is_dir()
                and not (path / ".git").is_symlink()
            )
        except OSError:
            return False

    def _initialize_new(
        self,
        checkout: Path,
        name: str,
        idempotency_key: str,
        intent: dict[str, object],
        progress: dict[str, object],
    ) -> None:
        milestone = progress.get("milestone")
        target_identity = progress.get("target_identity")
        if milestone == "reserved":
            if checkout.exists() or checkout.is_symlink():
                raise ProjectRegistryRefusal(
                    "project_path_changed", "New project path changed after inspection.", 409
                )
            self._safe_owned_directory(checkout.parent)
            try:
                checkout.mkdir(mode=0o700)
            except OSError as exc:
                raise ProjectRegistryRefusal(
                    "project_path_changed", "New project path changed after inspection.", 409
                ) from exc
            self.registry.advance_create(idempotency_key, intent, "directory_created", checkout)
            progress = self.registry.create_progress(idempotency_key, intent)
            milestone = progress.get("milestone")
            target_identity = progress.get("target_identity")
        if (
            not isinstance(target_identity, str)
            or self._directory_identity(checkout) != target_identity
        ):
            raise ProjectRegistryRefusal(
                "project_path_changed", "New project path changed after inspection.", 409
            )
        if milestone == "directory_created":
            try:
                subprocess.run(
                    [_GIT, "init", "--quiet", "."],
                    cwd=checkout,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=True,
                    timeout=20,
                    env=_git_environment(),
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise ProjectRegistryRefusal(
                    "project_git_init_failed", "Project repository could not be initialized.", 409
                ) from exc
            self.registry.advance_create(idempotency_key, intent, "git_initialized", checkout)
            progress = self.registry.create_progress(idempotency_key, intent)
            milestone = progress.get("milestone")
        if milestone == "git_initialized":
            if not self._is_empty_git_shell(checkout):
                # A crash can leave either our own or an unrelated initialized
                # workspace on this inode.  The bounded receipt cannot prove
                # which one it is, so require a new explicit existing-project
                # inspection instead of adopting either.
                raise ProjectRegistryRefusal(
                    "project_initialization_ambiguous",
                    "Project initialization was interrupted; inspect and connect the repository.",
                    409,
                )
            CommonsManager.initialize(checkout, integrations=(), workspace_name=name)
            self.registry.advance_create(idempotency_key, intent, "commons_initialized", checkout)
            progress = self.registry.create_progress(idempotency_key, intent)
            milestone = progress.get("milestone")
        if (
            milestone != "commons_initialized"
            or not (checkout / ".agent-commons" / "workspace.yaml").is_file()
        ):
            raise ProjectRegistryRefusal(
                "project_path_changed", "New project path changed after inspection.", 409
            )

    def _prune(self) -> None:
        now = self._clock()
        for key, value in tuple(self._inspections.items()):
            if value.expires_at < now:
                self._inspections.pop(key, None)

    @staticmethod
    def _inspection_from_durable(inspection_id: str, intent: dict[str, object]) -> _Inspection:
        mode = intent.get("mode")
        checkout = intent.get("checkout")
        name = intent.get("name")
        if mode not in ("new", "existing") or type(checkout) is not str or type(name) is not str:
            raise ProjectRegistryRefusal(
                "project_registry_unavailable", "Project registry is unavailable.", 409
            )
        required = intent.get("initialization_required")
        anchor = intent.get("anchor_identity")
        git_identity = intent.get("git_identity")
        if (
            type(required) is not bool
            or type(anchor) is not str
            or (git_identity is not None and type(git_identity) is not str)
        ):
            raise ProjectRegistryRefusal(
                "project_registry_unavailable", "Project registry is unavailable.", 409
            )
        return _Inspection(
            inspection_id, mode, Path(checkout), name, required, float("inf"), anchor, git_identity
        )
