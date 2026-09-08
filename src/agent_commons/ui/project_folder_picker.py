"""Bounded native folder picker with an explicit headless fallback.

The browser cannot supply a trusted local path.  This service asks the host
OS for one directory, returns a closed DTO, and refuses rather than guessing
when no interactive chooser is available.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

from agent_commons.ui.project_registry import ProjectRegistryRefusal, _safe_text

FOLDER_SELECTION_SCHEMA = "agent_commons.project_folder_selection.v1"
_PICKER_TIMEOUT_SECONDS = 120
_DARWIN_SCRIPTS = {
    purpose: (
        f'try\nreturn POSIX path of (choose folder with prompt "{prompt}")\n'
        'on error number -128\nreturn ""\nend try'
    )
    for purpose, prompt in (
        ("parent", "Choose the parent folder"),
        ("existing", "Choose the project folder"),
    )
}
_LINUX_TITLES = {
    "parent": "Choose the parent folder",
    "existing": "Choose the project folder",
}


def inferred_project_name(path: str | Path) -> str:
    """Return the last path component, bounded the same way the Work UI infers it."""

    trimmed = str(path).rstrip("/\\")
    name = "".join(list(Path(trimmed).name)[:160])
    return _safe_text(name)


def expand_user_home(path: object, *, home: Path | None = None) -> Path:
    """Expand only the current user's ``~`` / ``~/...`` prefix.

    Other-user forms such as ``~root`` are refused.  ``..`` and absolute
    suffixes after ``~/`` cannot escape the home directory through expansion.
    """

    if type(path) is not str or not path or any(ord(character) < 32 for character in path):
        raise ProjectRegistryRefusal("project_invalid", "Project path is invalid.")
    if any(ord(character) == 127 for character in path):
        raise ProjectRegistryRefusal("project_invalid", "Project path is invalid.")
    root = home if home is not None else Path.home()
    if path == "~" or path == "~/":
        return Path(root)
    if path.startswith("~/"):
        remainder = Path(path[2:])
        if remainder.is_absolute() or remainder.anchor or ".." in remainder.parts:
            raise ProjectRegistryRefusal("project_path_unsafe", "Project path is unavailable.")
        return Path(root) / remainder
    if path.startswith("~"):
        raise ProjectRegistryRefusal("project_path_unsafe", "Project path is unavailable.")
    return Path(path)


class ProjectFolderPicker:
    """Ask macOS or Linux for one existing directory, or fail closed."""

    def __init__(
        self,
        *,
        platform: str | None = None,
        env: Mapping[str, str] | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        which: Callable[[str], str | None] = shutil.which,
        timeout_seconds: float = _PICKER_TIMEOUT_SECONDS,
        home: str | Path | None = None,
    ) -> None:
        self._platform = platform if platform is not None else sys.platform
        self._env = dict(os.environ if env is None else env)
        self._runner = runner
        self._which = which
        self._timeout_seconds = timeout_seconds
        self._home = Path(home) if home is not None else Path.home()

    def available(self) -> bool:
        if self._env.get("CI") or self._env.get("GITHUB_ACTIONS"):
            return False
        if self._env.get("SSH_CONNECTION"):
            return False
        if self._platform == "darwin":
            return self._which("osascript") is not None
        if self._platform == "linux":
            if not (self._env.get("DISPLAY") or self._env.get("WAYLAND_DISPLAY")):
                return False
            return self._linux_binary() is not None
        return False

    def pick(self, purpose: object) -> dict[str, object]:
        if purpose not in ("parent", "existing"):
            raise ProjectRegistryRefusal("project_invalid", "Project request is invalid.")
        command = self._command(purpose)
        if command is None:
            raise ProjectRegistryRefusal(
                "project_picker_unavailable", "Folder picker is unavailable.", 503
            )
        try:
            result = self._runner(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=self._timeout_seconds,
                check=False,
                env=self._env,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ProjectRegistryRefusal(
                "project_picker_unavailable", "Folder picker is unavailable.", 503
            ) from exc
        if result.returncode == 1 and self._platform == "linux":
            return self._cancelled()
        if result.returncode != 0:
            raise ProjectRegistryRefusal(
                "project_picker_unavailable", "Folder picker is unavailable.", 503
            )
        selected = (result.stdout or "").strip()
        if not selected:
            return self._cancelled()
        return self._selected(selected)

    def _command(self, purpose: str) -> list[str] | None:
        if not self.available():
            return None
        if self._platform == "darwin":
            return ["/usr/bin/osascript", "-e", _DARWIN_SCRIPTS[purpose]]
        binary = self._linux_binary()
        if binary is None:
            return None
        name = Path(binary).name
        if name == "zenity":
            return [binary, "--file-selection", "--directory", "--title", _LINUX_TITLES[purpose]]
        if name == "kdialog":
            return [binary, "--getexistingdirectory", str(self._home)]
        return None

    def _linux_binary(self) -> str | None:
        for name in ("zenity", "kdialog"):
            located = self._which(name)
            if located:
                return located
        return None

    @staticmethod
    def _cancelled() -> dict[str, object]:
        return {
            "schema": FOLDER_SELECTION_SCHEMA,
            "status": "cancelled",
            "path": None,
            "name": None,
        }

    @staticmethod
    def _selected(raw: str) -> dict[str, object]:
        if any(ord(character) < 32 or ord(character) == 127 for character in raw):
            raise ProjectRegistryRefusal(
                "project_picker_unavailable", "Folder picker is unavailable.", 503
            )
        normalized = raw if raw == "/" else raw.rstrip("/\\")
        path = Path(normalized)
        if not path.is_absolute():
            raise ProjectRegistryRefusal(
                "project_picker_unavailable", "Folder picker is unavailable.", 503
            )
        return {
            "schema": FOLDER_SELECTION_SCHEMA,
            "status": "selected",
            "path": normalized,
            "name": inferred_project_name(normalized),
        }
