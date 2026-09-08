from __future__ import annotations

import hashlib
import io
import os
import struct
import threading
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from agent_commons.errors import IntegrityError, LifecycleConflictError, ValidationError
from agent_commons.runtime.message_attachments import (
    MAX_ATTACHMENT_BYTES,
    UNBOUND_TTL_SECONDS,
    BindingReceipt,
    MessageAttachmentStore,
)


def png(width: int = 2, height: int = 1) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00" + b"\x00\x7f\xff" * 2))
        + chunk(b"IEND", b"")
    )


def jpeg() -> bytes:
    from PIL import Image

    output = io.BytesIO()
    Image.new("RGB", (3, 2), (20, 40, 60)).save(output, format="JPEG")
    return output.getvalue()


@pytest.fixture
def store(tmp_path: Path) -> MessageAttachmentStore:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    return MessageAttachmentStore(
        tmp_path / "private", workspace_root=workspace, workspace_id="workspace.test"
    )


def reserve(store: MessageAttachmentStore):
    return store.reserve_draft(thread_id="thread.test", operator_id="operator.test")


def upload(store: MessageAttachmentStore, draft, data: bytes = b"bounded synthetic file", **kwargs):
    return store.upload(
        draft.draft_id,
        thread_id="thread.test",
        operator_id="operator.test",
        filename=kwargs.pop("filename", "notes.txt"),
        declared_media_type=kwargs.pop("declared_media_type", "text/plain"),
        chunks=kwargs.pop("chunks", [data]),
        **kwargs,
    )


def view(store: MessageAttachmentStore, draft):
    return store.show_draft(draft.draft_id, thread_id="thread.test", operator_id="operator.test")


def read(store: MessageAttachmentStore, draft, item):
    return store.read(
        draft.draft_id, item.attachment_id, thread_id="thread.test", operator_id="operator.test"
    )


def intent(store: MessageAttachmentStore, draft):
    return store.begin_binding(
        draft.draft_id,
        thread_id="thread.test",
        operator_id="operator.test",
        operation_id="operation.test",
    )


def receipt(draft, *items):
    return BindingReceipt(
        "workspace.test",
        "thread.test",
        "operator.test",
        "operation.test",
        "message.test",
        tuple(item.attachment_id for item in items),
    )


def test_private_storage_and_immutable_descriptors(store, tmp_path):
    draft = reserve(store)
    item = upload(store, draft, b"hello", chunks=[b"he", b"llo"])
    assert read(store, draft, item) == (item, b"hello")
    assert item.sha256 == hashlib.sha256(b"hello").hexdigest()
    assert item.size_bytes == 5
    assert item.kind == "file"
    assert item.width is None
    assert item.filename == "notes.txt"
    assert not list((tmp_path / "repo").iterdir())
    assert not any(key in item.as_dict() for key in ("path", "content", "operator_id"))
    for path in store.root.rglob("*"):
        assert path.stat().st_mode & 0o077 == 0
    other = upload(store, draft, b"hello")
    assert other.sha256 == item.sha256 and other.attachment_id != item.attachment_id


@pytest.mark.parametrize(
    "data,media,size", [(png(), "image/png", (2, 1)), (jpeg(), "image/jpeg", (3, 2))]
)
def test_image_type_and_dimensions_come_from_bytes(store, data, media, size):
    draft = reserve(store)
    item = upload(
        store, draft, data, filename="image.bin", declared_media_type="application/octet-stream"
    )
    assert item.kind == "image" and item.media_type == media
    assert (item.width, item.height) == size


@pytest.mark.parametrize(
    "data,media,name",
    [
        (png()[:-1], "image/png", "image.png"),
        (png()[:30] + b"wrong", "image/png", "image.png"),
        (png(), "image/jpeg", "image.jpg"),
        (b"not image", "image/png", "image.png"),
        (b"not image", "application/octet-stream", "image.png"),
        (jpeg()[:-2], "image/jpeg", "image.jpg"),
        (png(32768, 32768), "image/png", "image.png"),
        (b"<svg/>", "image/svg+xml", "image.svg"),
    ],
)
def test_image_refusal_preserves_prior_ready_file(store, data, media, name):
    draft = reserve(store)
    ready = upload(store, draft)
    with pytest.raises(ValidationError):
        upload(store, draft, data, filename=name, declared_media_type=media)
    assert view(store, draft).attachments == (ready,)
    assert read(store, draft, ready)[1] == b"bounded synthetic file"
    assert not any(
        path.name.startswith(".upload-") for path in (store.root / draft.draft_id).iterdir()
    )


@pytest.mark.parametrize(
    "name",
    [
        "../secret",
        "/tmp/secret",
        "a\\b",
        ".",
        "..",
        "a\nb",
        "a\x00b",
        "a:b",
        "name\u202etxt",
        "a" * 181,
    ],
)
def test_client_paths_and_unsafe_filenames_never_reach_storage(store, name):
    draft = reserve(store)
    with pytest.raises(ValidationError):
        upload(store, draft, filename=name)
    assert view(store, draft).attachments == ()


def test_stream_byte_limit_is_exact_and_failure_does_not_reset_draft(store):
    draft = reserve(store)
    ready = upload(store, draft, chunks=[b"a" * 1_000_000] * 15)
    assert ready.size_bytes == MAX_ATTACHMENT_BYTES
    with pytest.raises(ValidationError, match="15000000"):
        upload(store, draft, chunks=[b"a" * 1_000_000] * 15 + [b"x"])
    assert view(store, draft).attachments == (ready,)


