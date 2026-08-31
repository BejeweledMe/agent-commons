from __future__ import annotations

import hashlib
import stat
import threading
import time
from multiprocessing import get_context
from pathlib import Path

import pytest

from agent_commons.errors import ConfigurationError, IntegrityError
from agent_commons.services.delegation_runtime import _delegation_lock


def _hold_lock(
    state_root: str,
    delegation_id: str,
    entered,
    release,
) -> None:
    with _delegation_lock(Path(state_root), delegation_id):
        entered.set()
        release.wait(timeout=10)


def test_delegation_lock_serializes_threads_and_uses_private_files(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    first_entered = threading.Event()
    release_first = threading.Event()
    contender_attempted = threading.Event()
    second_entered = threading.Event()

    def first() -> None:
        with _delegation_lock(state_root, "delegation.same"):
            first_entered.set()
            release_first.wait(timeout=10)

    def second() -> None:
        first_entered.wait(timeout=10)
        contender_attempted.set()
        with _delegation_lock(state_root, "delegation.same"):
            second_entered.set()

    holder = threading.Thread(target=first, daemon=True)
    contender = threading.Thread(target=second, daemon=True)
    holder.start()
    contender.start()
    assert first_entered.wait(timeout=10)
    assert contender_attempted.wait(timeout=10)
    assert not second_entered.wait(timeout=0.1)
    release_first.set()
    holder.join(timeout=10)
    contender.join(timeout=10)

    assert second_entered.is_set()
    lock_root = state_root / "runtime" / "delegation-locks"
    digest = hashlib.sha256(b"delegation.same").hexdigest()
    assert stat.S_IMODE(lock_root.stat().st_mode) == 0o700
    assert stat.S_IMODE((lock_root / f"{digest}.lock").stat().st_mode) == 0o600


def test_delegation_lock_serializes_processes_but_not_other_ids(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    context = get_context("spawn")
    entered = context.Event()
    release = context.Event()
    child = context.Process(
        target=_hold_lock,
        args=(str(state_root), "delegation.same", entered, release),
    )
    child.start()
    assert entered.wait(timeout=10)

    same_entered = threading.Event()
    contender_attempted = threading.Event()

    def contend() -> None:
        contender_attempted.set()
        with _delegation_lock(state_root, "delegation.same"):
            same_entered.set()

    contender = threading.Thread(target=contend, daemon=True)
    contender.start()
    assert contender_attempted.wait(timeout=10)
    assert not same_entered.wait(timeout=0.1)

    started = time.monotonic()
    with _delegation_lock(state_root, "delegation.other"):
        pass
    assert time.monotonic() - started < 1

    release.set()
    child.join(timeout=10)
    contender.join(timeout=10)
    assert child.exitcode == 0
    assert same_entered.is_set()


def test_delegation_lock_preserves_symlink_directory_refusal(tmp_path: Path) -> None:
    runtime_root = tmp_path / "state" / "runtime"
    runtime_root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (runtime_root / "delegation-locks").symlink_to(outside, target_is_directory=True)

    with pytest.raises(
        ConfigurationError,
        match="runtime delegation-lock directory must not be a symlink",
    ):
        with _delegation_lock(tmp_path / "state", "delegation.unsafe"):
            raise AssertionError("a symlink lock directory must never be entered")


def test_delegation_lock_does_not_follow_lock_file_symlinks(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    lock_root = state_root / "runtime" / "delegation-locks"
    lock_root.mkdir(parents=True)
    target = tmp_path / "outside.lock"
    target.touch()
    digest = hashlib.sha256(b"delegation.unsafe").hexdigest()
    (lock_root / f"{digest}.lock").symlink_to(target)

    with pytest.raises(IntegrityError, match="lock must not be a symlink"):
        with _delegation_lock(state_root, "delegation.unsafe"):
            raise AssertionError("a symlink lock file must never be entered")
