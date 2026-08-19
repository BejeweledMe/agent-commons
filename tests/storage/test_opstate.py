from __future__ import annotations

import stat
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agent_commons.errors import IntegrityError, ValidationError
from agent_commons.storage import opstate
from agent_commons.storage.opstate import (
    ATTEMPT_STORAGE,
    COMMUNICATION_STORAGE,
    SESSION_STORAGE,
    ensure_private_directory,
    exclusive_lock,
    strict_state_bytes,
)

_POLICIES = (SESSION_STORAGE, ATTEMPT_STORAGE, COMMUNICATION_STORAGE)


@pytest.mark.parametrize("policy", _POLICIES)
def test_private_operational_directories_reject_symlinks(tmp_path: Path, policy) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(outside, target_is_directory=True)

    with pytest.raises(IntegrityError, match="must not be a symlink"):
        ensure_private_directory(linked, policy=policy)


@pytest.mark.parametrize("policy", _POLICIES)
def test_private_operational_locks_reject_symlinks(tmp_path: Path, policy) -> None:
    target = tmp_path / "target.lock"
    target.touch()
    linked = tmp_path / "linked.lock"
    linked.symlink_to(target)

    with pytest.raises(IntegrityError, match="lock must not be a symlink"):
        with exclusive_lock(linked, policy=policy):
            raise AssertionError("a symlink lock must never be entered")


def test_all_stores_share_one_resolved_process_lock_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(opstate, "lock_exclusive", lambda _descriptor: None)
    monkeypatch.setattr(opstate, "unlock", lambda _descriptor: None)
    nested = tmp_path / "nested"
    nested.mkdir()
    direct = tmp_path / "shared.lock"
    aliased = nested / ".." / "shared.lock"
    start = threading.Barrier(3)
    guard = threading.Lock()
    active = 0
    peak = 0

    def enter(path: Path, policy) -> None:
        nonlocal active, peak
        start.wait()
        with exclusive_lock(path, policy=policy):
            with guard:
                active += 1
                peak = max(peak, active)
            time.sleep(0.02)
            with guard:
                active -= 1

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(enter, direct, SESSION_STORAGE)
        second = pool.submit(enter, aliased, COMMUNICATION_STORAGE)
        start.wait()
        first.result()
        second.result()

    assert peak == 1
    assert stat.S_IMODE(direct.stat().st_mode) == 0o600


@pytest.mark.parametrize("value", [{"score": float("nan")}, {1: "not a string key"}])
def test_new_operational_state_requires_strict_json(value) -> None:
    with pytest.raises(ValidationError):
        strict_state_bytes(value)
