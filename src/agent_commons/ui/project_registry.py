"""Private, bounded registry for the projects served by one local host.

The registry is deliberately independent of a project's ledger.  It stores the
operator's choice of checkout and state binding, but publishes only the small
project DTO used by the panel.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_commons.core.canonical import loads_json_strict
from agent_commons.errors import ConfigurationError, IntegrityError, ValidationError
from agent_commons.storage.atomic import atomic_write_replace
from agent_commons.storage.opstate import OperationalStoragePolicy, exclusive_lock

PROJECT_LIST_SCHEMA = "agent_commons.project_list.v1"
PROJECT_REGISTRY_SCHEMA = "agent_commons.project_registry.v1"
MAX_PROJECTS = 64
MAX_REGISTRY_BYTES = 262_144
_PROJECT_ID = re.compile(r"project\.[0-9a-f]{32}\Z")
_INSPECTION_ID = re.compile(r"inspection\.[0-9a-f]{32}\Z")
_IDENTITY = re.compile(r"[0-9]+:[0-9]+\Z")
_GIT = "/usr/bin/git"
_REGISTRY_POLICY = OperationalStoragePolicy(
    "project registry", True, "project-registry", "resolved", True, True
)


class ProjectRegistryRefusal(ValidationError):
    """A safe, typed refusal for the local project API."""

    def __init__(self, code: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def default_project_registry_root() -> Path:
    configured = os.environ.get("XDG_CONFIG_HOME")
    base = (
        Path(configured)
        if configured and Path(configured).is_absolute()
        else Path.home() / ".config"
    )
    return base / "agent-commons" / "projects"


def _canonical(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


def _revision(value: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _safe_text(value: object, *, limit: int = 160) -> str:
    if type(value) is not str or not value.strip() or len(value) > limit:
        raise ProjectRegistryRefusal("project_invalid", "Project details are invalid.")
    if any(ord(character) < 32 for character in value):
        raise ProjectRegistryRefusal("project_invalid", "Project details are invalid.")
    return value.strip()


def _git_identity(checkout: Path) -> str | None:
    try:
        result = subprocess.run(
            [_GIT, "rev-parse", "--git-common-dir"],
            cwd=checkout,
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
            env=_git_environment(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    if not value:
        return None
    candidate = Path(value)
    return str((candidate if candidate.is_absolute() else checkout / candidate).resolve())


def _git_environment() -> dict[str, str]:
    """Do not let the panel process redirect Git's repository identity."""
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_") and key in {"HOME", "PATH", "LANG", "LC_ALL", "TMPDIR"}
    }


def _valid_key(value: object) -> bool:
    return type(value) is str and bool(value) and len(value) <= 160


def _normalized_absolute(value: object) -> bool:
    if not isinstance(value, str) or not Path(value).is_absolute():
        return False
    try:
        return str(Path(value).resolve()) == value
    except OSError:
        return False


def _create_intent(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict) or set(value) != {
        "inspection_id",
        "mode",
        "checkout",
        "name",
        "state_binding",
        "initialization_required",
        "anchor_identity",
        "git_identity",
    }:
        return None
    if (
        not isinstance(value["inspection_id"], str)
        or _INSPECTION_ID.fullmatch(value["inspection_id"]) is None
        or value["mode"] not in ("new", "existing")
        or not isinstance(value["checkout"], str)
        or not Path(value["checkout"]).is_absolute()
        or type(value["initialization_required"]) is not bool
        or not isinstance(value["anchor_identity"], str)
        or _IDENTITY.fullmatch(value["anchor_identity"]) is None
        or (
            value["state_binding"] is not None
            and (
                not isinstance(value["state_binding"], str)
                or not Path(value["state_binding"]).is_absolute()
            )
        )
        or (value["git_identity"] is not None and not isinstance(value["git_identity"], str))
    ):
        return None
    try:
        value = dict(value)
        value["name"] = _safe_text(value["name"])
    except ProjectRegistryRefusal:
        return None
    return value


