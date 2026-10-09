"""Private UI persistence is scoped, bounded and never canonical conversation history."""

from __future__ import annotations

import json
import multiprocessing
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agent_commons.errors import ValidationError
from agent_commons.ui.workspace_preferences import (
    DEFAULT_PREFERENCES,
    WorkspacePreferenceError,
    WorkspacePreferenceStore,
)
from tests.ui.conftest import authorized, tree_digest


def store(tmp_path: Path, principal: str = "owner") -> WorkspacePreferenceStore:
    return WorkspacePreferenceStore(
        tmp_path / "state",
        project_root=tmp_path / "project",
        workspace_id="workspace.test",
        principal=principal,
    )


def layout(**changes):
    return {"expected_revision": 0, **DEFAULT_PREFERENCES, **changes}


def draft(**changes):
    return {
        "expected_revision": 0,
        "scope": {"kind": "project"},
        "text": "Мой черновик",
        "reply_to_message_id": None,
        **changes,
    }


def test_layout_defaults_retry_and_principal_isolation(tmp_path):
    first = store(tmp_path)
    assert first.preferences()["revision"] == 0
    assert not first.root.exists()  # a view creates no directories or messages
    saved = first.save_preferences(layout(panels_swapped=True))
    assert saved["revision"] == 1
    assert first.save_preferences(layout(panels_swapped=True)) == saved
    with pytest.raises(WorkspacePreferenceError, match="workspace_preferences_conflict"):
        first.save_preferences(layout(chat_width=400))
    assert store(tmp_path, "another-owner").preferences()["revision"] == 0
    assert (first.root / "layout.json").stat().st_mode & 0o077 == 0


def test_text_draft_reload_clear_and_conflicting_tab(tmp_path):
    first = store(tmp_path)
    saved = first.save_text_draft(draft())
    assert store(tmp_path).text_draft({"kind": "project"}) == saved
    assert first.save_text_draft(draft()) == saved
    newer = first.save_text_draft(draft(expected_revision=1, text="Newer tab"))
    with pytest.raises(WorkspacePreferenceError, match="conversation_text_draft_conflict"):
        first.save_text_draft(draft(expected_revision=1, text=""))
    assert first.text_draft({"kind": "project"}) == newer
    cleared = first.save_text_draft(draft(expected_revision=2, text=""))
    assert cleared["text"] == "" and cleared["revision"] == 3
    assert store(tmp_path, "another-owner").text_draft({"kind": "project"})["text"] == ""


@pytest.mark.parametrize(
    "patch",
    [
        {"chat_width": 99999},
        {"sidebar_width": True},
        {"panels_swapped": "true"},
        {"extra": "hidden"},
    ],
)
def test_layout_rejects_invalid_closed_shapes(tmp_path, patch):
    with pytest.raises(WorkspacePreferenceError):
        store(tmp_path).save_preferences(layout(**patch))


@pytest.mark.parametrize(
    "patch",
    [
        {"text": "я" * 32_001},
        {"text": "bad\x00value"},
        {"scope": {"kind": "project", "id": "unexpected"}},
        {"reply_to_message_id": "not-message"},
    ],
)
def test_drafts_reject_oversized_or_invalid_metadata(tmp_path, patch):
    with pytest.raises((ValidationError, WorkspacePreferenceError)):
        store(tmp_path).save_text_draft(draft(**patch))


def _writer(args):
    path, width = args
    try:
        return store(Path(path)).save_preferences(layout(chat_width=width))["chat_width"]
    except WorkspacePreferenceError as exc:
        return exc.code


def test_concurrent_threads_cannot_both_replace_revision_zero(tmp_path):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(_writer, [(str(tmp_path), 328), (str(tmp_path), 336)]))
    assert results.count("workspace_preferences_conflict") == 1
    assert store(tmp_path).preferences()["revision"] == 1


def test_concurrent_processes_share_the_cas_lock(tmp_path):
    with multiprocessing.get_context("spawn").Pool(2) as pool:
        results = pool.map(_writer, [(str(tmp_path), 328), (str(tmp_path), 336)])
    assert results.count("workspace_preferences_conflict") == 1


def test_failed_atomic_replace_keeps_previous_document(tmp_path, monkeypatch):
    first = store(tmp_path)
    saved = first.save_text_draft(draft())

    def fail(*args):
        raise OSError("synthetic replace failure")

    monkeypatch.setattr("agent_commons.ui.workspace_preferences.os.replace", fail)
    with pytest.raises(OSError):
        first.save_text_draft(draft(expected_revision=1, text="replacement"))
    assert first.text_draft({"kind": "project"}) == saved
    assert not list(first.root.glob(".pending-*"))


def test_symlink_state_and_document_are_refused(tmp_path):
    first = store(tmp_path)
    first.save_preferences(layout())
    target = tmp_path / "sentinel"
    target.write_text("untouched")
    (first.root / "layout.json").unlink()
    (first.root / "layout.json").symlink_to(target)
    with pytest.raises((OSError, WorkspacePreferenceError)):
        first.save_preferences(layout())
    assert target.read_text() == "untouched"
    redirected = tmp_path / "redirect"
    redirected.symlink_to(tmp_path / "state", target_is_directory=True)
    other = WorkspacePreferenceStore(
        redirected,
        project_root=tmp_path / "project",
        workspace_id="workspace.test",
        principal="owner",
    )
    with pytest.raises(WorkspacePreferenceError):
        other.preferences()


