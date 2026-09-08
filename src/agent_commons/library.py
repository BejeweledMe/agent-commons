"""Versioned service-owned methods and provider-neutral specializations.

Packaged content is immutable. Operator edits create retained content-addressed
versions outside the delegated workspace; no request can select a storage path.
Only exact references enter the ledger. Text is read through authenticated UI
or worker readers and composed ephemerally into a provider invocation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import Any

from agent_commons.core.canonical import loads_json_strict
from agent_commons.errors import ValidationError
from agent_commons.operator_files import (
    assert_outside_state_storage,
    assert_outside_workspace,
    replace_operator_file,
)
from agent_commons.storage.opstate import (
    OperationalStoragePolicy,
    ensure_private_directory,
    exclusive_lock,
)

LIBRARY_SCHEMA = "agent_commons.ui.library.v1"
MAX_FILE_BYTES = 65_536
MAX_BUNDLE_BYTES = 262_144
MAX_SNAPSHOT_BYTES = 1_048_576
MAX_STATE_BYTES = 2_097_152
MAX_CUSTOM_ENTRIES = 512
MAX_RECEIPTS = 4096
_ID = re.compile(r"[a-z][a-z0-9-]{0,63}").fullmatch
_DIGEST = re.compile(r"[0-9a-f]{64}").fullmatch
_POLICY = OperationalStoragePolicy("service library", True, "library", "resolved", True, True)


class LibraryError(ValidationError):
    """Closed, path-free refusal suitable for the operator API."""

    def __init__(self, code: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def _refuse(message: str, code: str = "library_invalid", status: int = 422) -> None:
    raise LibraryError(code, message, status)


def _json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _text(value: object, label: str, limit: int, *, empty: bool = False) -> str:
    if type(value) is not str or (not empty and not value.strip()):
        _refuse(f"Library {label} must be text.")
    if len(value.encode("utf-8")) > limit or any(
        ord(character) < 32 and character not in "\n\r\t" for character in value
    ):
        _refuse(f"Library {label} exceeds its text limit.")
    return value


@dataclass(frozen=True, slots=True)
class LibraryRef:
    kind: str
    source: str
    id: str
    version: str

    @classmethod
    def parse(cls, value: object, *, kind: str | None = None) -> LibraryRef:
        if not isinstance(value, Mapping) or set(value) != {"kind", "source", "id", "version"}:
            _refuse("Library reference has an invalid shape.")
        if (
            value["kind"] not in ("role", "skill")
            or value["source"] not in ("builtin", "custom")
            or type(value["id"]) is not str
            or not _ID(value["id"])
            or type(value["version"]) is not str
            or not _DIGEST(value["version"])
            or (kind is not None and value["kind"] != kind)
        ):
            _refuse("Library reference is invalid.")
        return cls(value["kind"], value["source"], value["id"], value["version"])

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "source": self.source, "id": self.id, "version": self.version}


def validate_library_ref(value: object, *, kind: str | None = None) -> dict[str, str]:
    return LibraryRef.parse(value, kind=kind).as_dict()


def default_library_root() -> Path:
    configured = os.environ.get("XDG_CONFIG_HOME", "")
    base = (
        Path(configured)
        if configured and Path(configured).is_absolute()
        else Path.home() / ".config"
    )
    return base / "agent-commons" / "library"


def _relative(value: object) -> str:
    if type(value) is not str or len(value) > 256 or "\\" in value or "\x00" in value:
        _refuse("Library resource path is invalid.")
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or any(part in ("", ".", "..") for part in value.split("/"))
        or path.suffix not in (".md", ".yaml")
    ):
        _refuse("Library resource path is invalid.")
    return value


def _skill_refs(content: Mapping[str, Any]) -> tuple[LibraryRef, ...]:
    values = [content["entry_skill"], *content.get("core_skills", [])]
    values.extend(item["ref"] for item in content.get("conditional_skills", []))
    result: dict[str, LibraryRef] = {}
    for value in values:
        ref = LibraryRef.parse(value, kind="skill")
        if ref.id in result and result[ref.id] != ref:
            _refuse("A role cannot select different versions of the same skill identity.")
        result[ref.id] = ref
    return tuple(result.values())


def _content(kind: str, value: object) -> dict[str, Any]:
    common = {"name", "description"}
    allowed = common | (
        {"instruction"}
        if kind == "skill"
        else {"system_prompt", "group", "entry_skill", "core_skills", "conditional_skills"}
    )
    if not isinstance(value, Mapping) or set(value) - allowed or not common <= set(value):
        _refuse("Library content has an invalid shape.")
    result: dict[str, Any] = {
        "name": _text(value["name"], "name", 800),
        "description": _text(value["description"], "description", 16_000, empty=True),
    }
    if len(result["name"]) > 200 or len(result["description"]) > 4000:
        _refuse("Library name or description exceeds its character limit.")
    if kind == "skill":
        result["instruction"] = _text(value.get("instruction"), "instruction", MAX_FILE_BYTES)
        return result
    result["system_prompt"] = _text(value.get("system_prompt"), "role guidance", MAX_FILE_BYTES)
    result["group"] = _text(value.get("group", "custom"), "group", 320)
    if len(result["group"]) > 80:
        _refuse("Library group exceeds its character limit.")
    result["entry_skill"] = validate_library_ref(value.get("entry_skill"), kind="skill")
    core = value.get("core_skills", [result["entry_skill"]])
    conditional = value.get("conditional_skills", [])
    if (
        type(core) is not list
        or len(core) > 8
        or type(conditional) is not list
        or len(conditional) > 32
    ):
        _refuse("Library role routes exceed their collection limit.")
    result["core_skills"] = [validate_library_ref(item, kind="skill") for item in core]
    routes = []
    for item in conditional:
        if not isinstance(item, Mapping) or set(item) != {"ref", "when"}:
            _refuse("Library conditional route has an invalid shape.")
        when = _text(item["when"], "route condition", 16_000)
        if len(when) > 4000:
            _refuse("Library route condition exceeds its character limit.")
        routes.append({"ref": validate_library_ref(item["ref"], kind="skill"), "when": when})
    result["conditional_skills"] = routes
    for collection in (result["core_skills"], [item["ref"] for item in routes]):
        if len({_json(item) for item in collection}) != len(collection):
            _refuse("Library role repeats a route.")
    _skill_refs(result)
    return result


def library_snapshot(
    kind: str, source: str, identifier: str, content: Mapping[str, Any], files: Mapping[str, str]
) -> dict[str, Any]:
    """Build the exact object hashed by the importer, editor and launch resolver."""
    if kind not in ("role", "skill") or source not in ("builtin", "custom") or not _ID(identifier):
        _refuse("Library identity is invalid.")
    validated = _content(kind, content)
    if len(files) > 128 or (kind == "role" and files):
        _refuse("Library resource collection is invalid.")
    texts = {
        _relative(path): _text(text, "resource", MAX_FILE_BYTES, empty=True)
        for path, text in files.items()
    }
    if sum(len(text.encode()) for text in texts.values()) > MAX_BUNDLE_BYTES:
        _refuse("Library resource closure exceeds its size limit.", "library_too_large")
    if kind == "skill" and texts.get("SKILL.md") != validated["instruction"]:
        _refuse("Library primary instruction does not match its resource.")
    hashes = {path: _sha(text.encode()) for path, text in sorted(texts.items())}
    version = _sha(
        _json(
            {
                "kind": kind,
                "source": source,
                "id": identifier,
                "content": validated,
                "files": hashes,
            }
        )
    )
    ref = LibraryRef(kind, source, identifier, version)
    return {"ref": ref.as_dict(), "content": validated, "files": texts}


def _verified(value: object, ref: LibraryRef) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"ref", "content", "files"}:
        _refuse("Library version is malformed.", "library_version_unavailable", 409)
    if not isinstance(value["files"], Mapping):
        _refuse("Library resources are malformed.")
    result = library_snapshot(ref.kind, ref.source, ref.id, value["content"], value["files"])
    if result["ref"] != ref.as_dict() or value["ref"] != ref.as_dict():
        _refuse("Library version failed its content check.", "library_digest_mismatch", 409)
    return result


def _resource_bytes(relative: str, limit: int) -> bytes:
    path = resources.files("agent_commons.resources").joinpath("library", *relative.split("/"))
    if isinstance(path, Path):
        boundary = resources.files("agent_commons.resources").joinpath("library")
        current = path
        while current != boundary:
            if current.is_symlink():
                _refuse(
                    "Packaged library resource is symlinked.", "library_version_unavailable", 409
                )
            current = current.parent
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise LibraryError(
            "library_version_unavailable", "Packaged library resource is unavailable.", 409
        ) from exc
    if len(raw) > limit:
        _refuse("Packaged library resource exceeds its limit.", "library_too_large")
    return raw


def _manifest() -> dict[str, Any]:
    value = loads_json_strict(_resource_bytes("manifest.json", MAX_SNAPSHOT_BYTES))
    if not isinstance(value, dict) or value.get("schema") != "agent-commons.library-pack.v1":
        _refuse("Packaged library manifest is unsupported.")
    if not isinstance(value.get("entries"), list) or len(value["entries"]) != 75:
        _refuse("Packaged library inventory is incomplete.")
    seen = set()
    for entry in value["entries"]:
        if not isinstance(entry, dict) or "ref" not in entry:
            _refuse("Packaged library entry is malformed.")
        ref = LibraryRef.parse(entry["ref"])
        if ref.source != "builtin" or (ref.kind, ref.id) in seen:
            _refuse("Packaged library identities are invalid.")
        fields = {"ref", "name", "description"}
        if ref.kind == "role":
            fields |= {"group", "entry_skill", "core_skills", "conditional_skills"}
        if set(entry) != fields:
            _refuse("Packaged library metadata has unsupported fields.")
        # Validate through the same closed DTO rules without exposing guidance.
        metadata = {key: item for key, item in entry.items() if key != "ref"}
        metadata["instruction" if ref.kind == "skill" else "system_prompt"] = "metadata"
        _content(ref.kind, metadata)
        seen.add((ref.kind, ref.id))
    if sum(kind == "role" for kind, _identifier in seen) != 30:
        _refuse("Packaged role inventory is incomplete.")
    return value


@dataclass(frozen=True, slots=True)
class LibraryBundle:
    role_ref: LibraryRef
    skill_refs: tuple[LibraryRef, ...]
    source_digest: str
    instruction: str


class LibraryStore:
    """One trusted service storage root, supplied by composition code, never an API body."""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        workspace_root: str | Path | None = None,
        state_root: str | Path | None = None,
        state_base: str | Path | None = None,
    ) -> None:
        self.root = Path(root) if root is not None else default_library_root()
        assert_outside_workspace(self.root, workspace_root, label="service library")
        storage_root = state_root if state_root is not None else state_base
        if storage_root is not None:
            assert_outside_state_storage(
                self.root, state_root=storage_root, state_base=state_base, label="service library"
            )
        self._check_directory(self.root)

    @staticmethod
    def _check_directory(path: Path) -> None:
        if path.is_symlink():
            _refuse("Service library storage cannot be symlinked.", "library_storage_refused", 409)
        if path.exists():
            mode = path.stat()
            if (
                not stat.S_ISDIR(mode.st_mode)
                or mode.st_mode & 0o022
                or (hasattr(os, "getuid") and mode.st_uid not in (0, os.getuid()))
            ):
                _refuse(
                    "Service library storage ownership is unsafe.", "library_storage_refused", 409
                )

    def _read(self, path: Path, limit: int) -> object:
        self._check_directory(self.root)
        current = path.parent
        while current != self.root:
            self._check_directory(current)
            current = current.parent
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(descriptor, "rb") as handle:
                metadata = os.fstat(handle.fileno())
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_mode & 0o022
                    or (hasattr(os, "getuid") and metadata.st_uid not in (0, os.getuid()))
                ):
                    _refuse("Library version ownership is unsafe.", "library_storage_refused", 409)
                raw = handle.read(limit + 1)
        except OSError as exc:
            raise LibraryError(
                "library_version_unavailable", "Library version is unavailable.", 409
            ) from exc
        if len(raw) > limit:
            _refuse("Library stored data exceeds its limit.", "library_too_large")
        return loads_json_strict(raw)

    def _version_path(self, ref: LibraryRef) -> Path:
        return self.root / "versions" / ref.source / ref.kind / ref.id / f"{ref.version}.json"

    def _state(self) -> dict[str, Any]:
        path = self.root / "state.json"
        if not path.exists() and not path.is_symlink():
            return {"schema": "agent-commons.library-state.v1", "heads": {}, "receipts": {}}
        value = self._read(path, MAX_STATE_BYTES)
        if (
            not isinstance(value, dict)
            or set(value) != {"schema", "heads", "receipts"}
            or value["schema"] != "agent-commons.library-state.v1"
        ):
            _refuse("Library state is malformed.", "library_storage_refused", 409)
        if (
            not isinstance(value["heads"], dict)
            or not isinstance(value["receipts"], dict)
            or len(value["heads"]) > MAX_CUSTOM_ENTRIES
            or len(value["receipts"]) > MAX_RECEIPTS
        ):
            _refuse("Library state exceeds its collection limits.")
        for key, head in value["heads"].items():
            ref = LibraryRef.parse(head)
            if ref.source != "custom" or key != f"{ref.kind}:{ref.id}":
                _refuse("Library current-version index is invalid.")
        # Receipts contain only operation fingerprints and exact result refs.
        for key, receipt in value["receipts"].items():
            if (
                type(key) is not str
                or not _DIGEST(key)
                or not isinstance(receipt, dict)
                or set(receipt) != {"request_digest", "ref"}
                or type(receipt["request_digest"]) is not str
                or not _DIGEST(receipt["request_digest"])
            ):
                _refuse("Library edit receipt is invalid.")
            LibraryRef.parse(receipt["ref"])
        return value

    def resolve(self, value: object) -> dict[str, Any]:
        ref = LibraryRef.parse(value)
        stored = self._version_path(ref)
        if stored.exists() or stored.is_symlink():
            return _verified(self._read(stored, MAX_SNAPSHOT_BYTES), ref)
        if ref.source != "builtin":
            _refuse("Exact library version is unavailable.", "library_version_unavailable", 409)
        entry = next(
            (entry for entry in _manifest()["entries"] if entry["ref"] == ref.as_dict()), None
        )
        if entry is None:
            _refuse("Exact library version is unavailable.", "library_version_unavailable", 409)
        raw = loads_json_strict(_resource_bytes(f"{ref.kind}s/{ref.id}.json", MAX_SNAPSHOT_BYTES))
        if ref.kind == "role":
            contract = _resource_bytes("common-contract.md", MAX_FILE_BYTES).decode("utf-8")
            raw["content"]["system_prompt"] = contract + raw["content"]["system_prompt"]
            files = {}
        else:
            files = {
                path: _resource_bytes(f"skills/{ref.id}/{_relative(path)}", MAX_FILE_BYTES).decode(
                    "utf-8"
                )
                for path in raw["files"]
            }
            raw["content"]["instruction"] = files["SKILL.md"]
        return _verified({"ref": raw["ref"], "content": raw["content"], "files": files}, ref)

    def allowed_skill_refs(self, role_ref: object) -> tuple[dict[str, str], ...]:
        ref = LibraryRef.parse(role_ref, kind="role")
        return tuple(item.as_dict() for item in _skill_refs(self.resolve(ref.as_dict())["content"]))

    @staticmethod
    def _summary(snapshot: Mapping[str, Any]) -> dict[str, Any]:
        content = snapshot["content"]
        result = {
            "ref": dict(snapshot["ref"]),
            "name": content["name"],
            "description": content["description"],
        }
        if snapshot["ref"]["kind"] == "role":
            result.update(
                {
                    key: content[key]
                    for key in ("group", "entry_skill", "core_skills", "conditional_skills")
                }
            )
        return result

    def catalog(self, *, include_editable: bool = False) -> dict[str, Any]:
        entries = [dict(entry) for entry in _manifest()["entries"]]
        entries.extend(self._summary(self.resolve(ref)) for ref in self._state()["heads"].values())
        result = {
            "schema": LIBRARY_SCHEMA,
            "roles": [entry for entry in entries if entry["ref"]["kind"] == "role"],
            "skills": [entry for entry in entries if entry["ref"]["kind"] == "skill"],
            "editing_enabled": bool(include_editable),
        }
        from agent_commons.library_organization import SkillOrganization

        result["skill_organization"] = SkillOrganization(self).catalog(result["skills"])
        return result

    def detail(self, value: object, *, editable: bool = False) -> dict[str, Any]:
        snapshot = self.resolve(value)
        result = self._summary(snapshot)
        result["files"] = [
            {"path": path, "sha256": _sha(text.encode()), "size": len(text.encode())}
            for path, text in sorted(snapshot["files"].items())
        ]
        if editable:
            result["content"] = snapshot["content"]
        return result

    def read_file(self, value: object, path: str = "SKILL.md") -> dict[str, Any]:
        ref = LibraryRef.parse(value, kind="skill")
        relative = _relative(path)
        text = self.resolve(ref.as_dict())["files"].get(relative)
        if text is None:
            _refuse(
                "Resource is not part of this exact skill version.",
                "library_resource_unavailable",
                404,
            )
        return {"path": relative, "text": text, "sha256": _sha(text.encode())}

    def _persist(self, snapshot: Mapping[str, Any]) -> None:
        ref = LibraryRef.parse(snapshot["ref"])
        target = self._version_path(ref)
        if target.exists() or target.is_symlink():
            _verified(self._read(target, MAX_SNAPSHOT_BYTES), ref)
            return
        current = self.root
        for component in target.parent.relative_to(self.root).parts:
            current = current / component
            self._check_directory(current)
            ensure_private_directory(current, policy=_POLICY)
        encoded = _json(snapshot)
        if len(encoded) > MAX_SNAPSHOT_BYTES:
            _refuse("Library version exceeds its storage limit.", "library_too_large")
        replace_operator_file(target, encoded, label="service library version")

    def retain(self, value: object) -> dict[str, str]:
        ref = LibraryRef.parse(value)
        snapshot = self.resolve(ref.as_dict())
        snapshots = [snapshot]
        if ref.kind == "role":
            snapshots = [
                self.resolve(item.as_dict()) for item in _skill_refs(snapshot["content"])
            ] + snapshots
        self._check_directory(self.root)
        with exclusive_lock(self.root / "library.lock", policy=_POLICY):
            for item in snapshots:
                self._persist(item)
        return ref.as_dict()

    def save(
        self,
        *,
        kind: str,
        id: str,
        content: object,
        expected_version: str | None,
        idempotency_key: str,
        fork_ref: object | None = None,
    ) -> dict[str, Any]:
        if kind not in ("role", "skill") or type(id) is not str or not _ID(id):
            _refuse("Library identity is invalid.")
        if expected_version is not None and (
            type(expected_version) is not str or not _DIGEST(expected_version)
        ):
            _refuse("Library expected version is invalid.")
        key = _text(idempotency_key, "idempotency key", 256)
        validated = _content(kind, content)
        fork = LibraryRef.parse(fork_ref, kind=kind) if fork_ref is not None else None
        if fork is not None and expected_version is not None:
            _refuse("A fork creates a new custom entry and requires an empty expected version.")
        request_digest = _sha(
            _json(
                {
                    "kind": kind,
                    "id": id,
                    "content": validated,
                    "expected_version": expected_version,
                    "fork_ref": fork.as_dict() if fork else None,
                }
            )
        )
        receipt_key = _sha(key.encode())
        self._check_directory(self.root)
        with exclusive_lock(self.root / "library.lock", policy=_POLICY):
            state = self._state()
            receipt = state["receipts"].get(receipt_key)
            if receipt is not None:
                if receipt["request_digest"] != request_digest:
                    _refuse(
                        "Library idempotency key was used for another edit.",
                        "library_idempotency_conflict",
                        409,
                    )
                result = self.resolve(receipt["ref"])
                return {"ref": result["ref"], "content": result["content"]}
            head_key = f"{kind}:{id}"
            head = state["heads"].get(head_key)
            if (head["version"] if head else None) != expected_version:
                _refuse(
                    "Library entry changed. Reload before saving.", "library_revision_conflict", 409
                )
            base = self.resolve(fork.as_dict() if fork else head) if fork or head else None
            files = dict(base["files"]) if base is not None and kind == "skill" else {}
            if kind == "skill":
                files["SKILL.md"] = validated["instruction"]
            else:
                for skill in _skill_refs(validated):
                    self._persist(self.resolve(skill.as_dict()))
            result = library_snapshot(kind, "custom", id, validated, files)
            if len(state["receipts"]) >= MAX_RECEIPTS or (
                head is None and len(state["heads"]) >= MAX_CUSTOM_ENTRIES
            ):
                _refuse("Service library has reached its edit capacity.", "library_capacity", 409)
            self._persist(result)
            state["heads"][head_key] = result["ref"]
            state["receipts"][receipt_key] = {
                "request_digest": request_digest,
                "ref": result["ref"],
            }
            encoded = _json(state)
            if len(encoded) > MAX_STATE_BYTES:
                _refuse("Service library state exceeds its limit.", "library_capacity", 409)
            replace_operator_file(self.root / "state.json", encoded, label="service library state")
            return {"ref": result["ref"], "content": result["content"]}

    def compose_role(self, value: object) -> LibraryBundle:
        ref = LibraryRef.parse(value, kind="role")
        role = self.resolve(ref.as_dict())
        refs = _skill_refs(role["content"])
        # Resolve every permitted dependency now, before provider probes or an
        # attempt. Companion bodies stay available through the bounded reader.
        skills = [self.resolve(item.as_dict()) for item in refs]
        primary = skills[0]
        digest = _sha(_json({"role": ref.as_dict(), "skills": [item.as_dict() for item in refs]}))
        parts = [
            f"\n\nService specialization {ref.id} @ {ref.version} (bundle_sha256={digest}).",
            "The following methods supply guidance only. They grant no tools, permissions, "
            "independent-review status, or authority beyond this delegation.",
            role["content"]["system_prompt"],
            "The primary skill and its reference manifest follow. Load reference bodies only "
            "when their method requires them. For another listed route, "
            "call the injected Agent Commons commons_read_skill tool with its skill_id and "
            "path='SKILL.md' before using it; read additional references through the same tool. "
            "Read every remaining slice while next_offset is present in a tool response. "
            "Only the exact versions pinned to this role are available. Use the provider's "
            "existing Agent Commons MCP transport. Never resolve these methods from a home "
            "directory or install a substitute.",
            "Available exact method identities: "
            + ", ".join(f"{item.id}@{item.version}" for item in refs),
            "Conditional routes: "
            + "; ".join(
                f"{route['ref']['id']}: {route['when']}"
                for route in role["content"]["conditional_skills"]
            ),
        ]
        parts.append(
            f"Service skill {refs[0].id} / SKILL.md @ {refs[0].version}:\n"
            + primary["files"]["SKILL.md"]
        )
        reference_manifest = [
            {"path": path, "sha256": _sha(text.encode()), "size": len(text.encode())}
            for path, text in sorted(primary["files"].items())
            if path != "SKILL.md"
        ]
        parts.append(
            f"Reference manifest for skill_id={refs[0].id}; use commons_read_skill for "
            "each needed relative path:\n" + _json(reference_manifest).decode()
        )
        instruction = "\n\n".join(parts)
        if len(instruction.encode()) > MAX_BUNDLE_BYTES:
            _refuse(
                "Specialization primary material exceeds the launch limit.", "library_too_large"
            )
        return LibraryBundle(ref, refs, digest, instruction)