def test_interrupted_stream_only_removes_its_own_partial_upload(store):
    draft = reserve(store)
    ready = upload(store, draft)

    def interrupted():
        yield b"partial"
        raise RuntimeError("synthetic disconnect")

    with pytest.raises(RuntimeError, match="synthetic disconnect"):
        upload(store, draft, chunks=interrupted())
    assert view(store, draft).attachments == (ready,)
    assert {
        path.name
        for path in (store.root / draft.draft_id).iterdir()
        if not path.name.startswith("upload.")
    } == {
        "draft.json",
        ready.attachment_id + ".bin",
    }


def test_independent_image_file_limits_allow_twenty_total(store):
    draft = reserve(store)
    for _ in range(10):
        upload(store, draft)
    with pytest.raises(ValidationError, match="image or file limit"):
        upload(store, draft)
    for _ in range(10):
        upload(store, draft, png(), filename="image.png", declared_media_type="image/png")
    assert len(view(store, draft).attachments) == 20
    with pytest.raises(ValidationError, match="file limit"):
        upload(store, draft)


def test_sniffed_images_cannot_bypass_image_count_with_octet_stream(store):
    draft = reserve(store)
    for _ in range(10):
        upload(
            store,
            draft,
            png(),
            filename="image.bin",
            declared_media_type="application/octet-stream",
        )
    with pytest.raises(ValidationError, match="image or file limit"):
        upload(
            store,
            draft,
            png(),
            filename="another.bin",
            declared_media_type="application/octet-stream",
        )
    upload(store, draft)


def test_concurrent_uploads_cannot_exceed_the_draft_limit(store):
    draft = reserve(store)

    def attempt(_):
        try:
            upload(store, draft)
            return True
        except ValidationError:
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(attempt, range(12))) == 10
    assert len(view(store, draft).attachments) == 10


@pytest.mark.parametrize(
    "thread_id,operator_id", [("thread.other", "operator.test"), ("thread.test", "operator.other")]
)
def test_every_access_checks_thread_and_operator(store, thread_id, operator_id):
    draft = reserve(store)
    item = upload(store, draft)
    calls = [
        lambda: store.show_draft(draft.draft_id, thread_id=thread_id, operator_id=operator_id),
        lambda: store.read(
            draft.draft_id, item.attachment_id, thread_id=thread_id, operator_id=operator_id
        ),
        lambda: store.upload(
            draft.draft_id,
            thread_id=thread_id,
            operator_id=operator_id,
            filename="x",
            declared_media_type="text/plain",
            chunks=[b"x"],
        ),
        lambda: store.begin_binding(
            draft.draft_id,
            thread_id=thread_id,
            operator_id=operator_id,
            operation_id="operation.test",
        ),
        lambda: store.finalize_binding(
            draft.draft_id,
            thread_id=thread_id,
            operator_id=operator_id,
            receipt=receipt(draft, item),
        ),
        lambda: store.reconcile_binding(
            draft.draft_id,
            thread_id=thread_id,
            operator_id=operator_id,
            lookup=lambda _: receipt(draft, item),
        ),
    ]
    for call in calls:
        with pytest.raises(ValidationError, match="unavailable in this scope"):
            call()


def test_workspace_scope_and_external_storage_are_enforced(store, tmp_path):
    draft = reserve(store)
    other = MessageAttachmentStore(
        tmp_path / "private", workspace_root=tmp_path / "repo", workspace_id="workspace.other"
    )
    with pytest.raises(ValidationError):
        view(other, draft)
    with pytest.raises(ValidationError, match="outside the workspace"):
        MessageAttachmentStore(
            tmp_path / "repo" / "state",
            workspace_root=tmp_path / "repo",
            workspace_id="workspace.test",
        )


def test_binding_freezes_set_and_canonical_reconciliation_is_exact(store):
    draft = reserve(store)
    item = upload(store, draft)
    begun = intent(store, draft)
    assert begun.state == "binding"
    assert intent(store, draft) == begun
    with pytest.raises(LifecycleConflictError):
        upload(store, draft)
    with pytest.raises(LifecycleConflictError):
        store.begin_binding(
            draft.draft_id,
            thread_id="thread.test",
            operator_id="operator.test",
            operation_id="operation.other",
        )
    absent = store.reconcile_binding(
        draft.draft_id, thread_id="thread.test", operator_id="operator.test", lookup=lambda _: None
    )
    assert absent == begun
    with pytest.raises(IntegrityError):
        store.finalize_binding(
            draft.draft_id,
            thread_id="thread.test",
            operator_id="operator.test",
            receipt=replace(receipt(draft, item), thread_id="thread.other"),
        )
    assert view(store, draft).state == "binding"
    bound = store.reconcile_binding(
        draft.draft_id,
        thread_id="thread.test",
        operator_id="operator.test",
        lookup=lambda operation: receipt(draft, item) if operation == "operation.test" else None,
    )
    assert bound.state == "bound" and bound.message_id == "message.test"
    assert read(store, draft, item)[1] == b"bounded synthetic file"
    assert (
        store.finalize_binding(
            draft.draft_id,
            thread_id="thread.test",
            operator_id="operator.test",
            receipt=receipt(draft, item),
        )
        == bound
    )
    with pytest.raises(IntegrityError):
        store.finalize_binding(
            draft.draft_id,
            thread_id="thread.test",
            operator_id="operator.test",
            receipt=replace(receipt(draft, item), message_id="message.other"),
        )


