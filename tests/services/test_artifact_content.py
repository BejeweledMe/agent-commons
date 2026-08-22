from __future__ import annotations

from pathlib import Path

import pytest

from agent_commons.services import CommonsManager
from agent_commons.services.artifact_content import ArtifactPreviewReader, ArtifactPreviewRefusal


def _png(width: int = 3, height: int = 2) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00\x00\x00\x00\x00"
    )


def _jpeg(width: int = 3, height: int = 2) -> bytes:
    return (
        b"\xff\xd8\xff\xc0\x00\x11\x08"
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00\xff\xd9"
    )


def _reader(
    tmp_path: Path,
    *,
    content: bytes,
    media_type: str = "image/png",
    classification: str = "internal",
) -> tuple[ArtifactPreviewReader, CommonsManager, Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    CommonsManager.initialize(repo, integrations=())
    source = repo / "screens" / "first-screen"
    source.parent.mkdir()
    source.write_bytes(content)
    state_root = tmp_path / "state"
    bootstrap = CommonsManager(repo, state_root=state_root)
    session = bootstrap.start_session(
        stable_instance_id="artifact-preview-test",
        principal="test",
        client="pytest",
        software="pytest",
        role="builder",
    )
    manager = CommonsManager(repo, state_root=state_root, session_id=session["session_id"])
    artifact = manager.register_artifact(
        source,
        media_type=media_type,
        classification=classification,
        idempotency_key="artifact-preview",
    )
    return ArtifactPreviewReader(manager), manager, source, artifact["entity_ref"]["id"]


@pytest.mark.parametrize(
    ("media_type", "content", "width", "height"),
    [("image/png", _png(), 3, 2), ("image/jpeg", _jpeg(), 3, 2)],
)
def test_reader_returns_only_manifest_bound_png_or_jpeg(
    tmp_path: Path,
    media_type: str,
    content: bytes,
    width: int,
    height: int,
) -> None:
    reader, _manager, _source, artifact_id = _reader(
        tmp_path, content=content, media_type=media_type
    )

    preview = reader.read(artifact_id)

    assert preview.media_type == media_type
    assert preview.content == content
    assert (preview.width, preview.height) == (width, height)


@pytest.mark.parametrize("classification", ["restricted", "pii", "secret"])
def test_reader_refuses_every_non_previewable_classification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    classification: str,
) -> None:
    reader, manager, _source, artifact_id = _reader(tmp_path, content=_png())
    bundle = manager.get_artifact_bundle(artifact_id)
    bundle["artifact"]["classification"] = classification
    bundle["manifest"]["classification"] = classification
    monkeypatch.setattr(manager, "get_artifact_bundle", lambda _artifact_id: bundle)

    with pytest.raises(ArtifactPreviewRefusal) as raised:
        reader.read(artifact_id)

    assert raised.value.code == "artifact_preview_classification_blocked"
    assert raised.value.status_code == 403


def test_reader_refuses_a_mismatched_projected_content_revision_before_reading_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader, manager, source, artifact_id = _reader(tmp_path, content=_png())
    bundle = manager.get_artifact_bundle(artifact_id)
    bundle["artifact"]["content_revision"] = "sha256:" + "0" * 64
    monkeypatch.setattr(manager, "get_artifact_bundle", lambda _artifact_id: bundle)
    source.unlink()

    with pytest.raises(ArtifactPreviewRefusal) as raised:
        reader.read(artifact_id)

    assert raised.value.code == "artifact_preview_manifest_invalid"


@pytest.mark.parametrize(
    ("content", "media_type", "expected_code"),
    [
        (b"not an image", "image/png", "artifact_preview_invalid_image"),
        (_png(), "text/plain", "artifact_preview_unsupported_media_type"),
    ],
)
def test_reader_refuses_unknown_media_or_mismatched_magic(
    tmp_path: Path,
    content: bytes,
    media_type: str,
    expected_code: str,
) -> None:
    reader, _manager, _source, artifact_id = _reader(
        tmp_path, content=content, media_type=media_type
    )

    with pytest.raises(ArtifactPreviewRefusal) as raised:
        reader.read(artifact_id)

    assert raised.value.code == expected_code


def test_reader_refuses_a_replaced_source(tmp_path: Path) -> None:
    reader, _manager, source, artifact_id = _reader(tmp_path, content=_png())
    source.write_bytes(_png(width=4, height=4))

    with pytest.raises(ArtifactPreviewRefusal) as raised:
        reader.read(artifact_id)

    assert raised.value.code == "artifact_preview_stale_source"


def test_reader_refuses_a_missing_or_symlinked_source(tmp_path: Path) -> None:
    reader, _manager, source, artifact_id = _reader(tmp_path, content=_png())
    source.unlink()

    with pytest.raises(ArtifactPreviewRefusal) as missing:
        reader.read(artifact_id)

    assert missing.value.code == "artifact_preview_missing_source"

    source.symlink_to(tmp_path / "outside.png")
    with pytest.raises(ArtifactPreviewRefusal) as symlink:
        reader.read(artifact_id)

    assert symlink.value.code == "artifact_preview_symlink_source"


def test_reader_refuses_byte_and_pixel_bombs(tmp_path: Path) -> None:
    _, byte_manager, _source, byte_artifact = _reader(tmp_path / "bytes", content=_png())
    byte_reader = ArtifactPreviewReader(byte_manager, max_bytes=8)
    with pytest.raises(ArtifactPreviewRefusal) as oversized:
        byte_reader.read(byte_artifact)
    assert oversized.value.code == "artifact_preview_oversize"

    _, pixel_manager, _source, pixel_artifact = _reader(tmp_path / "pixels", content=_png(5, 5))
    pixel_reader = ArtifactPreviewReader(pixel_manager, max_pixels=24)
    with pytest.raises(ArtifactPreviewRefusal) as pixel_bomb:
        pixel_reader.read(pixel_artifact)
    assert pixel_bomb.value.code == "artifact_preview_pixel_limit"
