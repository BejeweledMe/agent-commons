"""Bounded, advisory project prerequisites. Presence never proves build readiness."""

from __future__ import annotations

import json
import os
import re
import selectors
import signal
import stat
import subprocess
import time
import tomllib
from collections.abc import Callable
from pathlib import Path

from agent_commons.errors import ConfigurationError
from agent_commons.runtime.model import resolve_trusted_executable

_MAX_MANIFEST_BYTES = 256 * 1024
_VERSION = re.compile(r"v?(\d{1,4}(?:\.\d{1,4}){0,2})")
_TOOL_ARGS = {"node": ("node", "--version"), "python": ("python3", "-I", "--version")}
_LOCKS = {
    "node": ("package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock", "bun.lockb"),
    "python": ("uv.lock", "poetry.lock", "Pipfile.lock"),
}


def _file(root_fd: int, name: str, *, read: bool = False) -> tuple[str, bytes | None]:
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root_fd)
    except FileNotFoundError:
        return "missing", None
    except OSError:
        return "unavailable", None
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            return "unavailable", None
        if not read:
            return "present", None
        if before.st_size > _MAX_MANIFEST_BYTES:
            return "unavailable", None
        content = os.read(fd, _MAX_MANIFEST_BYTES + 1)
        after = os.fstat(fd)
        if (
            len(content) != before.st_size
            or len(content) > _MAX_MANIFEST_BYTES
            or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        ):
            return "unavailable", None
        return "present", content
    finally:
        os.close(fd)


def _directory(root_fd: int, name: str) -> str:
    try:
        info = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        return "present" if stat.S_ISDIR(info.st_mode) else "unavailable"
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unavailable"


def _version_probe(name: str, root: Path) -> str | None:
    """Only fixed version arguments, outside the project, without inherited secrets/hooks."""
    args = _TOOL_ARGS[name]
    try:
        executable = resolve_trusted_executable(args[0], workspace_root=root)
    except ConfigurationError:
        return None
    try:
        process = subprocess.Popen(
            (executable, *args[1:]),
            cwd=os.path.abspath(os.sep),
            env={"PATH": os.defpath, "LANG": "C"},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return None
    output = bytearray()
    completed = False
    deadline = time.monotonic() + 2
    try:
        assert process.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    return None
                chunk = os.read(process.stdout.fileno(), 129 - len(output))
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > 128:
                    return None
        if process.wait(timeout=max(0.001, deadline - time.monotonic())) != 0:
            return None
        completed = True
        value = output.decode("ascii").strip()
        if name == "python":
            value = value.removeprefix("Python ")
        match = _VERSION.fullmatch(value)
        return match[1] if match else None
    except (OSError, UnicodeError, subprocess.TimeoutExpired):
        return None
    finally:
        # Reap even a hanging version wrapper or a child holding its pipe open.
        if not completed:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait()
        if process.stdout is not None:
            process.stdout.close()


def read_project_environment(
    root: Path,
    *,
    version_probe: Callable[[str, Path], str | None] = _version_probe,
) -> dict[str, object]:
    result: dict[str, object] = {
        "schema": "agent_commons.project-environment.v1",
        "state": "unknown",
        "build_verified": False,
        "manifests": [],
        "lockfiles": [],
        "dependencies": [],
        "tools": [],
        "limitations": ["presence_only", "root_manifests_only"],
    }
    manifests, locks, dependencies, tools = [], [], [], []
    attention = False
    root = root.resolve()
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for ecosystem, names in (
            ("node", ("package.json",)),
            ("python", ("pyproject.toml", "requirements.txt")),
        ):
            detected = False
            for name in names:
                state, content = _file(root_fd, name, read=True)
                if state == "present":
                    try:
                        if name.endswith(".json") and not isinstance(json.loads(content), dict):
                            raise ValueError("manifest is not an object")
                        if name.endswith(".toml"):
                            tomllib.loads(content.decode("utf-8"))
                    except (ValueError, UnicodeError, RecursionError):
                        state = "invalid"
                detected |= state != "missing"
                manifests.append({"name": name, "state": state})
                attention |= state in {"invalid", "unavailable"}
            if not detected:
                continue
            ecosystem_locks = [
                {"name": name, "state": _file(root_fd, name)[0]} for name in _LOCKS[ecosystem]
            ]
            locks.extend(ecosystem_locks)
            attention |= not any(item["state"] == "present" for item in ecosystem_locks)
            dependency_state = _directory(
                root_fd, "node_modules" if ecosystem == "node" else ".venv"
            )
            dependencies.append({"ecosystem": ecosystem, "state": dependency_state})
            attention |= dependency_state != "present"
            pin_state, pin = _file(
                root_fd, ".node-version" if ecosystem == "node" else ".python-version", read=True
            )
            required = None
            if pin is not None:
                match = _VERSION.fullmatch(pin.decode("ascii", errors="replace").strip())
                required = match[1] if match else None
                attention |= match is None
            attention |= pin_state == "unavailable"
            observed = version_probe(ecosystem, root)
            matches = (
                (observed.split(".")[: len(required.split("."))] == required.split("."))
                if observed and required
                else None
            )
            tools.append(
                {
                    "name": ecosystem,
                    "availability": "available" if observed else "unavailable",
                    "required_version": required,
                    "observed_version": observed,
                    "version_matches": matches,
                }
            )
            attention |= observed is None or matches is False
    finally:
        os.close(root_fd)
    return {
        **result,
        "state": "attention" if attention else ("observed" if tools else "unknown"),
        "manifests": manifests,
        "lockfiles": locks,
        "dependencies": dependencies,
        "tools": tools,
    }
