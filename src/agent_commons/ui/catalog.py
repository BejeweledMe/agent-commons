"""Operator-owned catalogue of what a role setting may select.

Two halves with different owners, because only one of them is safe to write
from a loopback HTTP surface:

* **capability-granting** -- skills, MCP servers, tools.  A role referencing one
  of these is choosing from a set the operator declared; the UI shows the list
  and never edits it.  A bearer token must not be able to widen what a child
  process may do.
* **narrowing** -- presets and the display names around them.  These can only
  reduce authority, so they live in the ledger and are written from the UI.

The file is loaded with the same discipline as the runtime profile config: it
must sit outside the delegated workspace, be a regular non-symlink file, not be
group/world writable, be owned by the operator or root, and stay small.  A
writable builder runs with `--sandbox workspace-write`, so a catalogue inside
the workspace would be a catalogue the agent can rewrite.
"""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from agent_commons.errors import ConfigurationError

CATALOG_SECTIONS = ("skills", "mcp_servers", "tools")
_MAX_BYTES = 64 * 1024
_CATALOG_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_ENTRY_FIELDS = frozenset({"id", "title", "description"})


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
    return {
        "id": identifier,
        "title": str(value.get("title") or identifier),
        "description": str(value.get("description") or ""),
    }


def empty_catalog() -> dict[str, list[dict[str, str]]]:
    return {section: [] for section in CATALOG_SECTIONS}


def load_role_catalog(
    path: str | Path | None, *, workspace_root: str | Path | None = None
) -> dict[str, list[dict[str, str]]]:
    """Read the operator catalogue, or return an empty one when unconfigured."""

    if path is None:
        return empty_catalog()
    source = Path(path).expanduser()
    if workspace_root is not None:
        try:
            resolved_source = source.resolve(strict=True)
            resolved_workspace = Path(workspace_root).expanduser().resolve(strict=True)
        except OSError as exc:
            raise ConfigurationError("role catalog cannot be resolved safely") from exc
        if resolved_source == resolved_workspace or resolved_workspace in resolved_source.parents:
            raise ConfigurationError("role catalog must be outside the delegated workspace")
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


def catalog_ids(catalogue: Mapping[str, Any], section: str) -> frozenset[str]:
    return frozenset(str(entry["id"]) for entry in catalogue.get(section, ()))