def test_expiry_retains_bound_pending_and_uncertain_data(store):
    now = [1000.0]
    store.clock = lambda: now[0]
    ready, pending, bound, uncertain = [reserve(store) for _ in range(4)]
    ready_item, _, bound_item, _ = [
        upload(store, draft) for draft in [ready, pending, bound, uncertain]
    ]
    intent(store, pending)
    intent(store, bound)
    store.finalize_binding(
        bound.draft_id,
        thread_id="thread.test",
        operator_id="operator.test",
        receipt=receipt(bound, bound_item),
    )
    (store.root / uncertain.draft_id / ".upload-interrupted").write_bytes(b"uncertain")
    now[0] += UNBOUND_TTL_SECONDS - 1
    assert store.expire_unbound() == ()
    now[0] += 1
    assert store.expire_unbound() == (ready.draft_id,)
    with pytest.raises(ValidationError):
        read(store, ready, ready_item)
    assert view(store, pending).state == "binding"
    assert view(store, bound).state == "bound"
    assert view(store, uncertain).state == "ready"


def test_upload_commit_uncertainty_preserves_prior_data_and_orphan(store, monkeypatch):
    draft = reserve(store)
    ready = upload(store, draft)

    def fail(*args):
        raise OSError("synthetic metadata failure")

    monkeypatch.setattr(store, "_write_state", fail)
    with pytest.raises(OSError):
        upload(store, draft, b"new data")
    assert read(store, draft, ready)[1] == b"bounded synthetic file"
    assert len(list((store.root / draft.draft_id).glob("*.bin"))) == 2
    store.clock = lambda: 10**12
    assert store.expire_unbound() == ()


def test_storage_symlinks_and_content_tampering_are_rejected(store, tmp_path):
    draft = reserve(store)
    item = upload(store, draft)
    path = store.root / draft.draft_id / (item.attachment_id + ".bin")
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside secret")
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises((IntegrityError, OSError)):
        read(store, draft, item)
    path.unlink()
    path.write_bytes(b"changed")
    with pytest.raises(IntegrityError, match="immutable descriptor"):
        read(store, draft, item)
    state_alias = tmp_path / "state-alias"
    state_alias.symlink_to(tmp_path / "private", target_is_directory=True)
    with pytest.raises(IntegrityError):
        MessageAttachmentStore(
            state_alias, workspace_root=tmp_path / "repo", workspace_id="workspace.test"
        )


def test_pinned_directory_read_does_not_follow_a_swapped_draft_path(store, tmp_path, monkeypatch):
    draft = reserve(store)
    item = upload(store, draft)
    original = store.root / draft.draft_id
    relocated = store.root / "relocated"
    outside = tmp_path / "outside-dir"
    outside.mkdir()
    (outside / (item.attachment_id + ".bin")).write_bytes(b"outside secret")
    real_open = os.open
    swapped = []

    def racing_open(path, flags, mode=0o777, *, dir_fd=None):
        if path == item.attachment_id + ".bin" and not swapped:
            original.rename(relocated)
            original.symlink_to(outside, target_is_directory=True)
            swapped.append(True)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", racing_open)
    assert read(store, draft, item)[1] == b"bounded synthetic file"
    assert swapped == [True]
    assert (outside / (item.attachment_id + ".bin")).read_bytes() == b"outside secret"


@pytest.mark.parametrize(
    "changed",
    [
        {"workspace_id": "workspace.other"},
        {"operator_id": "operator.other"},
        {"operation_id": "operation.other"},
        {"attachment_ids": ()},
        {"attachment_ids": ("attachment." + "0" * 32,)},
    ],
)
def test_canonical_receipt_mismatch_never_promotes_or_unprotects_intent(store, changed):
    draft = reserve(store)
    item = upload(store, draft)
    intent(store, draft)
    with pytest.raises(IntegrityError):
        store.finalize_binding(
            draft.draft_id,
            thread_id="thread.test",
            operator_id="operator.test",
            receipt=replace(receipt(draft, item), **changed),
        )
    store.clock = lambda: 10**12
    assert view(store, draft).state == "binding"
    assert store.expire_unbound() == ()
    assert read(store, draft, item)[1] == b"bounded synthetic file"


def test_failed_canonical_lookup_preserves_intent_and_content(store):
    draft = reserve(store)
    item = upload(store, draft)
    intent(store, draft)

    def unavailable(_):
        raise RuntimeError("synthetic canonical lookup unavailable")

    with pytest.raises(RuntimeError):
        store.reconcile_binding(
            draft.draft_id, thread_id="thread.test", operator_id="operator.test", lookup=unavailable
        )
    assert view(store, draft).state == "binding"
    assert read(store, draft, item)[1] == b"bounded synthetic file"


def test_empty_or_expired_draft_cannot_be_bound(store):
    draft = reserve(store)
    with pytest.raises(LifecycleConflictError):
        intent(store, draft)
    upload(store, draft)
    store.clock = lambda: 10**12
    with pytest.raises(LifecycleConflictError):
        intent(store, draft)


def test_hardlinked_content_is_not_read_or_expired(store, tmp_path):
    draft = reserve(store)
    item = upload(store, draft)
    content = store.root / draft.draft_id / (item.attachment_id + ".bin")
    os.link(content, tmp_path / "outside-hardlink")
    with pytest.raises(IntegrityError, match="private regular file"):
        read(store, draft, item)
    store.clock = lambda: 10**12
    assert store.expire_unbound() == ()


def bound(store, draft, item):
    intent(store, draft)
    return store.finalize_binding(
        draft.draft_id,
        thread_id="thread.test",
        operator_id="operator.test",
        receipt=receipt(draft, item),
    )


