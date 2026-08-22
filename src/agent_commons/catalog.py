"""Operator-owned catalogue of what a role setting may select.

It lives beside the runtime profile config rather than inside it, because the
two have different writers.  The profile config names executables and is edited
only by hand; this file names skills and tools and may be edited from the local
UI when the operator starts it with that gate open.  Keeping them apart is what
makes "the panel can manage the catalogue" and "the panel cannot change what a
provider process is" both true.

Loading uses the runtime config's discipline: outside the delegated workspace, a
regular non-symlink file, not group/world writable, owned by the operator or
root, and size-bounded.  A writable builder runs with `--sandbox
workspace-write`, so a catalogue inside the workspace would be a catalogue the
agent can rewrite.
"""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from agent_commons.errors import ConfigurationError, ValidationError
from agent_commons.operator_files import assert_outside_workspace, replace_operator_file

_LABEL = "role catalog"

#: A skill carries operator-authored instruction text; a tool carries only an
#: identity, because the tool it names is already fixed by the profile.
CATALOG_SECTIONS = ("skills", "tools")
_MAX_BYTES = 64 * 1024
_CATALOG_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_ENTRY_FIELDS = frozenset({"id", "title", "description", "instruction"})
_MAX_INSTRUCTION = 4_000


def empty_catalog() -> dict[str, list[dict[str, str]]]:
    return {section: [] for section in CATALOG_SECTIONS}


def _entry(section: str, value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ConfigurationError(f"catalog {section} entries must be mappings")
    unknown = sorted(set(value) - _ENTRY_FIELDS)
    if unknown:
        raise ConfigurationError(
            f"catalog {section} entry has unsupported fields: {', '.join(unknown)}"
        )
    identifier = value.get("id")
    if not isinstance(identifier, str) or _CATALOG_ID.fullmatch(identifier) is None:
        raise ConfigurationError(f"catalog {section} entry has an invalid id")
    entry = {
        "id": identifier,
        "title": str(value.get("title") or identifier),
        "description": str(value.get("description") or ""),
    }
    instruction = value.get("instruction")
    if section == "skills":
        # A skill with no text would add nothing to the run that selects it.
        if not isinstance(instruction, str) or not instruction.strip():
            raise ConfigurationError(f"catalog skill {identifier} needs instruction text")
        if len(instruction) > _MAX_INSTRUCTION:
            raise ConfigurationError(f"catalog skill {identifier} exceeds the instruction limit")
        entry["instruction"] = instruction
    elif instruction is not None:
        raise ConfigurationError(f"catalog {section} entries carry no instruction text")
    return entry


def _validated(value: Any) -> dict[str, list[dict[str, str]]]:
    if value is None:
        return empty_catalog()
    if not isinstance(value, Mapping):
        raise ConfigurationError("role catalog must be a mapping")
    unknown = sorted(set(value) - set(CATALOG_SECTIONS))
    if unknown:
        raise ConfigurationError("role catalog has unsupported sections: " + ", ".join(unknown))
    catalogue = empty_catalog()
    for section in CATALOG_SECTIONS:
        entries = value.get(section) or []
        if not isinstance(entries, list):
            raise ConfigurationError(f"catalog section {section} must be a list")
        seen: set[str] = set()
        for item in entries:
            entry = _entry(section, item)
            if entry["id"] in seen:
                raise ConfigurationError(f"catalog {section} repeats id {entry['id']}")
            seen.add(entry["id"])
            catalogue[section].append(entry)
    return catalogue


def load_role_catalog(
    path: str | Path | None, *, workspace_root: str | Path | None = None
) -> dict[str, list[dict[str, str]]]:
    """Read the operator catalogue, or return an empty one when unconfigured."""

    if path is None:
        return empty_catalog()
    source = Path(path).expanduser()
    assert_outside_workspace(source, workspace_root, label=_LABEL)
    if not source.exists():
        # An operator may name the file before creating it; an absent catalogue
        # is empty, while an unreadable one is an error.
        return empty_catalog()
    descriptor = -1
    try:
        descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ConfigurationError("role catalog must be a regular file")
        if metadata.st_mode & 0o022:
            raise ConfigurationError("role catalog must not be group/world writable")
        if hasattr(os, "getuid") and metadata.st_uid not in {0, os.getuid()}:
            raise ConfigurationError("role catalog must be owned by the operator or root")
        handle = os.fdopen(descriptor, "rb", closefd=True)
        descriptor = -1
        with handle:
            raw = handle.read(_MAX_BYTES + 1)
    except OSError as exc:
        raise ConfigurationError(
            "role catalog must be a readable regular non-symlink file"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(raw) > _MAX_BYTES:
        raise ConfigurationError("role catalog exceeds 64 KiB")
    try:
        value = yaml.safe_load(raw.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ConfigurationError("role catalog is not valid UTF-8 YAML") from exc
    return _validated(value)


def write_role_catalog(
    path: str | Path,
    catalogue: Mapping[str, Any],
    *,
    workspace_root: str | Path | None = None,
) -> dict[str, list[dict[str, str]]]:
    """Replace the catalogue atomically, or leave the old one exactly as it was.

    Validated before anything is written, then published by rename, so a
    rejected edit cannot leave a half-written file that the next launch fails
    to read.
    """

    target = Path(path).expanduser()
    assert_outside_workspace(target, workspace_root, label=_LABEL)
    validated = _validated(catalogue)
    body = yaml.safe_dump(validated, allow_unicode=True, sort_keys=True, width=88)
    encoded = body.encode("utf-8")
    if len(encoded) > _MAX_BYTES:
        raise ConfigurationError("role catalog exceeds 64 KiB")
    replace_operator_file(target, encoded, label=_LABEL)
    return validated


def catalog_ids(catalogue: Mapping[str, Any], section: str) -> frozenset[str]:
    return frozenset(str(entry["id"]) for entry in catalogue.get(section, ()))


def skill_instructions(
    catalogue: Mapping[str, Any], selected: tuple[str, ...]
) -> tuple[tuple[str, str], ...]:
    """Instruction text for each selected skill, in the order the role lists it.

    Fails closed on an id the catalogue does not have.  Dropping it silently
    would run the role without the skill it was configured to require, which is
    a different agent than the one the operator described.
    """

    by_id = {str(entry["id"]): entry for entry in catalogue.get("skills", ())}
    missing = sorted(set(selected) - set(by_id))
    if missing:
        raise ValidationError(
            "role selects skills the operator catalogue does not define: " + ", ".join(missing)
        )
    return tuple((identifier, str(by_id[identifier]["instruction"])) for identifier in selected)
