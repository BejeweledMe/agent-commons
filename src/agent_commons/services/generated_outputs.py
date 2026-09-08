"""Closed provenance contract for generated task image artifact metadata."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent_commons.core.ids import is_typed_id
from agent_commons.domain.snapshot import ProjectSnapshot

OUTPUT_KIND = "task_design_image"


def generated_series(delegation_id: str, source_path: str) -> str:
    return "generated." + hashlib.sha256(f"{delegation_id}\0{source_path}".encode()).hexdigest()


def producer_for_task(
    snapshot: ProjectSnapshot,
    session: str | None,
    task_id: str,
    delegations: Mapping[str, list[tuple[str, Mapping[str, Any]]]],
) -> tuple[str, str] | None:
    candidates = []
    for identifier, delegation in delegations.get(session or "", ()):
        agent = delegation.get("agent_id")
        target_revision = delegation.get("target_revision")
        if (
            delegation.get("target_ref") == {"kind": "task", "id": task_id}
            and isinstance(agent, str)
            and agent in snapshot.agents
            and isinstance(target_revision, str)
            and snapshot.entity_revision_actor("task", task_id, target_revision) is not None
        ):
            candidates.append((identifier, agent))
    return candidates[0] if len(candidates) == 1 else None


def validated_generated_metadata(
    snapshot: ProjectSnapshot,
    artifact: Mapping[str, Any],
    manifest: Mapping[str, Any],
    delegations: Mapping[str, list[tuple[str, Mapping[str, Any]]]],
) -> dict[str, Any] | None:
    """Metadata alone never establishes a producer; prove every canonical binding."""
    value = manifest.get("metadata")
    if (
        not isinstance(value, dict)
        or set(value) != {"output_kind", "output_task", "output_delegation", "series_id", "title"}
        or value["output_kind"] != OUTPUT_KIND
    ):
        return None
    task = value["output_task"]
    delegation = value["output_delegation"]
    for binding, kind in ((task, "task"), (delegation, "delegation")):
        if (
            not isinstance(binding, dict)
            or set(binding) != {"id", "revision"}
            or not is_typed_id(binding["id"], kind)
            or not is_typed_id(binding["revision"], "evt")
            or snapshot.entity_revision_actor(kind, binding["id"], binding["revision"]) is None
        ):
            return None
    title = value["title"]
    if (
        not isinstance(title, str)
        or not 1 <= len(title) <= 256
        or any(ord(char) < 32 or ord(char) == 127 for char in title)
    ):
        return None
    source = manifest.get("source")
    if (
        not isinstance(source, dict)
        or not isinstance(source.get("path"), str)
        or not re.fullmatch(r"generated\.[0-9a-f]{64}", str(value["series_id"]))
        or value["series_id"] != generated_series(delegation["id"], source["path"])
    ):
        return None
    relative = Path(source["path"])
    if (
        relative.is_absolute()
        or not relative.parts
        or relative.as_posix() != source["path"]
        or any(part.startswith(".") for part in relative.parts)
        or relative.suffix.lower() not in {".png", ".jpg", ".jpeg"}
        or "\\" in source["path"]
        or any(ord(char) < 32 or ord(char) == 127 for char in source["path"])
    ):
        return None
    revision = artifact.get("effective_revision") or artifact.get("revision")
    session = snapshot.entity_revision_actor("artifact", artifact["id"], revision)
    producer = producer_for_task(snapshot, session, task["id"], delegations)
    if producer is None or producer[0] != delegation["id"]:
        return None
    if (
        artifact.get("classification") not in {"internal", "public"}
        or manifest.get("classification") != artifact.get("classification")
        or manifest.get("artifact_id") != artifact["id"]
        or manifest.get("revision") != artifact.get("content_revision")
        or artifact.get("manifest_ref") not in snapshot.known_manifest_ids
    ):
        return None
    return {**value, "producer_session_id": session, "producer_agent_id": producer[1]}