def bound_read(store, item, **scope):
    return store.read_bound(
        thread_id=scope.get("thread_id", "thread.test"),
        message_id=scope.get("message_id", "message.test"),
        attachment_id=scope.get("attachment_id", item.attachment_id),
    )


def remove(store, draft, item, **scope):
    return store.remove_attachment(
        draft.draft_id,
        item.attachment_id,
        thread_id=scope.get("thread_id", "thread.test"),
        operator_id=scope.get("operator_id", "operator.test"),
    )


def test_bound_recipient_read_uses_no_operator_or_draft_input_or_directory_scan(store, monkeypatch):
    draft = reserve(store)
    item = upload(store, draft)
    bound(store, draft, item)

    def no_scan(*args, **kwargs):
        pytest.fail("bound reads must not enumerate private drafts")

    monkeypatch.setattr(os, "listdir", no_scan)
    monkeypatch.setattr(os, "scandir", no_scan)
    assert bound_read(store, item) == (item, b"bounded synthetic file")
    assert not any(key in item.as_dict() for key in ("operator_id", "draft_id", "path"))


@pytest.mark.parametrize(
    "scope",
    [
        {"thread_id": "thread.other"},
        {"message_id": "message.other"},
        {"attachment_id": "attachment." + "0" * 32},
    ],
)
def test_bound_read_rejects_other_thread_message_and_attachment_without_disclosure(store, scope):
    from agent_commons.runtime.message_attachments import MessageAttachmentUnavailable

    draft = reserve(store)
    item = upload(store, draft)
    bound(store, draft, item)
    with pytest.raises(MessageAttachmentUnavailable) as caught:
        bound_read(store, item, **scope)
    assert caught.value.code == "message_attachment_unavailable"
    assert str(caught.value) == "attachment is unavailable for this message"
    assert draft.draft_id not in str(caught.value)


def test_bound_read_rejects_wrong_workspace(store, tmp_path):
    from agent_commons.runtime.message_attachments import MessageAttachmentUnavailable

    draft = reserve(store)
    item = upload(store, draft)
    bound(store, draft, item)
    other = MessageAttachmentStore(
        tmp_path / "private", workspace_root=tmp_path / "repo", workspace_id="workspace.other"
    )
    with pytest.raises(MessageAttachmentUnavailable):
        bound_read(other, item)


def test_binding_intent_never_becomes_readable_without_trusted_finalization(store):
    from agent_commons.runtime.message_attachments import MessageAttachmentUnavailable

    draft = reserve(store)
    item = upload(store, draft)
    intent(store, draft)
    with pytest.raises(MessageAttachmentUnavailable):
        bound_read(store, item)
    assert view(store, draft).state == "binding"
    store.reconcile_binding(
        draft.draft_id, thread_id="thread.test", operator_id="operator.test", lookup=lambda _: None
    )
    with pytest.raises(MessageAttachmentUnavailable):
        bound_read(store, item)
    store.reconcile_binding(
        draft.draft_id,
        thread_id="thread.test",
        operator_id="operator.test",
        lookup=lambda _: receipt(draft, item),
    )
    assert bound_read(store, item)[1] == b"bounded synthetic file"


def test_index_publication_crash_requires_trusted_reconciliation(store, monkeypatch):
    from agent_commons.runtime.message_attachments import MessageAttachmentUnavailable

    draft = reserve(store)
    item = upload(store, draft)
    publish = store._publish_bound_indexes

    def fail(*args):
        raise OSError("synthetic index publication failure")

    monkeypatch.setattr(store, "_publish_bound_indexes", fail)
    with pytest.raises(OSError):
        bound(store, draft, item)
    assert view(store, draft).state == "bound"
    with pytest.raises(MessageAttachmentUnavailable):
        bound_read(store, item)
    monkeypatch.setattr(store, "_publish_bound_indexes", publish)
    store.reconcile_binding(
        draft.draft_id,
        thread_id="thread.test",
        operator_id="operator.test",
        lookup=lambda _: receipt(draft, item),
    )
    assert bound_read(store, item)[1] == b"bounded synthetic file"


@pytest.mark.parametrize("mutation", ["symlink", "tamper", "oversize"])
def test_bound_index_is_bounded_nofollow_and_scope_checked(store, tmp_path, mutation):
    from agent_commons.runtime.message_attachments import MessageAttachmentUnavailable

    draft = reserve(store)
    item = upload(store, draft)
    bound(store, draft, item)
    index = next(store.root.glob("bound.*.json"))
    original = index.read_bytes()
    if mutation == "symlink":
        outside = tmp_path / "outside-binding"
        outside.write_bytes(original)
        index.unlink()
        index.symlink_to(outside)
    elif mutation == "tamper":
        index.write_bytes(original.replace(b"thread.test", b"thread.other"))
    else:
        index.write_bytes(b"x" * 4097)
    with pytest.raises(MessageAttachmentUnavailable):
        bound_read(store, item)


@pytest.mark.parametrize("mutation", ["symlink", "tamper"])
def test_bound_content_is_verified_without_following_symlinks(store, tmp_path, mutation):
    from agent_commons.runtime.message_attachments import MessageAttachmentUnavailable

    draft = reserve(store)
    item = upload(store, draft)
    bound(store, draft, item)
    content = store.root / draft.draft_id / (item.attachment_id + ".bin")
    if mutation == "symlink":
        outside = tmp_path / "outside-content"
        outside.write_bytes(b"bounded synthetic file")
        content.unlink()
        content.symlink_to(outside)
    else:
        content.write_bytes(b"tampered")
    with pytest.raises(MessageAttachmentUnavailable):
        bound_read(store, item)


