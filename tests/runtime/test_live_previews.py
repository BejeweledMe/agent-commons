from __future__ import annotations

import json
import stat
from dataclasses import replace
from pathlib import Path

import pytest

from agent_commons.domain.snapshot import ProjectSnapshot
from agent_commons.runtime.live_previews import LivePreviewRefusal, LivePreviewRegistry

TASK = "task." + "1" * 26
AGENT = "agent." + "2" * 26
RUN = "delegation." + "3" * 26
REV = "evt." + "4" * 26
SESSION = "session." + "a" * 32


def bound():  # type: ignore[no-untyped-def]
    snapshot = ProjectSnapshot(workspace_id="workspace-a")
    snapshot.tasks[TASK] = {"id": TASK, "revision": REV}
    snapshot.agents[AGENT] = {"id": AGENT}
    snapshot.delegations[RUN] = {
        "id": RUN,
        "revision": REV,
        "target_ref": {"kind": "task", "id": TASK},
        "target_revision": REV,
        "agent_id": AGENT,
        "child_session_id": SESSION,
    }
    snapshot.entity_revision_actor_session_ids[("task", TASK, REV)] = SESSION
    snapshot.entity_revision_actor_session_ids[("delegation", RUN, REV)] = SESSION
    return snapshot


def payload(**updates):  # type: ignore[no-untyped-def]
    return {
        "idempotency_key": "preview-publish-1",
        "task_id": TASK,
        "task_revision": REV,
        "delegation_id": RUN,
        "delegation_revision": REV,
        "title": "Frontend",
        "url": "http://127.0.0.1:5173/",
        "ttl_seconds": 60,
        "reported_state": "ready",
        **updates,
    }


def registry(tmp_path: Path, clock=None, **updates):  # type: ignore[no-untyped-def]
    return LivePreviewRegistry(
        tmp_path / "private",
        project_root=tmp_path / "project",
        workspace_id="workspace-a",
        forbidden_ports=frozenset({8080}),
        clock=clock or (lambda: 1000.0),
        **updates,
    )


def test_empty_reads_do_not_create_storage_and_ready_is_only_reported(tmp_path: Path) -> None:
    store = registry(tmp_path)
    snapshot = bound()
    assert store.list(snapshot, "task", TASK) == ()
    assert not store.root.exists()
    row = store.publish(snapshot, payload())
    assert row.state == "reported_ready" and row.reachability == "unknown"
    assert row.producer_session_id == SESSION and row.producer_agent_id == AGENT
    assert store.list(snapshot, "agent", AGENT) == (row,)
    assert stat.S_IMODE((store.root / "registry.json").stat().st_mode) == 0o600
    assert stat.S_IMODE(store.root.stat().st_mode) == 0o700


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.1:5173",
        "http://127.0.0.1:8080",
        "http://[::1]:8080/",
        "http://127.0.0.1:80",
        "http://127.0.0.1:65536",
        "http://user@127.0.0.1:5173",
        "http://127.0.0.1:5173/?token=secret",
        "http://127.0.0.1:5173/#secret",
        "http://127.0.0.1:5173/signed-secret",
        "http://127.0.0.1:5173/%2f",
        "http://127.0.0.1:5173\\@example.com",
        "http://10.0.0.1:5173",
        "http://[::ffff:127.0.0.1]:5173",
        "http://127.0.0.1:5173\n",
    ],
)
def test_rejects_noncanonical_token_bearing_nonloopback_and_control_origins(
    tmp_path: Path, url: str
) -> None:
    store = registry(tmp_path)
    with pytest.raises(LivePreviewRefusal, match="registration"):
        store.publish(bound(), payload(url=url))
    assert not store.root.exists()


def test_ipv6_separate_origin_is_allowed_without_dns_or_network(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    import socket

    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *args: pytest.fail("No URL lookup is allowed")
    )
    row = registry(tmp_path).publish(bound(), payload(url="http://[::1]:5173"))
    assert row.url == "http://[::1]:5173/"


@pytest.mark.parametrize("change", ["task", "delegation", "target", "agent", "child", "actor"])
def test_publication_requires_exact_canonical_producer_binding(tmp_path: Path, change: str) -> None:
    snapshot = bound()
    if change == "task":
        snapshot.tasks[TASK]["revision"] = "evt." + "5" * 26
    elif change == "delegation":
        snapshot.delegations[RUN]["revision"] = "evt." + "5" * 26
    elif change == "target":
        snapshot.delegations[RUN]["target_ref"] = {"kind": "task", "id": "task." + "5" * 26}
    elif change == "agent":
        snapshot.agents.clear()
    elif change == "child":
        snapshot.delegations[RUN]["child_session_id"] = "arbitrary-author"
    else:
        snapshot.entity_revision_actor_session_ids.clear()
    with pytest.raises(LivePreviewRefusal):
        registry(tmp_path).publish(snapshot, payload())


