"""Stable fingerprint for matching separately installed Agent Commons runtimes."""

from __future__ import annotations

import hashlib
from pathlib import Path


def agent_commons_source_sha256(package_root: Path | None = None) -> str:
    """Hash every Python source path and byte in one Agent Commons package tree."""

    root = (package_root or Path(__file__).resolve().parents[1]).resolve()
    sources = sorted(
        path for path in root.rglob("*.py") if path.is_file() and not path.is_symlink()
    )
    if not sources:
        raise RuntimeError("Agent Commons package contains no Python source files")
    digest = hashlib.sha256()
    for path in sources:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        content = path.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()