def test_bound_lookup_rechecks_authoritative_draft_instead_of_trusting_index(store):
    from agent_commons.core.canonical import loads_json_strict
    from agent_commons.runtime.message_attachments import MessageAttachmentUnavailable
    from agent_commons.storage.opstate import strict_state_bytes

    draft = reserve(store)
    item = upload(store, draft)
    bound(store, draft, item)
    path = store.root / draft.draft_id / "draft.json"
    value = loads_json_strict(path.read_bytes())
    value["thread_id"] = "thread.other"
    path.write_bytes(strict_state_bytes(value))
    with pytest.raises(MessageAttachmentUnavailable):
        bound_read(store, item)


def test_remove_ready_attachment_preserves_others_and_is_idempotent(store):
    draft = reserve(store)
    removed = upload(store, draft)
    kept = upload(store, draft, b"keep this")
    result = remove(store, draft, removed)
    assert result.attachments == (kept,)
    assert result.state == "ready"
    assert not (store.root / draft.draft_id / (removed.attachment_id + ".bin")).exists()
    assert read(store, draft, kept) == (kept, b"keep this")
    assert remove(store, draft, removed) == result
    with pytest.raises(ValidationError):
        read(store, draft, removed)


@pytest.mark.parametrize("state", ["binding", "bound"])
def test_remove_cannot_change_a_binding_or_bound_draft(store, state):
    draft = reserve(store)
    item = upload(store, draft)
    if state == "bound":
        bound(store, draft, item)
    else:
        intent(store, draft)
    with pytest.raises(LifecycleConflictError):
        remove(store, draft, item)
    assert read(store, draft, item)[1] == b"bounded synthetic file"


@pytest.mark.parametrize(
    "scope", [{"thread_id": "thread.other"}, {"operator_id": "operator.other"}]
)
def test_remove_checks_authorized_draft_scope(store, scope):
    draft = reserve(store)
    item = upload(store, draft)
    with pytest.raises(ValidationError):
        remove(store, draft, item, **scope)
    assert view(store, draft).attachments == (item,)


@pytest.mark.parametrize("commit_before_error", [False, True])
def test_uncertain_remove_metadata_commit_never_deletes_bytes(
    store, monkeypatch, commit_before_error
):
    draft = reserve(store)
    item = upload(store, draft)
    kept = upload(store, draft, b"kept")
    write_state = store._write_state

    def fail(folder, value):
        if commit_before_error:
            write_state(folder, value)
        raise OSError("synthetic metadata acknowledgement failure")

    monkeypatch.setattr(store, "_write_state", fail)
    with pytest.raises(OSError):
        remove(store, draft, item)
    assert (store.root / draft.draft_id / (item.attachment_id + ".bin")).exists()
    expected = (kept,) if commit_before_error else (item, kept)
    assert view(store, draft).attachments == expected
    assert read(store, draft, kept)[1] == b"kept"


def test_remove_cleanup_failure_retains_orphan_without_corrupting_ready_metadata(
    store, monkeypatch
):
    draft = reserve(store)
    item = upload(store, draft)
    kept = upload(store, draft, b"kept")
    unlink = os.unlink

    def fail(name, *, dir_fd=None):
        if name == item.attachment_id + ".bin":
            raise OSError("synthetic deletion failure")
        return unlink(name, dir_fd=dir_fd)

    monkeypatch.setattr(os, "unlink", fail)
    assert remove(store, draft, item).attachments == (kept,)
    assert (store.root / draft.draft_id / (item.attachment_id + ".bin")).exists()
    store.clock = lambda: 10**12
    assert store.expire_unbound() == ()
    assert read(store, draft, kept)[1] == b"kept"


@pytest.mark.parametrize("mutation", ["symlink", "tamper"])
def test_remove_rejects_unsafe_content_before_metadata_mutation(store, tmp_path, mutation):
    draft = reserve(store)
    item = upload(store, draft)
    content = store.root / draft.draft_id / (item.attachment_id + ".bin")
    if mutation == "symlink":
        outside = tmp_path / "outside-remove"
        outside.write_bytes(b"bounded synthetic file")
        content.unlink()
        content.symlink_to(outside)
    else:
        content.write_bytes(b"tampered")
    with pytest.raises((IntegrityError, OSError)):
        remove(store, draft, item)
    assert view(store, draft).attachments == (item,)


@pytest.mark.parametrize("chunks", [[], [b""], [b"", b""]])
def test_empty_upload_is_rejected_without_affecting_other_selections(store, chunks):
    draft = reserve(store)
    kept = upload(store, draft)
    with pytest.raises(ValidationError, match="at least one byte"):
        upload(store, draft, chunks=chunks)
    assert view(store, draft).attachments == (kept,)
    assert read(store, draft, kept)[1] == b"bounded synthetic file"


def test_empty_stored_metadata_is_rejected_before_canonical_binding(store):
    from agent_commons.core.canonical import loads_json_strict
    from agent_commons.storage.opstate import strict_state_bytes

    draft = reserve(store)
    item = upload(store, draft)
    path = store.root / draft.draft_id / "draft.json"
    value = loads_json_strict(path.read_bytes())
    value["attachments"][0]["size_bytes"] = 0
    path.write_bytes(strict_state_bytes(value))
    with pytest.raises(IntegrityError):
        intent(store, draft)
    assert (store.root / draft.draft_id / (item.attachment_id + ".bin")).exists()


