"""Shared placement and write discipline for operator-owned files.

The role catalogue and the runtime profile config are both operator-owned:
files a human (or the panel acting for one) writes outside the delegated
workspace, and that the runtime later trusts.  They share three rules that no
single consumer should own:

- an operator file must not live inside the delegated workspace, because a
  writable builder runs with write access to that tree and could rewrite the
  file that defines its own limits;
- an operator file must not live inside the operational state base or state
  root, because a foreign file inside the state base switches state resolution
  to legacy-exact mode and makes ``ensure_layout`` refuse the whole workspace;
- an operator file is published atomically with private permissions, so a
  rejected or interrupted write can never leave a half-written file that the
  next launch fails to read.

This module deliberately depends only on :mod:`agent_commons.errors`, so the
catalogue, ``ui/``, and ``services/`` can all use it without any of them
importing the others.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from agent_commons.errors import ConfigurationError

#: Refusal code for a target inside the state base or state root, frozen by the
#: wave contract; consumers surface it verbatim so the frontend can render it.
STATE_REFUSAL_CODE = "setup_path_refused_state_base"


def assert_outside_workspace(
    source: str | Path, workspace_root: str | Path | None, *, label: str
) -> None:
    """Refuse a path inside (or equal to) the delegated workspace.

    ``workspace_root`` must exist: a workspace that cannot be resolved is an
    error rather than a pass, because a missing anchor would silently disable
    the check.
    """

    if workspace_root is None:
        return
    try:
        resolved_source = Path(source).resolve()
        resolved_workspace = Path(workspace_root).expanduser().resolve(strict=True)
    except OSError as exc:
        raise ConfigurationError(f"{label} cannot be resolved safely") from exc
    if resolved_source == resolved_workspace or resolved_workspace in resolved_source.parents:
        raise ConfigurationError(f"{label} must be outside the delegated workspace")


def assert_outside_state_storage(
    target: str | Path,
    *,
    state_root: str | Path,
    state_base: str | Path | None = None,
    label: str,
) -> None:
    """Refuse a path equal to or inside the state base or state root.

    A foreign file inside the state base is not cosmetic damage: it switches
    state resolution to legacy-exact mode and ``ensure_layout`` then refuses
    the workspace with ``state_owner_unproven``.  The roots are checked whether
    or not they exist yet, because a file written at such a path today poisons
    the base the moment it is created.

    Raises :class:`ConfigurationError` with ``code`` set to
    :data:`STATE_REFUSAL_CODE`.
    """

    try:
        resolved_target = Path(target).expanduser().resolve()
    except OSError as exc:
        raise ConfigurationError(f"{label} cannot be resolved safely") from exc
    roots: tuple[tuple[str, str | Path | None], ...] = (
        ("state_base", state_base),
        ("state_root", state_root),
    )
    for kind, root in roots:
        if root is None:
            continue
        try:
            resolved_root = Path(root).expanduser().resolve()
        except OSError as exc:
            raise ConfigurationError(f"{label} cannot be resolved safely") from exc
        if resolved_target == resolved_root or resolved_root in resolved_target.parents:
            error = ConfigurationError(
                f"{label} must be outside the operational state directory: {resolved_root}"
            )
            error.code = STATE_REFUSAL_CODE  # type: ignore[attr-defined]
            error.details = {  # type: ignore[attr-defined]
                "target": str(resolved_target),
                "refused_root": str(resolved_root),
                "root_kind": kind,
            }
            raise error


def replace_operator_file(target: Path, payload: bytes, *, label: str) -> None:
    """Publish ``payload`` at ``target`` atomically, or leave the old file as it was.

    The bytes land in a ``0600`` sibling temporary file, are fsynced, and are
    published by rename; any failure removes the temporary file instead of the
    target.  The caller validates content and placement first — this function
    only refuses a symlink target and a missing parent directory.
    """

    if target.is_symlink():
        raise ConfigurationError(f"{label} must not be a symlink")
    parent = target.parent
    if not parent.is_dir():
        raise ConfigurationError(f"{label} directory does not exist: {parent}")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        temporary = None  # type: ignore[assignment]
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