def test_real_authenticated_routes_are_private_not_messages(writable, writable_client):
    before = tree_digest(writable.repo / ".agent-commons")
    read = writable_client.get("/api/workspace/preferences", headers=authorized())
    assert read.status_code == 200
    assert read.headers["cache-control"] == "no-store"
    saved = writable_client.post(
        "/api/workspace/preferences", json=layout(chat_width=400), headers=authorized()
    )
    assert saved.status_code == 200 and saved.json()["chat_width"] == 400
    saved_draft = writable_client.post(
        "/api/conversations/text-draft", json=draft(), headers=authorized()
    )
    assert saved_draft.status_code == 200, saved_draft.text
    fetched = writable_client.get(
        "/api/conversations/text-draft?scope_kind=project", headers=authorized()
    )
    assert fetched.json() == saved_draft.json()
    assert tree_digest(writable.repo / ".agent-commons") == before
    assert writable.manager().snapshot().threads == {}
    unauthenticated = writable_client.get("/api/conversations/text-draft")
    assert unauthenticated.status_code in (401, 403)
    unknown_scope = writable_client.post(
        "/api/conversations/text-draft",
        json=draft(scope={"kind": "task", "id": "task.0" + "1" * 25}),
        headers=authorized(),
    )
    assert unknown_scope.status_code == 409


def test_readonly_does_not_mount_persistence_writes(client):
    for path in ("/api/workspace/preferences", "/api/conversations/text-draft"):
        response = client.post(path, json={}, headers=authorized())
        assert response.status_code == 405


def test_serialized_envelope_limit_cannot_poison_a_draft(writable_client):
    payload = json.dumps(draft(text="\x01" * 16_640), separators=(",", ":"))
    assert len(payload.encode()) < 100_000
    response = writable_client.post(
        "/api/conversations/text-draft",
        content=payload,
        headers={**authorized(), "content-type": "application/json"},
    )
    assert response.status_code == 413
    read = writable_client.get("/api/conversations/text-draft", headers=authorized())
    assert read.status_code == 200 and read.json()["text"] == ""


def test_delete_reclaims_quota_without_resurrecting_old_tab(tmp_path, monkeypatch):
    monkeypatch.setattr("agent_commons.ui.workspace_preferences.MAX_DRAFTS", 2)
    first = store(tmp_path)
    scopes = [{"kind": "task", "id": "task.0" + str(index) * 25} for index in (1, 2, 3)]
    original = draft(scope=scopes[0])
    first.save_text_draft(original)
    first.save_text_draft(draft(scope=scopes[1], expected_revision=1))
    with pytest.raises(WorkspacePreferenceError, match="conversation_text_draft_limit"):
        first.save_text_draft(draft(scope=scopes[2], expected_revision=2))
    first.save_text_draft(draft(scope=scopes[0], expected_revision=1, text=""))
    assert len(list(first.root.glob("draft-*.json"))) == 1
    first.save_text_draft(draft(scope=scopes[2], expected_revision=3))
    with pytest.raises(WorkspacePreferenceError, match="conversation_text_draft_conflict"):
        first.save_text_draft(original)
    assert first.text_draft(scopes[0])["text"] == ""
    assert len(list(first.root.glob("draft-*.json"))) == 2


def _crash_before_publish(path):
    from agent_commons.ui import workspace_preferences

    replace = workspace_preferences.os.replace

    def crash(source, destination):
        if Path(destination).name.startswith("draft-"):
            os._exit(88)
        replace(source, destination)

    workspace_preferences.os.replace = crash
    store(Path(path)).save_text_draft(draft(text="private crash fixture"))


def test_uncatchable_exit_pending_bytes_recovered_on_next_write(tmp_path):
    child = multiprocessing.get_context("spawn").Process(
        target=_crash_before_publish, args=(str(tmp_path),)
    )
    child.start()
    child.join(timeout=20)
    assert not child.is_alive() and child.exitcode == 88
    first = store(tmp_path)
    assert len(list(first.root.glob(".pending-*"))) == 1
    empty = first.text_draft({"kind": "project"})
    assert empty["revision"] == 1 and empty["text"] == ""
    first.save_text_draft(draft(expected_revision=1, text=""))
    assert not list(first.root.glob(".pending-*"))
    assert not list(first.root.glob("draft-*.json"))


def _crash_before_delete(path):
    unlink = Path.unlink

    def crash(self, *args, **kwargs):
        if self.name.startswith("draft-"):
            os._exit(89)
        return unlink(self, *args, **kwargs)

    Path.unlink = crash
    store(Path(path)).save_text_draft(draft(expected_revision=1, text=""))


def test_clear_retry_after_clock_publish_before_unlink(tmp_path):
    first = store(tmp_path)
    saved = first.save_text_draft(draft())
    child = multiprocessing.get_context("spawn").Process(
        target=_crash_before_delete, args=(str(tmp_path),)
    )
    child.start()
    child.join(timeout=20)
    assert not child.is_alive() and child.exitcode == 89
    assert first.text_draft({"kind": "project"}) == saved
    cleared = first.save_text_draft(draft(expected_revision=1, text=""))
    assert cleared["revision"] == 3 and cleared["text"] == ""
    assert first.save_text_draft(draft(expected_revision=1, text="")) == cleared
    with pytest.raises(WorkspacePreferenceError, match="conversation_text_draft_conflict"):
        first.save_text_draft(draft())