def test_crc_valid_png_with_invalid_compressed_pixels_is_rejected(store):
    draft = reserve(store)
    data = b"not a zlib stream"
    idat = (
        struct.pack(">I", len(data))
        + b"IDAT"
        + data
        + struct.pack(">I", zlib.crc32(b"IDAT" + data))
    )
    invalid_png = png()[:33] + idat + png()[-12:]
    with pytest.raises(ValidationError, match="fully decoded"):
        upload(store, draft, invalid_png, filename="image.png", declared_media_type="image/png")
    assert view(store, draft).attachments == ()


def test_structurally_bounded_but_undecodable_jpeg_is_rejected(store):
    draft = reserve(store)
    invalid_jpeg = (
        b"\xff\xd8\xff\xc0\x00\x0b\x08\x00\x02\x00\x03\x01\x01\x11\x00"
        b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00\x01\xff\xd9"
    )
    with pytest.raises(ValidationError, match="fully decoded"):
        upload(store, draft, invalid_jpeg, filename="image.jpg", declared_media_type="image/jpeg")


def test_image_decoder_missing_or_loosened_configuration_fails_closed(store, monkeypatch):
    import sys

    from PIL import ImageFile

    from agent_commons.errors import ConfigurationError

    draft = reserve(store)
    monkeypatch.setattr(ImageFile, "LOAD_TRUNCATED_IMAGES", True)
    with pytest.raises(ConfigurationError, match="strict truncation"):
        upload(store, draft, png(), filename="image.png", declared_media_type="image/png")
    monkeypatch.setattr(ImageFile, "LOAD_TRUNCATED_IMAGES", False)
    monkeypatch.setitem(sys.modules, "PIL", None)
    with pytest.raises(ConfigurationError, match="requires Pillow"):
        upload(store, draft, png(), filename="image.png", declared_media_type="image/png")


def test_oversized_dimensions_are_rejected_before_pixel_decoder_load(store, monkeypatch):
    from PIL import Image

    draft = reserve(store)

    def no_load(*args, **kwargs):
        pytest.fail("oversized image must not allocate decoded pixels")

    monkeypatch.setattr(Image.Image, "load", no_load)
    with pytest.raises(ValidationError, match="dimensions"):
        upload(
            store, draft, png(32768, 32768), filename="image.png", declared_media_type="image/png"
        )


def retry_upload(store, draft, data=b"retry bytes", operation_id="upload.operation", **kwargs):
    return upload(store, draft, data, operation_id=operation_id, **kwargs)


def upload_receipt(store, draft, operation_id="upload.operation"):
    from agent_commons.core.canonical import loads_json_strict

    return loads_json_strict(
        (store.root / draft.draft_id / store._upload_name(operation_id)).read_bytes()
    )


def test_upload_operation_reuses_exact_descriptor_and_does_not_duplicate_content(store):
    draft = reserve(store)
    first = retry_upload(store, draft)
    assert retry_upload(store, draft, chunks=[b"retry ", b"bytes"]) == first
    assert view(store, draft).attachments == (first,)
    assert len(list((store.root / draft.draft_id).glob("*.bin"))) == 1
    assert len(list((store.root / draft.draft_id).glob("upload.*.json"))) == 1
    assert upload_receipt(store, draft)["descriptor"] == first.as_dict()


@pytest.mark.parametrize(
    "changed", [{"filename": "other.txt"}, {"declared_media_type": "application/pdf"}]
)
def test_same_upload_operation_rejects_changed_intent_without_receiving_body(store, changed):
    from agent_commons.errors import IdempotencyConflictError

    draft = reserve(store)
    first = retry_upload(store, draft)

    def no_body():
        pytest.fail("known intent conflict must be refused before receiving body")
        yield b""

    with pytest.raises(IdempotencyConflictError, match="different intent"):
        retry_upload(store, draft, chunks=no_body(), **changed)
    assert view(store, draft).attachments == (first,)


def test_same_upload_operation_rejects_changed_bytes_after_receipt(store):
    from agent_commons.errors import IdempotencyConflictError

    draft = reserve(store)
    first = retry_upload(store, draft)
    with pytest.raises(IdempotencyConflictError, match="different content"):
        retry_upload(store, draft, b"different")
    assert read(store, draft, first)[1] == b"retry bytes"


def test_interrupted_body_keeps_retry_identity_without_adopting_partial_bytes(store):
    draft = reserve(store)

    def interrupted():
        yield b"incomplete"
        raise RuntimeError("synthetic receive interruption")

    with pytest.raises(RuntimeError):
        retry_upload(store, draft, chunks=interrupted())
    pending = upload_receipt(store, draft)
    assert pending["descriptor"] is None
    assert not list((store.root / draft.draft_id).glob("*.bin"))
    item = retry_upload(store, draft, b"complete body")
    assert item.attachment_id == pending["attachment_id"]
    assert read(store, draft, item)[1] == b"complete body"


@pytest.mark.parametrize("different", [False, True])
def test_concurrent_same_operation_has_one_exact_winner(store, different):
    from agent_commons.errors import IdempotencyConflictError

    draft = reserve(store)
    barrier = threading.Barrier(2, timeout=5)

    def attempt(data):
        def chunks():
            barrier.wait()
            yield data

        try:
            return retry_upload(store, draft, chunks=chunks())
        except IdempotencyConflictError:
            return None

    bodies = [b"one", b"two" if different else b"one"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, bodies))
    successful = [item for item in outcomes if item is not None]
    assert len(successful) == (1 if different else 2)
    assert all(item == successful[0] for item in successful)
    assert view(store, draft).attachments == (successful[0],)