def _normal_intent(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "checkout",
        "workspace_id",
        "state_binding",
        "name",
    }:
        return False
    if (
        not isinstance(value["checkout"], str)
        or not Path(value["checkout"]).is_absolute()
        or not isinstance(value["workspace_id"], str)
        or not value["workspace_id"].startswith("workspace.")
        or (
            value["state_binding"] is not None
            and (
                not isinstance(value["state_binding"], str)
                or not Path(value["state_binding"]).is_absolute()
            )
        )
    ):
        return False
    try:
        _safe_text(value["name"])
    except ProjectRegistryRefusal:
        return False
    return True


def _directory_identity(path: Path) -> str | None:
    try:
        metadata = path.lstat()
    except OSError:
        return None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        return None
    return f"{metadata.st_dev}:{metadata.st_ino}"


def _default_state_base(checkout: Path) -> Path:
    """Resolve the documented default without consulting inherited UI process env."""
    identity = _git_identity(checkout)
    if identity is None:
        raise ProjectRegistryRefusal("project_not_repository", "Project repository is unavailable.")
    return Path(identity) / "agent-commons-state"


def open_project_manager(
    checkout: Path, state_binding: str | Path | None, *, read_only: bool = False
):
    """Open a project's manager with an explicit binding or its isolated default."""
    from agent_commons.services.manager import CommonsManager

    if state_binding is not None:
        return CommonsManager(
            checkout,
            state_root=state_binding,
            state_source="project-registry",
            read_only=read_only,
        )
    return CommonsManager(
        checkout,
        state_base=_default_state_base(checkout),
        state_source="project-default",
        read_only=read_only,
    )


@dataclass(frozen=True, slots=True)
class RegisteredProject:
    id: str
    workspace_id: str
    name: str
    revision: str
    archived: bool
    checkout: Path
    git_identity: str
    state_binding: str | None
    state_identity: str | None

    def public(self) -> dict[str, Any]:
        state = "ready"
        available = self.checkout.is_dir() and not self.checkout.is_symlink()
        if not available:
            state = "missing"
        elif _git_identity(self.checkout) != self.git_identity:
            state = "identity_conflict"
            available = False
        elif (
            self.state_binding is not None
            and _directory_identity(Path(self.state_binding)) != self.state_identity
        ):
            state = "identity_conflict"
            available = False
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "name": self.name,
            "revision": self.revision,
            "archived": self.archived,
            "available": available,
            "state": state,
        }


