"""One private collaboration namespace shared by HTTP and worker entry points.

Operational state may live in a checkout. Attachment bytes and preview metadata
must not. Resolve their separate root without creating directories or migrating
any data. Explicit external operational roots retain their existing layout.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from agent_commons.errors import ConfigurationError
from agent_commons.platform_support import require_supported_platform


def collaboration_state_root(manager: Any) -> Path:
    """Resolve from canonical project identity, never an HTTP or tool path argument."""
    return private_collaboration_root(
        manager.paths.state_root,
        project_root=manager.repo_root,
        workspace_id=manager.workspace_id,
    )


def private_collaboration_root(
    state_root: str | Path, *, project_root: str | Path, workspace_id: str
) -> Path:
    require_supported_platform()
    project = Path(project_root).expanduser().resolve()
    state = Path(state_root).expanduser().absolute()
    if not isinstance(workspace_id, str) or not workspace_id:
        raise ConfigurationError("Private collaboration requires a workspace identity.")
    if not state.resolve().is_relative_to(project):
        # Do not resolve away symlinks: the stores pin and reject unsafe components.
        return state
    configured = os.environ.get("XDG_STATE_HOME")
    if configured and Path(configured).is_absolute():
        base = Path(configured)
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path.home() / ".local" / "state"
    # Different checkouts of the same canonical workspace must not share bytes.
    identity = json.dumps([str(project), workspace_id], separators=(",", ":"))
    partition = hashlib.sha256(identity.encode()).hexdigest()
    private = base / "agent-commons" / "collaboration-v1" / partition
    if private.resolve().is_relative_to(project):
        raise ConfigurationError("Private collaboration storage must be outside the workspace.")
    return private