def test_rejects_author_fields_invalid_ttls_and_reported_states(tmp_path: Path) -> None:
    store = registry(tmp_path)
    for updates in (
        {"producer_agent_id": AGENT},
        {"ttl_seconds": True},
        {"ttl_seconds": 0},
        {"ttl_seconds": 3601},
        {"reported_state": "reachable"},
    ):
        with pytest.raises(LivePreviewRefusal):
            store.publish(bound(), payload(**updates))


def test_expiry_staleness_and_latest_never_fall_back_to_an_older_ready_server(
    tmp_path: Path,
) -> None:
    clock = [1000.0]
    store, snapshot = registry(tmp_path, lambda: clock[0]), bound()
    first = store.publish(snapshot, payload())
    clock[0] += 61
    expired = store.list(snapshot, "task", TASK)[0]
    assert expired.state == "expired" and expired.url is None
    retry = store.publish(snapshot, payload())
    assert retry.output_id == first.output_id and retry.expires_at == first.expires_at
    assert retry.state == "expired"
    new = store.publish(
        snapshot, payload(idempotency_key="preview-publish-2", reported_state="starting")
    )
    latest = store.list(snapshot, "task", TASK)
    assert len(latest) == 1 and latest[0].output_id == new.output_id and latest[0].url is None
    history = store.list(snapshot, "task", TASK, versions="all")
    assert len(history) == 2 and history[1].state == "stale" and history[1].url is None
    snapshot.tasks[TASK]["effective_revision"] = "evt." + "5" * 26
    assert store.list(snapshot, "task", TASK)[0].state == "stale"


def test_cross_project_workspace_task_and_agent_reads_do_not_cross_over(tmp_path: Path) -> None:
    store, snapshot = registry(tmp_path), bound()
    store.publish(snapshot, payload())
    assert store.list(snapshot, "task", "task." + "6" * 26) == ()
    assert store.list(snapshot, "agent", "agent." + "6" * 26) == ()
    other = LivePreviewRegistry(
        tmp_path / "private",
        project_root=tmp_path / "other-project",
        workspace_id=snapshot.workspace_id,
        forbidden_ports=frozenset({8080}),
    )
    assert other.list(snapshot, "task", TASK) == ()
    with pytest.raises(LivePreviewRefusal):
        store.list(replace(snapshot, workspace_id="other"), "task", TASK)
    snapshot.delegations[RUN]["agent_id"] = "agent." + "6" * 26
    assert store.list(snapshot, "agent", AGENT) == ()


def test_retry_conflicts_and_restart_ui_port_revoke_navigation(tmp_path: Path) -> None:
    store, snapshot = registry(tmp_path), bound()
    row = store.publish(snapshot, payload())
    assert store.publish(snapshot, payload()).output_id == row.output_id
    with pytest.raises(LivePreviewRefusal) as caught:
        store.publish(snapshot, payload(title="Different"))
    assert caught.value.code == "live_preview_idempotency_conflict"
    reopened = LivePreviewRegistry(
        tmp_path / "private",
        project_root=tmp_path / "project",
        workspace_id=snapshot.workspace_id,
        forbidden_ports=frozenset({5173}),
        clock=lambda: 1001,
    )
    assert reopened.list(snapshot, "task", TASK)[0].url is None


def test_registry_refuses_repository_storage_symlinks_hardlinks_and_foreign_envelopes(
    tmp_path: Path,
) -> None:
    with pytest.raises(LivePreviewRefusal):
        LivePreviewRegistry(
            tmp_path / "project" / "private",
            project_root=tmp_path / "project",
            workspace_id="workspace-a",
            forbidden_ports=frozenset({8080}),
        )
    store, snapshot = registry(tmp_path), bound()
    store.publish(snapshot, payload())
    path = store.root / "registry.json"
    original = path.read_bytes()
    value = json.loads(original)
    value["project_key"] = "foreign"
    path.write_text(json.dumps(value))
    with pytest.raises(LivePreviewRefusal):
        store.list(snapshot, "task", TASK)
    path.write_bytes(original)
    alias = store.root / "alias"
    alias.hardlink_to(path)
    with pytest.raises(LivePreviewRefusal):
        store.list(snapshot, "task", TASK)
    alias.unlink()
    path.unlink()
    path.symlink_to(tmp_path / "private-other-file")
    with pytest.raises(OSError):
        store.list(snapshot, "task", TASK)