def test_slow_receive_does_not_block_other_uploads_or_draft_reads(store):
    draft = reserve(store)
    started, release = threading.Event(), threading.Event()

    def slow():
        started.set()
        assert release.wait(5)
        yield b"slow body"

    with ThreadPoolExecutor(max_workers=3) as pool:
        waiting = pool.submit(retry_upload, store, draft, chunks=slow())
        assert started.wait(2)
        try:
            other = pool.submit(retry_upload, store, draft, b"fast body", "fast.operation").result(
                timeout=2
            )
            assert pool.submit(view, store, draft).result(timeout=2).attachments == (other,)
        finally:
            release.set()
        slow_item = waiting.result(timeout=2)
    assert len(view(store, draft).attachments) == 2
    assert read(store, draft, slow_item)[1] == b"slow body"


def test_image_decode_runs_outside_workspace_lock(store, monkeypatch):
    import agent_commons.runtime.message_attachments as module

    draft = reserve(store)
    started, release = threading.Event(), threading.Event()
    decoder = module._verify_image_decode

    def blocked_decode(data, detected):
        started.set()
        assert release.wait(5)
        decoder(data, detected)

    monkeypatch.setattr(module, "_verify_image_decode", blocked_decode)
    with ThreadPoolExecutor(max_workers=2) as pool:
        waiting = pool.submit(
            retry_upload, store, draft, png(), filename="image.png", declared_media_type="image/png"
        )
        assert started.wait(2)
        try:
            assert pool.submit(view, store, draft).result(timeout=2).attachments == ()
        finally:
            release.set()
        assert waiting.result(timeout=2).kind == "image"


def test_publication_rechecks_draft_freeze_after_slow_receive(store):
    draft = reserve(store)
    kept = upload(store, draft)
    started, release = threading.Event(), threading.Event()

    def slow():
        started.set()
        assert release.wait(5)
        yield b"late bytes"

    with ThreadPoolExecutor(max_workers=2) as pool:
        waiting = pool.submit(retry_upload, store, draft, chunks=slow())
        assert started.wait(2)
        try:
            begun = pool.submit(intent, store, draft).result(timeout=2)
            assert begun.attachments == (kept,)
        finally:
            release.set()
        with pytest.raises(LifecycleConflictError):
            waiting.result(timeout=2)
    assert view(store, draft).attachments == (kept,)
    assert not list((store.root / draft.draft_id).glob(".upload-*"))


def test_discard_tombstone_prevents_slow_or_later_retry_resurrection(store):
    draft = reserve(store)
    item = retry_upload(store, draft)
    started, release = threading.Event(), threading.Event()

    def slow_retry():
        started.set()
        assert release.wait(5)
        yield b"retry bytes"

    with ThreadPoolExecutor(max_workers=2) as pool:
        waiting = pool.submit(retry_upload, store, draft, chunks=slow_retry())
        assert started.wait(2)
        try:
            assert pool.submit(remove, store, draft, item).result(timeout=2).attachments == ()
        finally:
            release.set()
        with pytest.raises(LifecycleConflictError, match="cannot be restored"):
            waiting.result(timeout=2)
    with pytest.raises(LifecycleConflictError, match="cannot be restored"):
        retry_upload(store, draft)
    assert view(store, draft).attachments == ()
    assert not list((store.root / draft.draft_id).glob("*.bin"))


@pytest.mark.parametrize("after_commit", [False, True])
def test_upload_retry_recovers_metadata_commit_uncertainty(store, monkeypatch, after_commit):
    draft = reserve(store)
    kept = upload(store, draft)
    writer = store._write_state

    def fail(folder, value):
        if after_commit:
            writer(folder, value)
        raise OSError("synthetic draft commit acknowledgement failure")

    monkeypatch.setattr(store, "_write_state", fail)
    with pytest.raises(OSError):
        retry_upload(store, draft)
    pending = upload_receipt(store, draft)
    assert pending["descriptor"] is not None
    monkeypatch.setattr(store, "_write_state", writer)
    recovered = retry_upload(store, draft)
    assert recovered.attachment_id == pending["attachment_id"]
    assert view(store, draft).attachments == (kept, recovered)
    assert retry_upload(store, draft) == recovered


@pytest.mark.parametrize("after_replace", [False, True])
def test_upload_retry_recovers_atomic_content_publication_failure(
    store, monkeypatch, after_replace
):
    draft = reserve(store)
    replace_file = os.replace

    def fail(source, destination, **kwargs):
        if isinstance(destination, str) and destination.endswith(".bin"):
            if after_replace:
                replace_file(source, destination, **kwargs)
            raise OSError("synthetic content publication failure")
        return replace_file(source, destination, **kwargs)

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        retry_upload(store, draft)
    proof = upload_receipt(store, draft)
    content = store.root / draft.draft_id / (proof["attachment_id"] + ".bin")
    assert content.stat().st_nlink == 1
    assert content.read_bytes() == (b"retry bytes" if after_replace else b"")
    monkeypatch.setattr(os, "replace", replace_file)
    result = retry_upload(store, draft)
    assert result.attachment_id == proof["attachment_id"]
    assert read(store, draft, result)[1] == b"retry bytes"


