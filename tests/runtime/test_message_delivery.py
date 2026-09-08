"""Hermetic regressions for private delivery receipt storage."""

import concurrent.futures
import os

import pytest

from agent_commons.errors import IntegrityError, ValidationError
from agent_commons.runtime import message_delivery as delivery
from agent_commons.runtime.message_delivery import MessageDeliveryStore
from agent_commons.storage.opstate import strict_state_bytes

IDS = ("thread.one", "message.one", "delegation.one")
ATTACHMENT = "attachment." + "a" * 32


@pytest.fixture
def store(tmp_path):
    return MessageDeliveryStore(tmp_path / "state", "workspace")


def receipt_path(store):
    return store.root / store._name(store._identity(*IDS))


def test_fetch_ack_retry_and_scope(store):
    with pytest.raises(ValidationError):
        store.record(*IDS, acknowledged=True)
    first = store.record(*IDS)
    assert store.record(*IDS) == first
    image = store.record(*IDS, attachment_id=ATTACHMENT)
    assert image["fetched_at"] == first["fetched_at"]
    ack = store.record(*IDS, acknowledged=True)
    assert store.record(*IDS, acknowledged=True) == ack
    assert MessageDeliveryStore(store.root.parent.parent, "workspace").show(*IDS) == ack
    assert MessageDeliveryStore(store.root.parent.parent, "other").show(*IDS)["fetched_at"] is None
    assert store.show("thread.other", *IDS[1:])["fetched_at"] is None
    assert store.root.stat().st_mode & 0o777 == 0o700
    assert receipt_path(store).stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "patch",
    [
        {"fetched_at": []},
        {"fetched_at": "yesterday"},
        {"fetched_at": "2026-02-30T00:00:00Z"},
        {"fetched_at": "2026-09-08T00:00:00"},
        {"fetched_at": "2026-09-08T00:00:00+03:00"},
        {"fetched_at": None, "acknowledged_at": "2026-09-08T00:00:00Z"},
        {"fetched_at": "2026-09-09T00:00:00Z", "acknowledged_at": "2026-09-08T00:00:00Z"},
        {"acknowledged_at": {"private": "data"}},
        {"attachments": {ATTACHMENT: None}},
        {"attachments": {ATTACHMENT: "bad"}},
        {"attachments": {"../path": "2026-09-08T00:00:00Z"}},
        {"thread_id": "thread.other"},
        {"workspace_id": "other"},
        {"extra": True},
    ],
)
def test_corrupt_receipt_fails_closed(store, patch):
    value = store.record(*IDS)
    value.update(patch)
    receipt_path(store).write_bytes(strict_state_bytes(value))
    with pytest.raises(IntegrityError):
        store.show(*IDS)
    with pytest.raises(IntegrityError):
        store.record(*IDS)


@pytest.mark.parametrize(
    "body", [b"", b"x" * 16385, b'{"schema":0,"schema":1}', b"\xff", b"[" * 1500]
)
def test_malformed_bytes_fail_closed(store, body):
    store.record(*IDS)
    receipt_path(store).write_bytes(body)
    with pytest.raises(IntegrityError):
        store.show(*IDS)


@pytest.mark.parametrize("operation", ["show", "record"])
def test_ancestor_swap_after_construction_fails_closed(store, tmp_path, operation):
    runtime = store.root.parent
    runtime.rename(tmp_path / "original-runtime")
    outside = tmp_path / "outside"
    (outside / "message-delivery").mkdir(parents=True)
    runtime.symlink_to(outside, target_is_directory=True)
    with pytest.raises(IntegrityError):
        getattr(store, operation)(*IDS)
    assert list((outside / "message-delivery").iterdir()) == []


def test_existing_ancestor_symlink_rejected(tmp_path):
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "state").symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    with pytest.raises(IntegrityError):
        MessageDeliveryStore(tmp_path / "state", "workspace")
    assert list((tmp_path / "elsewhere").iterdir()) == []


def test_replaced_root_identity_rejected(store, tmp_path):
    store.root.rename(tmp_path / "old-root")
    store.root.mkdir(mode=0o700)
    with pytest.raises(IntegrityError):
        store.record(*IDS)
    assert list(store.root.iterdir()) == []