class ProjectRegistry:
    """Own a private, CAS-protected registry outside every project workspace."""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.root = Path(root) if root is not None else default_project_registry_root()
        self._clock = clock

    @property
    def path(self) -> Path:
        return self.root / "registry.json"

    @property
    def lock_path(self) -> Path:
        return self.root / "registry.lock"

    def _ensure_root(self) -> None:
        # Create each missing directory ourselves so a newly introduced symlink
        # cannot be followed by mkdir/chmod.  The registry root is operator data.
        current = self.root
        missing: list[Path] = []
        while not current.exists():
            missing.append(current)
            current = current.parent
        if current.is_symlink() or not current.is_dir():
            raise ProjectRegistryRefusal(
                "project_registry_unavailable", "Project registry is unavailable.", 409
            )
        for directory in reversed(missing):
            directory.mkdir(mode=0o700)
            if directory.is_symlink() or not directory.is_dir():
                raise ProjectRegistryRefusal(
                    "project_registry_unavailable", "Project registry is unavailable.", 409
                )
        try:
            metadata = self.root.lstat()
            if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                raise OSError("unsafe root")
            if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
                raise OSError("foreign owner")
            os.chmod(self.root, 0o700, follow_symlinks=False)
        except OSError as exc:
            raise ProjectRegistryRefusal(
                "project_registry_unavailable", "Project registry is unavailable.", 409
            ) from exc

    def _empty(self) -> dict[str, Any]:
        body: dict[str, Any] = {"schema": PROJECT_REGISTRY_SCHEMA, "projects": [], "receipts": {}}
        body["revision"] = _revision(body)
        return body

    def _load(self) -> dict[str, Any]:
        self._ensure_root()
        if not self.path.exists():
            return self._empty()
        if self.path.is_symlink():
            raise ProjectRegistryRefusal(
                "project_registry_unavailable", "Project registry is unavailable.", 409
            )
        descriptor = -1
        try:
            before = self.path.lstat()
            descriptor = os.open(self.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or metadata.st_size > MAX_REGISTRY_BYTES
            ):
                raise OSError("unsafe registry")
            with os.fdopen(descriptor, "rb", closefd=True) as handle:
                raw = handle.read(MAX_REGISTRY_BYTES + 1)
            descriptor = -1
            after = self.path.lstat()
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino) or len(
                raw
            ) > MAX_REGISTRY_BYTES:
                raise OSError("registry changed while read")
            value = loads_json_strict(raw)
        except (OSError, ValidationError) as exc:
            raise ProjectRegistryRefusal(
                "project_registry_unavailable", "Project registry is unavailable.", 409
            ) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        self._validate(value)
        return value

    def _validate(self, value: object) -> None:
        if not isinstance(value, dict) or set(value) != {
            "schema",
            "revision",
            "projects",
            "receipts",
        }:
            raise ProjectRegistryRefusal(
                "project_registry_unavailable", "Project registry is unavailable.", 409
            )
        if (
            value["schema"] != PROJECT_REGISTRY_SCHEMA
            or not isinstance(value["projects"], list)
            or not isinstance(value["receipts"], dict)
        ):
            raise ProjectRegistryRefusal(
                "project_registry_unavailable", "Project registry is unavailable.", 409
            )
        if len(value["projects"]) > MAX_PROJECTS or len(value["receipts"]) > MAX_PROJECTS * 4:
            raise ProjectRegistryRefusal(
                "project_registry_unavailable", "Project registry is unavailable.", 409
            )
        expected = dict(value)
        revision = expected.pop("revision")
        if not isinstance(revision, str) or revision != _revision(expected):
            raise ProjectRegistryRefusal(
                "project_registry_unavailable", "Project registry is unavailable.", 409
            )
        ids: set[str] = set()
        workspace_ids: set[str] = set()
        for record in value["projects"]:
            self._record(record)
            if record["id"] in ids or record["workspace_id"] in workspace_ids:
                raise ProjectRegistryRefusal(
                    "project_registry_unavailable", "Project registry is unavailable.", 409
                )
            ids.add(record["id"])
            workspace_ids.add(record["workspace_id"])
        for key, receipt in value["receipts"].items():
            if not _valid_key(key) or not self._valid_receipt(receipt, ids):
                raise ProjectRegistryRefusal(
                    "project_registry_unavailable", "Project registry is unavailable.", 409
                )

    @staticmethod
    def _valid_receipt(value: object, project_ids: set[str]) -> bool:
        if not isinstance(value, dict):
            return False
        if set(value) == {"intent", "project_id"}:
            intent = value["intent"]
            update = (
                isinstance(intent, dict)
                and set(intent) == {"project_id", "expected_revision", "name", "archived"}
                and intent.get("project_id") in project_ids
                and isinstance(intent.get("expected_revision"), str)
                and (
                    intent.get("name") is None
                    or _normal_intent(
                        {
                            "checkout": "/",
                            "workspace_id": "workspace.x",
                            "state_binding": None,
                            "name": intent.get("name"),
                        }
                    )
                )
                and (intent.get("archived") is None or type(intent.get("archived")) is bool)
            )
            return (_normal_intent(intent) or update) and value["project_id"] in project_ids
        expected = {"create_intent", "project_id", "milestone", "target_identity"}
        if set(value) != expected or _create_intent(value["create_intent"]) is None:
            return False
        if value["project_id"] is not None and value["project_id"] not in project_ids:
            return False
        if value["milestone"] not in {
            "reserved",
            "directory_created",
            "git_initialized",
            "commons_initialized",
            "published",
        }:
            return False
        if value["milestone"] == "reserved":
            return value["target_identity"] is None and value["project_id"] is None
        if (
            not isinstance(value["target_identity"], str)
            or _IDENTITY.fullmatch(value["target_identity"]) is None
        ):
            return False
        return (value["milestone"] == "published") == (value["project_id"] is not None)

    def _record(self, value: object) -> RegisteredProject:
        if not isinstance(value, dict) or set(value) != {
            "id",
            "workspace_id",
            "name",
            "revision",
            "archived",
            "checkout",
            "git_identity",
            "state_binding",
            "state_identity",
        }:
            raise ProjectRegistryRefusal(
                "project_registry_unavailable", "Project registry is unavailable.", 409
            )
        if not isinstance(value["id"], str) or _PROJECT_ID.fullmatch(value["id"]) is None:
            raise ProjectRegistryRefusal(
                "project_registry_unavailable", "Project registry is unavailable.", 409
            )
        if not isinstance(value["workspace_id"], str) or not value["workspace_id"].startswith(
            "workspace."
        ):
            raise ProjectRegistryRefusal(
                "project_registry_unavailable", "Project registry is unavailable.", 409
            )
        if (
            not isinstance(value["name"], str)
            or not isinstance(value["revision"], str)
            or not isinstance(value["archived"], bool)
        ):
            raise ProjectRegistryRefusal(
                "project_registry_unavailable", "Project registry is unavailable.", 409
            )
        if (
            not isinstance(value["checkout"], str)
            or not Path(value["checkout"]).is_absolute()
            or not isinstance(value["git_identity"], str)
        ):
            raise ProjectRegistryRefusal(
                "project_registry_unavailable", "Project registry is unavailable.", 409
            )
        if value["state_binding"] is not None and (
            not isinstance(value["state_binding"], str)
            or not Path(value["state_binding"]).is_absolute()
        ):
            raise ProjectRegistryRefusal(
                "project_registry_unavailable", "Project registry is unavailable.", 409
            )
        if value["state_binding"] is None:
            if value["state_identity"] is not None:
                raise ProjectRegistryRefusal(
                    "project_registry_unavailable", "Project registry is unavailable.", 409
                )
        elif not isinstance(value["state_identity"], str):
            raise ProjectRegistryRefusal(
                "project_registry_unavailable", "Project registry is unavailable.", 409
            )
        body = dict(value)
        revision = body.pop("revision")
        if revision != _revision(body):
            raise ProjectRegistryRefusal(
                "project_registry_unavailable", "Project registry is unavailable.", 409
            )
        return RegisteredProject(
            value["id"],
            value["workspace_id"],
            value["name"],
            value["revision"],
            value["archived"],
            Path(value["checkout"]),
            value["git_identity"],
            value["state_binding"],
            value["state_identity"],
        )

    def _save(self, value: dict[str, Any]) -> None:
        body = dict(value)
        if (
            not isinstance(body.get("projects"), list)
            or not isinstance(body.get("receipts"), dict)
            or len(body["projects"]) > MAX_PROJECTS
            or len(body["receipts"]) > MAX_PROJECTS * 4
        ):
            raise ProjectRegistryRefusal(
                "project_registry_full", "Project registry has reached its storage limit.", 409
            )
        body.pop("revision", None)
        body["revision"] = _revision(body)
        if len(_canonical(body)) > MAX_REGISTRY_BYTES:
            raise ProjectRegistryRefusal(
                "project_registry_full", "Project registry has reached its storage limit.", 409
            )
        value.clear()
        value.update(body)
        atomic_write_replace(self.path, _canonical(value), mode=0o600)

    def _entries(self, document: Mapping[str, Any]) -> list[RegisteredProject]:
        return [self._record(item) for item in document["projects"]]

    def list_projects(self) -> dict[str, Any]:
        self._ensure_root()
        with exclusive_lock(self.lock_path, policy=_REGISTRY_POLICY):
            document = self._load()
            return {
                "schema": PROJECT_LIST_SCHEMA,
                "revision": document["revision"],
                "projects": [entry.public() for entry in self._entries(document)],
            }

    def project_binding(self, project_id: str) -> RegisteredProject:
        """Return the private binding for the trusted local host only."""
        return self.get(project_id)

    def is_registered_checkout(self, checkout: Path) -> bool:
        """Private idempotency seam; never expose checkout paths to clients."""
        self._ensure_root()
        with exclusive_lock(self.lock_path, policy=_REGISTRY_POLICY):
            return any(entry.checkout == checkout for entry in self._entries(self._load()))

    def overlaps_registered_checkout(self, path: Path) -> bool:
        """Private placement guard for another project checkout's entire tree."""
        self._ensure_root()
        with exclusive_lock(self.lock_path, policy=_REGISTRY_POLICY):
            return any(
                path == entry.checkout
                or entry.checkout in path.parents
                or path in entry.checkout.parents
                for entry in self._entries(self._load())
            )

    def get(self, project_id: str) -> RegisteredProject:
        if not isinstance(project_id, str) or _PROJECT_ID.fullmatch(project_id) is None:
            raise ProjectRegistryRefusal("project_unknown", "Project was not found.", 404)
        self._ensure_root()
        with exclusive_lock(self.lock_path, policy=_REGISTRY_POLICY):
            for entry in self._entries(self._load()):
                if entry.id == project_id:
                    return entry
        raise ProjectRegistryRefusal("project_unknown", "Project was not found.", 404)

    def register_existing(
        self, repo: str | Path, state_binding: str | Path | None = None, *, name: str | None = None
    ) -> dict[str, Any]:
        """Trusted startup attachment.  It never initializes a checkout."""
        checkout = Path(repo).expanduser().resolve(strict=True)
        if checkout.is_symlink() or not checkout.is_dir() or _git_identity(checkout) is None:
            raise ProjectRegistryRefusal(
                "project_not_repository", "Project repository is unavailable."
            )
        try:
            manager = open_project_manager(checkout, state_binding, read_only=True)
        except (ConfigurationError, IntegrityError) as exc:
            raise ProjectRegistryRefusal(
                "project_unavailable", "Project is unavailable.", 409
            ) from exc
        return self.register(
            checkout=checkout,
            workspace_id=manager.workspace_id,
            state_binding=manager.paths.state_root,
            name=name or checkout.name,
            idempotency_key="startup:" + str(checkout),
        )

    def register(
        self,
        *,
        checkout: Path,
        workspace_id: str,
        state_binding: str | Path | None,
        name: str,
        idempotency_key: str,
        create_intent: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        label = _safe_text(name)
        if not _valid_key(idempotency_key):
            raise ProjectRegistryRefusal("project_invalid", "Project request is invalid.")
        identity = _git_identity(checkout)
        if identity is None:
            raise ProjectRegistryRefusal(
                "project_not_repository", "Project repository is unavailable."
            )
        normalized = str(checkout.resolve())
        state = (
            str(Path(state_binding).expanduser().resolve()) if state_binding is not None else None
        )
        state_identity = _directory_identity(Path(state)) if state is not None else None
        if state is not None and state_identity is None:
            raise ProjectRegistryRefusal(
                "project_unavailable", "Project state is unavailable.", 409
            )
        intent = {
            "checkout": normalized,
            "workspace_id": workspace_id,
            "state_binding": state,
            "name": label,
        }
        self._ensure_root()
        with exclusive_lock(self.lock_path, policy=_REGISTRY_POLICY):
            document = self._load()
            receipt = document["receipts"].get(idempotency_key)
            if receipt is not None:
                reserved = (
                    create_intent is not None
                    and isinstance(receipt, dict)
                    and "create_intent" in receipt
                    and receipt.get("create_intent") == create_intent
                )
                ordinary = isinstance(receipt, dict) and receipt.get("intent") == intent
                if not reserved and not ordinary:
                    raise ProjectRegistryRefusal(
                        "project_idempotency_mismatch",
                        "Project request key was reused for different input.",
                        409,
                    )
                project_id = receipt.get("project_id")
                if isinstance(project_id, str):
                    for entry in self._entries(document):
                        if entry.id == project_id:
                            if (
                                entry.state_binding == state
                                and entry.state_identity != state_identity
                            ):
                                raise ProjectRegistryRefusal(
                                    "project_state_binding_conflict",
                                    "Project state binding changed.",
                                    409,
                                )
                            return {"project": entry.public(), "revision": document["revision"]}
                    raise ProjectRegistryRefusal(
                        "project_registry_unavailable", "Project registry is unavailable.", 409
                    )
            entries = self._entries(document)
            for entry in entries:
                if entry.workspace_id == workspace_id:
                    if (
                        entry.checkout == checkout
                        and entry.git_identity == identity
                        and entry.state_binding == state
                        and entry.state_identity == state_identity
                    ):
                        result = {"project": entry.public(), "revision": document["revision"]}
                        document["receipts"][idempotency_key] = self._receipt(
                            intent, entry.id, create_intent, _directory_identity(checkout)
                        )
                        self._save(document)
                        return result
                    code = (
                        "project_state_binding_conflict"
                        if entry.checkout == checkout and entry.git_identity == identity
                        else "project_workspace_registered"
                    )
                    raise ProjectRegistryRefusal(
                        code,
                        "This workspace is already registered with a different binding.",
                        409,
                    )
            if len(entries) >= MAX_PROJECTS:
                raise ProjectRegistryRefusal(
                    "project_limit_reached", "The project limit has been reached.", 409
                )
            body = {
                "id": "project." + os.urandom(16).hex(),
                "workspace_id": workspace_id,
                "name": label,
                "archived": False,
                "checkout": normalized,
                "git_identity": identity,
                "state_binding": state,
                "state_identity": state_identity,
            }
            body["revision"] = _revision(body)
            document["projects"].append(body)
            document["receipts"][idempotency_key] = self._receipt(
                intent, body["id"], create_intent, _directory_identity(checkout)
            )
            self._save(document)
            return {"project": self._record(body).public(), "revision": document["revision"]}

    @staticmethod
    def _receipt(
        intent: Mapping[str, Any],
        project_id: str | None,
        create_intent: Mapping[str, Any] | None,
        target_identity: str | None,
    ) -> dict[str, Any]:
        if create_intent is None:
            return {"intent": dict(intent), "project_id": project_id}
        assert project_id is not None and target_identity is not None
        return {
            "create_intent": dict(create_intent),
            "project_id": project_id,
            "milestone": "published",
            "target_identity": target_identity,
        }

    def reserve_create(
        self, idempotency_key: str, intent: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        """Durably bind a request key before checkout side effects begin."""
        if not _valid_key(idempotency_key):
            raise ProjectRegistryRefusal("project_invalid", "Project request is invalid.")
        self._ensure_root()
        frozen = _create_intent(intent)
        if frozen is None:
            raise ProjectRegistryRefusal("project_invalid", "Project request is invalid.")
        with exclusive_lock(self.lock_path, policy=_REGISTRY_POLICY):
            document = self._load()
            receipt = document["receipts"].get(idempotency_key)
            if receipt is not None:
                if not isinstance(receipt, dict) or receipt.get("create_intent") != frozen:
                    raise ProjectRegistryRefusal(
                        "project_idempotency_mismatch",
                        "Project request key was reused for different input.",
                        409,
                    )
                project_id = receipt.get("project_id")
                if isinstance(project_id, str):
                    for entry in self._entries(document):
                        if entry.id == project_id:
                            return {"project": entry.public(), "revision": document["revision"]}
                    raise ProjectRegistryRefusal(
                        "project_registry_unavailable", "Project registry is unavailable.", 409
                    )
                return None
            document["receipts"][idempotency_key] = {
                "create_intent": frozen,
                "project_id": None,
                "milestone": "reserved",
                "target_identity": None,
            }
            self._save(document)
            return None

    def create_progress(self, idempotency_key: str, intent: Mapping[str, Any]) -> dict[str, Any]:
        frozen = _create_intent(intent)
        if frozen is None:
            raise ProjectRegistryRefusal("project_invalid", "Project request is invalid.")
        self._ensure_root()
        with exclusive_lock(self.lock_path, policy=_REGISTRY_POLICY):
            receipt = self._load()["receipts"].get(idempotency_key)
            if not isinstance(receipt, dict) or receipt.get("create_intent") != frozen:
                raise ProjectRegistryRefusal(
                    "project_idempotency_mismatch",
                    "Project request key was reused for different input.",
                    409,
                )
            return dict(receipt)

    def advance_create(
        self, idempotency_key: str, intent: Mapping[str, Any], milestone: str, target: Path
    ) -> None:
        frozen = _create_intent(intent)
        identity = _directory_identity(target)
        if (
            frozen is None
            or identity is None
            or milestone not in {"directory_created", "git_initialized", "commons_initialized"}
        ):
            raise ProjectRegistryRefusal("project_invalid", "Project request is invalid.")
        self._ensure_root()
        with exclusive_lock(self.lock_path, policy=_REGISTRY_POLICY):
            document = self._load()
            receipt = document["receipts"].get(idempotency_key)
            if not isinstance(receipt, dict) or receipt.get("create_intent") != frozen:
                raise ProjectRegistryRefusal(
                    "project_idempotency_mismatch",
                    "Project request key was reused for different input.",
                    409,
                )
            if receipt.get("project_id") is not None:
                return
            receipt["milestone"] = milestone
            receipt["target_identity"] = identity
            self._save(document)

    def recover_create(self, inspection_id: str, idempotency_key: str) -> dict[str, Any] | None:
        """Recover a reserved create after a host restart lost inspection memory."""
        self._ensure_root()
        with exclusive_lock(self.lock_path, policy=_REGISTRY_POLICY):
            document = self._load()
            receipt = document["receipts"].get(idempotency_key)
            if not isinstance(receipt, dict):
                return None
            intent = receipt.get("create_intent")
            if not isinstance(intent, dict) or intent.get("inspection_id") != inspection_id:
                raise ProjectRegistryRefusal(
                    "project_idempotency_mismatch",
                    "Project request key was reused for different input.",
                    409,
                )
            project_id = receipt.get("project_id")
            if isinstance(project_id, str):
                for entry in self._entries(document):
                    if entry.id == project_id:
                        return {"project": entry.public(), "revision": document["revision"]}
                raise ProjectRegistryRefusal(
                    "project_registry_unavailable", "Project registry is unavailable.", 409
                )
            return dict(intent)

    def update(
        self,
        project_id: str,
        expected_revision: str,
        *,
        name: str | None = None,
        archived: bool | None = None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if not isinstance(expected_revision, str) or not _valid_key(idempotency_key):
            raise ProjectRegistryRefusal("project_invalid", "Project request is invalid.")
        if name is None and archived is None:
            raise ProjectRegistryRefusal("project_invalid", "Project update is empty.")
        if name is not None:
            name = _safe_text(name)
        if archived is not None and type(archived) is not bool:
            raise ProjectRegistryRefusal("project_invalid", "Project update is invalid.")
        intent = {
            "project_id": project_id,
            "expected_revision": expected_revision,
            "name": name,
            "archived": archived,
        }
        self._ensure_root()
        with exclusive_lock(self.lock_path, policy=_REGISTRY_POLICY):
            document = self._load()
            receipt = document["receipts"].get(idempotency_key)
            if receipt is not None:
                if (
                    not isinstance(receipt, dict)
                    or receipt.get("intent") != intent
                    or not isinstance(receipt.get("project_id"), str)
                ):
                    raise ProjectRegistryRefusal(
                        "project_idempotency_mismatch",
                        "Project request key was reused for different input.",
                        409,
                    )
                for entry in self._entries(document):
                    if entry.id == receipt["project_id"]:
                        return {"project": entry.public(), "revision": document["revision"]}
                raise ProjectRegistryRefusal(
                    "project_registry_unavailable", "Project registry is unavailable.", 409
                )
            for index, entry in enumerate(self._entries(document)):
                if entry.id != project_id:
                    continue
                if entry.revision != expected_revision:
                    raise ProjectRegistryRefusal(
                        "project_stale", "Project changed; refresh and try again.", 409
                    )
                body = {
                    "id": entry.id,
                    "workspace_id": entry.workspace_id,
                    "name": name if name is not None else entry.name,
                    "archived": archived if archived is not None else entry.archived,
                    "checkout": str(entry.checkout),
                    "git_identity": entry.git_identity,
                    "state_binding": entry.state_binding,
                    "state_identity": entry.state_identity,
                }
                body["revision"] = _revision(body)
                document["projects"][index] = body
                document["receipts"][idempotency_key] = {"intent": intent, "project_id": entry.id}
                self._save(document)
                return {"project": self._record(body).public(), "revision": document["revision"]}
        raise ProjectRegistryRefusal("project_unknown", "Project was not found.", 404)