@pytest.mark.parametrize("after_commit", [False, True])
def test_upload_retry_recovers_receipt_acknowledgement_failure(store, monkeypatch, after_commit):
    draft = reserve(store)
    writer = store._write_private_record

    def fail(folder, name, value):
        if value.get("descriptor") is not None:
            if after_commit:
                writer(folder, name, value)
            raise OSError("synthetic receipt acknowledgement failure")
        writer(folder, name, value)

    monkeypatch.setattr(store, "_write_private_record", fail)
    with pytest.raises(OSError):
        retry_upload(store, draft)
    pending = upload_receipt(store, draft)
    monkeypatch.setattr(store, "_write_private_record", writer)
    result = retry_upload(store, draft)
    assert result.attachment_id == pending["attachment_id"]
    assert read(store, draft, result)[1] == b"retry bytes"


def test_unknown_preexisting_bytes_are_not_adopted_by_a_new_publication_receipt(store):
    draft = reserve(store)

    def injected():
        pending = upload_receipt(store, draft)
        (store.root / draft.draft_id / (pending["attachment_id"] + ".bin")).write_bytes(
            b"retry bytes"
        )
        yield b"retry bytes"

    with pytest.raises(IntegrityError, match="no prior publication receipt"):
        retry_upload(store, draft, chunks=injected())
    assert upload_receipt(store, draft)["descriptor"] is None
    assert view(store, draft).attachments == ()
    store.clock = lambda: 10**12
    assert store.expire_unbound() == ()


def test_receipted_but_tampered_content_cannot_be_recovered(store, monkeypatch):
    draft = reserve(store)
    writer = store._write_state
    monkeypatch.setattr(
        store, "_write_state", lambda *args: (_ for _ in ()).throw(OSError("synthetic failure"))
    )
    with pytest.raises(OSError):
        retry_upload(store, draft)
    pending = upload_receipt(store, draft)
    (store.root / draft.draft_id / (pending["attachment_id"] + ".bin")).write_bytes(
        b"unknown bytes"
    )
    monkeypatch.setattr(store, "_write_state", writer)
    with pytest.raises(IntegrityError, match="publication receipt"):
        retry_upload(store, draft)
    assert view(store, draft).attachments == ()


def test_unfinished_discard_blocks_retry_and_binding_until_explicit_retry(store, monkeypatch):
    draft = reserve(store)
    item = retry_upload(store, draft)
    writer = store._write_state

    def fail(*args):
        raise OSError("synthetic discard metadata failure")

    monkeypatch.setattr(store, "_write_state", fail)
    with pytest.raises(OSError):
        remove(store, draft, item)
    monkeypatch.setattr(store, "_write_state", writer)
    assert view(store, draft).attachments == (item,)
    with pytest.raises(LifecycleConflictError, match="cannot be restored"):
        retry_upload(store, draft)
    with pytest.raises(LifecycleConflictError, match="discard must be reconciled"):
        intent(store, draft)
    assert remove(store, draft, item).attachments == ()


def test_exact_upload_retry_after_message_binding_is_read_only(store):
    draft = reserve(store)
    item = retry_upload(store, draft)
    finalized = bound(store, draft, item)
    assert retry_upload(store, draft) == item
    assert view(store, draft) == finalized
    assert bound_read(store, item)[1] == b"retry bytes"


def test_expiry_accounts_for_completed_receipts_and_discard_tombstones(store):
    draft = reserve(store)
    removed = retry_upload(store, draft)
    kept = retry_upload(store, draft, b"kept", "other.operation")
    remove(store, draft, removed)
    assert view(store, draft).attachments == (kept,)
    store.clock = lambda: 10**12
    assert store.expire_unbound() == (draft.draft_id,)


def test_failed_receiving_receipts_can_expire_without_guessing_unknown_content(store):
    draft = reserve(store)
    with pytest.raises(ValidationError):
        retry_upload(store, draft, b"")
    store.clock = lambda: 10**12
    assert store.expire_unbound() == (draft.draft_id,)


@pytest.mark.parametrize("mutation", ["symlink", "wrong_scope", "bad_descriptor"])
def test_retry_receipt_is_private_scope_checked_and_not_followed(store, tmp_path, mutation):
    from agent_commons.storage.opstate import strict_state_bytes

    draft = reserve(store)
    item = retry_upload(store, draft)
    path = store.root / draft.draft_id / store._upload_name("upload.operation")
    value = upload_receipt(store, draft)
    if mutation == "symlink":
        outside = tmp_path / "receipt-copy"
        outside.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(outside)
    else:
        if mutation == "wrong_scope":
            value["thread_id"] = "thread.other"
        else:
            value["descriptor"]["size_bytes"] = 0
        path.write_bytes(strict_state_bytes(value))
    with pytest.raises((IntegrityError, OSError)):
        retry_upload(store, draft)
    assert read(store, draft, item)[1] == b"retry bytes"


def test_receipt_and_incomplete_upload_counts_are_bounded(store, monkeypatch):
    import agent_commons.runtime.message_attachments as module

    draft = reserve(store)
    monkeypatch.setattr(module, "MAX_UPLOAD_OPERATIONS", 2)
    for operation in ("one.operation", "two.operation"):
        with pytest.raises(ValidationError, match="at least one byte"):
            retry_upload(store, draft, b"", operation)
    with pytest.raises(ValidationError, match="operation limit"):
        retry_upload(store, draft, operation_id="three.operation")
    # An existing operation can still be recovered at the receipt count bound.
    assert retry_upload(store, draft, operation_id="one.operation").size_bytes > 0
    for index in range(module.MAX_ACTIVE_UPLOADS):
        (store.root / draft.draft_id / (".upload-" + f"{index:032x}")).write_bytes(b"uncertain")
    with pytest.raises(ValidationError, match="incomplete uploads"):
        retry_upload(store, draft, operation_id="one.operation")
