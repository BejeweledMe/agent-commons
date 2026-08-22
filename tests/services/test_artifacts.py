from __future__ import annotations

import inspect

from agent_commons.services.artifacts import ArtifactCommands
from agent_commons.services.manager import CommonsManager

_PUBLIC_ARTIFACT_METHODS = {
    "get_artifact_bundle",
    "list_artifacts",
    "register_artifact",
    "revise_artifact",
}

_SIGNATURES = {
    "list_artifacts": "(self) -> 'list[dict[str, Any]]'",
    "get_artifact_bundle": "(self, artifact_id: 'str') -> 'dict[str, Any]'",
    "_hash_artifact": "(self, source: 'str | Path') -> 'tuple[str, int, str]'",
    "_artifact_manifest": (
        "(self, artifact_id: 'str', source: 'str | Path', *, media_type: 'str', "
        "classification: 'str', metadata: 'Mapping[str, Any] | None') -> "
        "'tuple[dict[str, Any], str]'"
    ),
    "register_artifact": (
        "(self, source: 'str | Path', *, media_type: 'str' = 'application/octet-stream', "
        "classification: 'str' = 'internal', metadata: 'Mapping[str, Any] | None' = None, "
        "idempotency_key: 'str | None' = None) -> 'dict[str, Any]'"
    ),
    "revise_artifact": (
        "(self, artifact_id: 'str', expected_revision: 'str', source: 'str | Path', *, "
        "media_type: 'str' = 'application/octet-stream', classification: 'str' = 'internal', "
        "metadata: 'Mapping[str, Any] | None' = None, idempotency_key: 'str | None' = None) "
        "-> 'dict[str, Any]'"
    ),
}


def test_artifact_commands_are_the_exact_manager_surface_without_proxies() -> None:
    public = {
        name
        for name, value in ArtifactCommands.__dict__.items()
        if callable(value) and not name.startswith("_")
    }
    assert public == _PUBLIC_ARTIFACT_METHODS
    assert "_hash_artifact" in ArtifactCommands.__dict__
    assert "_artifact_manifest" in ArtifactCommands.__dict__

    for name, expected in _SIGNATURES.items():
        assert name not in CommonsManager.__dict__
        assert getattr(CommonsManager, name) is getattr(ArtifactCommands, name)
        assert str(inspect.signature(getattr(CommonsManager, name))) == expected