def test_registry_capacity_recovers_after_expired_retention_without_read_writes(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    from agent_commons.runtime import live_previews

    clock = [1000.0]
    store, snapshot = registry(tmp_path, lambda: clock[0]), bound()
    monkeypatch.setattr(live_previews, "MAX_PREVIEWS", 1)
    store.publish(snapshot, payload())
    with pytest.raises(LivePreviewRefusal) as caught:
        store.publish(snapshot, payload(idempotency_key="preview-publish-2"))
    assert caught.value.code == "live_preview_capacity_exceeded"
    original = (store.root / "registry.json").read_bytes()
    clock[0] += 61 + live_previews.EXPIRED_RETENTION_SECONDS
    assert store.list(snapshot, "task", TASK) == ()
    assert (store.root / "registry.json").read_bytes() == original
    row = store.publish(snapshot, payload(idempotency_key="preview-publish-2"))
    assert store.list(snapshot, "task", TASK) == (row,)


def test_atomic_failed_publication_preserves_previous_registration_and_retry(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    from agent_commons.runtime import live_previews

    store, snapshot = registry(tmp_path), bound()
    first = store.publish(snapshot, payload())
    original = (store.root / "registry.json").read_bytes()
    real_replace = live_previews.os.replace

    def fail(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise OSError("interrupted private write")

    monkeypatch.setattr(live_previews.os, "replace", fail)
    with pytest.raises(OSError):
        store.publish(snapshot, payload(idempotency_key="preview-publish-2"))
    assert (store.root / "registry.json").read_bytes() == original
    assert store.list(snapshot, "task", TASK) == (first,)
    assert not list(store.root.glob(".registry-*"))
    monkeypatch.setattr(live_previews.os, "replace", real_replace)
    new = store.publish(snapshot, payload(idempotency_key="preview-publish-2"))
    assert (
        store.publish(snapshot, payload(idempotency_key="preview-publish-2")).output_id
        == new.output_id
    )


def test_symlinked_parent_never_writes_the_target(tmp_path: Path) -> None:
    target = tmp_path / "external"
    target.mkdir()
    (tmp_path / "private").symlink_to(target, target_is_directory=True)
    store = registry(tmp_path)
    with pytest.raises(OSError):
        store.publish(bound(), payload())
    assert list(target.iterdir()) == []


def test_publication_only_registry_can_omit_ui_port_but_cannot_read_links(tmp_path: Path) -> None:
    with pytest.raises(LivePreviewRefusal):
        LivePreviewRegistry(
            tmp_path / "private",
            project_root=tmp_path / "project",
            workspace_id="workspace-a",
            forbidden_ports=frozenset(),
        )
    store = LivePreviewRegistry(
        tmp_path / "private",
        project_root=tmp_path / "project",
        workspace_id="workspace-a",
        forbidden_ports=frozenset(),
        publication_only=True,
        clock=lambda: 1000,
    )
    store.publish(bound(), payload(url="http://127.0.0.1:8080/"))
    with pytest.raises(LivePreviewRefusal) as caught:
        store.list(bound(), "task", TASK)
    assert caught.value.code == "live_preview_publication_only"
    reader = registry(tmp_path)
    assert reader.list(bound(), "task", TASK)[0].state == "unavailable"
    assert reader.list(bound(), "task", TASK)[0].url is None


SUCCESS_REV = "evt." + "6" * 26


def direct_success(snapshot):  # type: ignore[no-untyped-def]
    snapshot.delegations[RUN].update(
        state="succeeded",
        revision=SUCCESS_REV,
        effective_revision=SUCCESS_REV,
        expected_revision=REV,
    )
    snapshot.entity_revision_actor_session_ids[("delegation", RUN, SUCCESS_REV)] = SESSION


@pytest.mark.parametrize("scope", ["task", "agent"])
def test_direct_success_preserves_reported_link_only_until_original_expiry(
    tmp_path: Path,
    scope: str,
) -> None:
    clock = [1000.0]
    store, snapshot = registry(tmp_path, lambda: clock[0]), bound()
    original = store.publish(snapshot, payload())
    original_bytes = (store.root / "registry.json").read_bytes()
    direct_success(snapshot)
    # Other active sessions are irrelevant: only the recorded child author counts.
    snapshot.entity_revision_actor_session_ids[("delegation", "delegation." + "7" * 26, REV)] = (
        "session." + "b" * 32
    )
    scope_id = TASK if scope == "task" else AGENT
    row = store.list(snapshot, scope, scope_id)[0]
    assert row == original
    assert row.reachability == "unknown" and row.state == "reported_ready"
    clock[0] = original.expires_at
    row = store.list(snapshot, scope, scope_id)[0]
    assert row.state == "expired" and row.url is None
    assert row.expires_at == original.expires_at
    assert (store.root / "registry.json").read_bytes() == original_bytes
    # Publication keeps its strict current revision contract even after success.
    with pytest.raises(LivePreviewRefusal):
        store.publish(snapshot, payload())


@pytest.mark.parametrize(
    "change",
    [
        "failed",
        "cancelled",
        "timed_out",
        "needs_operator",
        "active",
        "input_needed",
        "requested",
        "wrong_actor",
        "missing_actor",
        "missing_predecessor",
        "wrong_predecessor",
        "correction",
        "task_revision",
        "task_correction",
        "target_revision",
        "target",
        "child",
        "agent",
    ],
)
def test_success_continuation_requires_exact_uncorrected_child_lineage(
    tmp_path: Path,
    change: str,
) -> None:
    store, snapshot = registry(tmp_path), bound()
    store.publish(snapshot, payload())
    direct_success(snapshot)
    delegation = snapshot.delegations[RUN]
    other_revision = "evt." + "7" * 26
    if change in {
        "failed",
        "cancelled",
        "timed_out",
        "needs_operator",
        "active",
        "input_needed",
        "requested",
    }:
        delegation["state"] = change
    elif change == "wrong_actor":
        snapshot.entity_revision_actor_session_ids[("delegation", RUN, SUCCESS_REV)] = (
            "session." + "b" * 32
        )
    elif change == "missing_actor":
        del snapshot.entity_revision_actor_session_ids[("delegation", RUN, SUCCESS_REV)]
    elif change == "missing_predecessor":
        del snapshot.entity_revision_actor_session_ids[("delegation", RUN, REV)]
    elif change == "wrong_predecessor":
        delegation["expected_revision"] = other_revision
    elif change == "correction":
        delegation["effective_revision"] = other_revision
        snapshot.entity_revision_actor_session_ids[("delegation", RUN, other_revision)] = SESSION
    elif change == "task_revision":
        snapshot.tasks[TASK]["revision"] = other_revision
    elif change == "task_correction":
        snapshot.tasks[TASK]["effective_revision"] = other_revision
    elif change == "target_revision":
        delegation["target_revision"] = other_revision
    elif change == "target":
        delegation["target_ref"] = {"kind": "task", "id": "task." + "7" * 26}
    elif change == "child":
        delegation["child_session_id"] = "session." + "b" * 32
    elif change == "agent":
        delegation["agent_id"] = "agent." + "7" * 26
    for scope, scope_id in (("task", TASK), ("agent", AGENT)):
        rows = store.list(snapshot, scope, scope_id)
        assert not rows or (rows[0].state == "stale" and rows[0].url is None)


@pytest.mark.parametrize("state", ["failed", "cancelled", "timed_out", "needs_operator"])
def test_terminal_failure_revokes_even_exact_revision_publication(
    tmp_path: Path, state: str
) -> None:
    store, snapshot = registry(tmp_path), bound()
    snapshot.delegations[RUN]["state"] = state
    with pytest.raises(LivePreviewRefusal):
        store.publish(snapshot, payload())


@pytest.mark.parametrize(
    "reported_state, expected", [("starting", "starting"), ("unavailable", "unavailable")]
)
def test_success_does_not_promote_reported_readiness(
    tmp_path: Path, reported_state: str, expected: str
) -> None:
    store, snapshot = registry(tmp_path), bound()
    store.publish(snapshot, payload(reported_state=reported_state))
    direct_success(snapshot)
    row = store.list(snapshot, "task", TASK)[0]
    assert row.state == expected and row.url is None and row.reachability == "unknown"


def test_success_keeps_origin_revocation_and_latest_no_fallback(tmp_path: Path) -> None:
    clock = [1000.0]
    store, snapshot = registry(tmp_path, lambda: clock[0]), bound()
    first = store.publish(snapshot, payload())
    clock[0] += 1
    newest = store.publish(
        snapshot, payload(idempotency_key="preview-second", reported_state="unavailable")
    )
    direct_success(snapshot)
    history = store.list(snapshot, "task", TASK, versions="all")
    assert history[0].output_id == newest.output_id and history[0].url is None
    assert history[1].output_id == first.output_id and history[1].state == "stale"
    assert history[1].url is None
    store.forbidden_ports = frozenset({8080, 5173})
    assert all(row.url is None for row in store.list(snapshot, "task", TASK, versions="all"))


def test_real_replayed_success_preserves_exact_preview_without_provider_launch(
    tmp_path: Path,
) -> None:
    from agent_commons.services import CommonsManager

    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="preview-success")
    manager = CommonsManager(repo, state_root=tmp_path / "state")
    parent = manager.start_session(
        stable_instance_id="preview-parent-session-12345678",
        principal="operator",
        client="codex",
        software="tests",
        role="builder",
    )
    manager.session_id = parent["session_id"]
    task = manager.create_task(
        title="Preview frontend",
        description="Exercise preview lineage.",
        acceptance_criteria=("Result remains scoped",),
        idempotency_key="task",
    )
    agent = manager.create_agent(
        name="Frontend",
        profile_id="codex-builder",
        rationale="Preview fixture",
        idempotency_key="agent",
    )
    delegation = manager.create_delegation(
        target_ref=task["entity_ref"],
        target_revision=task["revision"],
        target_profile="codex-builder",
        purpose="implementation",
        on_behalf_of_agent_id=agent["entity_ref"]["id"],
        limits={
            "max_depth": 0,
            "wall_time_seconds": 60,
            "max_attempts": 1,
            "max_concurrency": 1,
            "budget": {"unit": "micro_usd", "limit": 50000},
        },
        idempotency_key="delegation",
    )
    child = manager.start_session(
        stable_instance_id="preview-child-session-12345678",
        principal="worker",
        client="codex",
        software="tests",
        role="builder",
    )
    delegation_id = delegation["entity_ref"]["id"]
    started = manager.start_delegation(
        delegation_id,
        delegation["revision"],
        child_session_id=child["session_id"],
        idempotency_key="started",
    )
    snapshot = manager.snapshot()
    store = LivePreviewRegistry(
        tmp_path / "private",
        project_root=repo,
        workspace_id=snapshot.workspace_id,
        forbidden_ports=frozenset({8080}),
        clock=lambda: 1000.0,
    )
    row = store.publish(
        snapshot,
        payload(
            task_id=task["entity_ref"]["id"],
            task_revision=task["revision"],
            delegation_id=delegation_id,
            delegation_revision=started["revision"],
        ),
    )
    child_manager = CommonsManager(
        repo, state_root=tmp_path / "state", session_id=child["session_id"]
    )
    completed = child_manager.succeed_delegation(
        delegation_id,
        started["revision"],
        summary="Frontend fixture completed",
        result_refs=(task["entity_ref"],),
        idempotency_key="succeeded",
    )
    replay = manager.snapshot()
    record = replay.delegations[delegation_id]
    assert record["state"] == "succeeded" and record["expected_revision"] == started["revision"]
    assert (
        replay.entity_revision_actor("delegation", delegation_id, completed["revision"])
        == child["session_id"]
    )
    assert store.list(replay, "task", task["entity_ref"]["id"]) == (row,)
    assert store.list(replay, "agent", agent["entity_ref"]["id"]) == (row,)


@pytest.mark.parametrize(
    "field, value",
    [
        ("producer_agent_id", "agent." + "7" * 26),
        ("producer_session_id", "session." + "b" * 32),
    ],
)
def test_exact_revision_rejects_corrupted_persisted_producer(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    from agent_commons.runtime.live_previews import _read_binding

    store, snapshot = registry(tmp_path), bound()
    original = store.publish(snapshot, payload())
    # The binding helper must reject a mismatched producer independently of
    # list's existing scope filter, even with an otherwise exact revision.
    with pytest.raises(LivePreviewRefusal):
        _read_binding(snapshot, replace(original, **{field: value}))
    path = store.root / "registry.json"
    document = json.loads(path.read_text())
    document["items"][0]["preview"][field] = value
    path.write_text(json.dumps(document))
    corrupted = path.read_bytes()
    assert store.list(snapshot, "task", TASK) == ()
    assert store.list(snapshot, "agent", AGENT) == ()
    if field == "producer_agent_id":
        assert store.list(snapshot, "agent", value) == ()
    assert path.read_bytes() == corrupted
