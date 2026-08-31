from __future__ import annotations

import os
from multiprocessing import get_context
from pathlib import Path

import pytest

from agent_commons import platform_support
from agent_commons.errors import ConfigurationError


def _try_lock_in_child(path: str, results) -> None:
    descriptor = os.open(Path(path), os.O_RDWR)
    acquired = False
    try:
        acquired = platform_support.try_lock_exclusive(descriptor)
        results.put(acquired)
    finally:
        if acquired:
            platform_support.unlock(descriptor)
        os.close(descriptor)


def test_unsupported_platform_fails_with_action_before_lock_use(monkeypatch) -> None:
    monkeypatch.setattr(platform_support, "_fcntl", None)

    with pytest.raises(ConfigurationError, match="supports macOS and Linux only") as captured:
        platform_support.require_supported_platform()

    assert "supported host or container" in str(captured.value)


def test_nonblocking_lock_reports_contention(tmp_path) -> None:
    path = tmp_path / "runtime.lock"
    first = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    second = os.open(path, os.O_RDWR)
    try:
        assert platform_support.try_lock_exclusive(first) is True
        assert platform_support.try_lock_exclusive(second) is False
    finally:
        platform_support.unlock(first)
        os.close(first)
        os.close(second)


def test_nonblocking_lock_reports_cross_process_contention(tmp_path) -> None:
    path = tmp_path / "runtime.lock"
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    context = get_context("spawn")
    results = context.Queue()
    try:
        assert platform_support.try_lock_exclusive(descriptor) is True
        child = context.Process(target=_try_lock_in_child, args=(str(path), results))
        child.start()
        child.join(timeout=10)
        assert child.exitcode == 0
        assert results.get(timeout=1) is False
    finally:
        platform_support.unlock(descriptor)
        os.close(descriptor)