@pytest.mark.parametrize("target", ["receipt", "lock"])
@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_unsafe_files_rejected_without_touching_target(store, tmp_path, target, kind):
    path = receipt_path(store) if target == "receipt" else store.root / "receipts.lock"
    outside = tmp_path / "outside"
    outside.write_bytes(b"untouched")
    if kind == "symlink":
        path.symlink_to(outside)
    elif kind == "hardlink":
        os.link(outside, path)
    else:
        os.mkfifo(path)
    with pytest.raises(IntegrityError):
        store.record(*IDS)
    assert outside.read_bytes() == b"untouched"


def test_public_receipt_rejected(store):
    store.record(*IDS)
    receipt_path(store).chmod(0o644)
    with pytest.raises(IntegrityError):
        store.show(*IDS)


def test_public_root_rejected(store):
    store.root.chmod(0o755)
    with pytest.raises(IntegrityError):
        store.show(*IDS)


def test_concurrent_store_instances_preserve_all_receipts(store):
    root = store.root.parent.parent

    def record(number):
        instance = MessageDeliveryStore(root, "workspace")
        instance.record(*IDS, attachment_id="attachment." + f"{number:032x}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        list(executor.map(record, range(20)))
    assert len(store.show(*IDS)["attachments"]) == 20
    with pytest.raises(ValidationError):
        store.record(*IDS, attachment_id="attachment." + "f" * 32)
    store.record(*IDS, attachment_id="attachment." + "0" * 32)


def test_rename_during_publication_never_redirects_write(store, tmp_path, monkeypatch):
    real_replace = delivery.os.replace
    moved = tmp_path / "original-runtime"
    outside = tmp_path / "outside"
    (outside / "message-delivery").mkdir(parents=True)

    def replace(source, destination, **kwargs):
        store.root.parent.rename(moved)
        store.root.parent.symlink_to(outside, target_is_directory=True)
        return real_replace(source, destination, **kwargs)

    monkeypatch.setattr(delivery.os, "replace", replace)
    store.record(*IDS)
    assert (moved / "message-delivery" / receipt_path(store).name).exists()
    assert list((outside / "message-delivery").iterdir()) == []
    with pytest.raises(IntegrityError):
        store.show(*IDS)


def test_failure_before_publication_preserves_receipt(store, monkeypatch):
    before = store.record(*IDS)

    def fail(*args, **kwargs):
        raise OSError("synthetic publication failure")

    monkeypatch.setattr(delivery.os, "replace", fail)
    with pytest.raises(IntegrityError):
        store.record(*IDS, acknowledged=True)
    assert store.show(*IDS) == before
    assert not list(store.root.glob(".receipt-*"))


def test_failure_after_publication_can_retry(store, monkeypatch):
    store.record(*IDS)
    real_replace = delivery.os.replace

    def publish_then_fail(*args, **kwargs):
        real_replace(*args, **kwargs)
        raise OSError("synthetic response loss")

    monkeypatch.setattr(delivery.os, "replace", publish_then_fail)
    with pytest.raises(IntegrityError):
        store.record(*IDS, acknowledged=True)
    recorded = store.show(*IDS)
    assert recorded["acknowledged_at"] is not None
    monkeypatch.setattr(delivery.os, "replace", real_replace)
    assert store.record(*IDS, acknowledged=True) == recorded


@pytest.mark.parametrize(
    "kwargs",
    [
        {"attachment_id": "../bad"},
        {"attachment_id": ""},
        {"acknowledged": 1},
        {"acknowledged": True, "attachment_id": ATTACHMENT},
    ],
)
def test_invalid_operations_rejected(store, kwargs):
    with pytest.raises(ValidationError):
        store.record(*IDS, **kwargs)


def test_processes_preserve_all_updates(store):
    import subprocess
    import sys

    program = """import sys
from pathlib import Path
from agent_commons.runtime.message_delivery import MessageDeliveryStore
store = MessageDeliveryStore(Path(sys.argv[1]), 'workspace')
for number in range(int(sys.argv[2]) * 5, int(sys.argv[2]) * 5 + 5):
    store.record('thread.one', 'message.one', 'delegation.one',
                 attachment_id='attachment.' + f'{number:032x}')
"""
    children = []
    try:
        for number in range(4):
            child = subprocess.Popen(
                [sys.executable, "-", str(store.root.parent.parent), str(number)],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            children.append(child)
            child.stdin.write(program.encode())
            child.stdin.close()
            child.stdin = None
        for child in children:
            _, error = child.communicate(timeout=20)
            assert child.returncode == 0, error.decode()
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait()
    assert len(store.show(*IDS)["attachments"]) == 20
