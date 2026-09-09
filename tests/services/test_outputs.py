from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from agent_commons.domain.snapshot import ProjectionIssue
from agent_commons.services.artifact_content import ArtifactPreviewRefusal
from agent_commons.services.outputs import OutputReadRefusal, OutputReads
from tests.ui.test_gallery_read import _published

AGENT = "agent." + "1" * 26
OTHER_AGENT = "agent." + "2" * 26
CHILD = "session." + "3" * 26
RUN = "delegation." + "4" * 26
REVIEW = "review." + "5" * 26


def review(
    task_id: str,
    state: str,
    *,
    identifier: str = REVIEW,
    recorded_at: str = "2026-09-08T00:00:00Z",
    stale: bool = False,
) -> dict[str, Any]:
    """One projected review row shaped like the canonical review projection."""

    return {
        "id": identifier,
        "review_id": identifier,
        "state": state,
        "stale": stale,
        "target_ref": {"kind": "task", "id": task_id},
        "target_revision": "evt." + "1" * 26,
        "recorded_at": recorded_at,
    }


class SnapshotManager:
    def __init__(self, manager: Any) -> None:
        self.manager = manager
        self.value = manager.snapshot()
        self.calls = 0

    def snapshot(self):  # type: ignore[no-untyped-def]
        self.calls += 1
        return self.value

    def __getattr__(self, name: str) -> Any:
        return getattr(self.manager, name)


def _bound(tmp_path: Path):  # type: ignore[no-untyped-def]
    manager, package, artifacts = _published(tmp_path)
    wrapped = SnapshotManager(manager)
    screen = package.draft.screens[0]
    task_id = screen.producer_task_binding.identifier
    snapshot = wrapped.value
    snapshot.agents[AGENT] = {"id": AGENT, "state": "active"}
    snapshot.agents[OTHER_AGENT] = {"id": OTHER_AGENT, "state": "active"}
    snapshot.entity_revision_actor_session_ids[
        ("artifact", screen.artifact_binding.identifier, screen.artifact_binding.revision)
    ] = CHILD
    snapshot.delegations[RUN] = {
        "id": RUN,
        "agent_id": AGENT,
        "child_session_id": CHILD,
        "target_ref": {"kind": "task", "id": task_id},
        "target_revision": screen.producer_task_binding.revision,
        "state": "succeeded",
    }
    return wrapped, package, artifacts


def test_task_scope_filters_mixed_package_before_preview_and_limits(tmp_path: Path) -> None:
    manager, package, artifacts = _bound(tmp_path)
    # An unrelated broken image must not be read or consume the scoped limit.
    artifacts[1][1].write_bytes(b"unrelated invalid image")
    task_id = package.draft.screens[0].producer_task_binding.identifier
    result = OutputReads(manager, max_outputs=1).list("task", task_id)
    assert len(result.items) == 1
    assert result.items[0].state == "ready"
    assert result.items[0].producer_session_id == CHILD
    assert result.items[0].producer_agent_id == AGENT
    assert result.items[0].producer_delegation_id == RUN
    assert manager.calls == 1


def test_summary_never_opens_preview_or_claims_ready(tmp_path: Path) -> None:
    manager, package, artifacts = _bound(tmp_path)
    artifacts[0][1].unlink()

    def forbidden(_manager):  # type: ignore[no-untyped-def]
        pytest.fail("summary must not construct a preview reader")

    summary = OutputReads(manager, preview_reader_factory=forbidden).summary("agent", AGENT)
    assert (summary.total, summary.unchecked, summary.stale) == (1, 1, 0)
    detail = OutputReads(manager).list("agent", AGENT)
    assert detail.items[0].state == "stale"
    assert detail.items[0].reason == "artifact_preview_missing_source"


@pytest.mark.parametrize("break_binding", ["child", "task", "revision", "agent", "ambiguous"])
def test_agent_scope_requires_unique_exact_artifact_actor_binding(
    tmp_path: Path, break_binding: str
) -> None:
    manager, package, _ = _bound(tmp_path)
    delegation = dict(manager.value.delegations[RUN])
    if break_binding == "child":
        delegation["child_session_id"] = manager.session_id
    elif break_binding == "task":
        delegation["target_ref"] = {
            "kind": "task",
            "id": package.draft.screens[1].producer_task_binding.identifier,
        }
    elif break_binding == "revision":
        delegation["target_revision"] = "evt." + "0" * 26
    elif break_binding == "agent":
        delegation["agent_id"] = "agent." + "0" * 26
    else:
        manager.value.delegations["delegation." + "5" * 26] = dict(delegation)
    manager.value.delegations[RUN] = delegation
    assert OutputReads(manager).list("agent", AGENT).items == ()
    task_id = package.draft.screens[0].producer_task_binding.identifier
    item = OutputReads(manager).list("task", task_id).items[0]
    assert item.producer_agent_id is None
    assert item.producer_session_id == CHILD


def test_assignee_and_package_or_task_actor_do_not_establish_agent_ownership(
    tmp_path: Path,
) -> None:
    manager, package, _ = _bound(tmp_path)
    manager.value.delegations.clear()
    task_id = package.draft.screens[0].producer_task_binding.identifier
    task = dict(manager.value.tasks[task_id])
    task["agent_id"] = OTHER_AGENT
    manager.value.tasks[task_id] = task
    assert OutputReads(manager).summary("agent", AGENT).total == 0
    assert OutputReads(manager).summary("agent", OTHER_AGENT).total == 0
    assert OutputReads(manager).summary("task", task_id).total == 1


def test_latest_is_current_package_and_never_falls_back_when_stale(tmp_path: Path) -> None:
    manager, package, _ = _bound(tmp_path)
    task_id = package.draft.screens[0].producer_task_binding.identifier
    revision = "evt." + "7" * 26
    new_package = replace(package, revision=revision, effective_revision=revision)
    manager.value.design_packages[package.design_package_id] = new_package
    manager.value.design_package_revisions[(package.design_package_id, package.revision)] = package
    # A lifecycle edit makes the latest screen stale. Do not reopen old pixels.
    task = dict(manager.value.tasks[task_id])
    task["effective_revision"] = "evt." + "8" * 26
    manager.value.tasks[task_id] = task

    def forbidden(_manager):  # type: ignore[no-untyped-def]
        pytest.fail("stale bindings must not read bytes")

    reads = OutputReads(manager, preview_reader_factory=forbidden)
    latest = reads.list("task", task_id)
    history = reads.list("task", task_id, versions="all")
    assert len(latest.items) == 1
    assert latest.items[0].package_revision == revision
    assert latest.items[0].state == "stale"
    assert latest.items[0].version_count == 2
    assert len(history.items) == 2
    assert {item.reason for item in history.items} == {
        "producer_task_revision_changed",
        "package_revision_superseded",
    }


def test_removed_screen_keeps_last_scoped_result_until_reclassification(
    tmp_path: Path,
) -> None:
    manager, package, _ = _bound(tmp_path)
    screen = package.draft.screens[0]
    task_id = screen.producer_task_binding.identifier
    manager.value.design_package_revisions[(package.design_package_id, package.revision)] = package
    manager.value.design_packages[package.design_package_id] = replace(
        package,
        revision="evt." + "9" * 26,
        draft=replace(package.draft, screens=(package.draft.screens[1],)),
    )
    reads = OutputReads(manager)
    assert reads.summary("task", task_id).total == 1
    item = reads.list("task", task_id).items[0]
    assert item.latest is True
    assert item.reason == "package_revision_superseded"
    assert len(reads.list("task", task_id, versions="all").items) == 1
    artifact = dict(manager.value.artifacts[screen.artifact_binding.identifier])
    artifact["classification"] = "restricted"
    manager.value.artifacts[screen.artifact_binding.identifier] = artifact
    assert reads.list("task", task_id, versions="all").items == ()


def test_bound_exceeded_fails_before_opening_reader(tmp_path: Path) -> None:
    manager, package, _ = _bound(tmp_path)
    # Both distinct packages are relevant to this task; neither silently wins.
    second = replace(package, design_package_id="design_package." + "6" * 26)
    manager.value.design_packages[second.design_package_id] = second

    def forbidden(_manager):  # type: ignore[no-untyped-def]
        pytest.fail("oversized result must fail before preview access")

    reads = OutputReads(manager, preview_reader_factory=forbidden, max_outputs=1)
    assert reads.summary("agent", AGENT).total == 2
    with pytest.raises(OutputReadRefusal, match="display limit"):
        reads.list("agent", AGENT)


def test_missing_provenance_stays_visible_to_task_as_stale(tmp_path: Path) -> None:
    manager, package, _ = _bound(tmp_path)
    screen = package.draft.screens[0]
    del manager.value.entity_revision_actor_session_ids[
        ("artifact", screen.artifact_binding.identifier, screen.artifact_binding.revision)
    ]
    item = OutputReads(manager).list("task", screen.producer_task_binding.identifier).items[0]
    assert item.state == "stale"
    assert item.reason == "producer_provenance_missing"
    assert item.producer_session_id is None
    assert OutputReads(manager).summary("agent", AGENT).total == 0


@pytest.mark.parametrize("code", ["artifact_preview_invalid_image", "sensitive-code"])
def test_preview_refusals_never_forward_exception_prose(tmp_path: Path, code: str) -> None:
    manager, _, _ = _bound(tmp_path)

    class BrokenReader:
        def read(self, _identifier):  # type: ignore[no-untyped-def]
            raise ArtifactPreviewRefusal(code, 415, "sensitive detail")

    item = (
        OutputReads(manager, preview_reader_factory=lambda _: BrokenReader())
        .list("agent", AGENT)
        .items[0]
    )
    assert item.state == "unavailable"
    assert item.reason == (
        "artifact_preview_invalid_image"
        if code == "artifact_preview_invalid_image"
        else "output_preview_unavailable"
    )
    assert "sensitive" not in repr(item)


@pytest.mark.parametrize(
    ("kind", "identifier", "versions", "code"),
    [
        ("project", AGENT, "latest", "outputs_invalid_scope"),
        ("task", AGENT, "latest", "outputs_invalid_scope"),
        ("agent", "agent.invalid", "latest", "outputs_invalid_scope"),
        ("agent", AGENT, "oldest", "outputs_invalid_versions"),
        ("agent", "agent." + "0" * 26, "latest", "outputs_scope_not_found"),
    ],
)
def test_invalid_or_foreign_scope_is_closed(
    tmp_path: Path, kind: str, identifier: str, versions: str, code: str
) -> None:
    manager, _, _ = _bound(tmp_path)
    with pytest.raises(OutputReadRefusal) as exc:
        OutputReads(manager).list(kind, identifier, versions=versions)
    assert exc.value.code == code


def test_projection_fault_is_closed(tmp_path: Path) -> None:
    manager, _, _ = _bound(tmp_path)
    manager.value.issues.append(ProjectionIssue("bad", "error", "sensitive source detail"))
    with pytest.raises(OutputReadRefusal) as exc:
        OutputReads(manager).summary("agent", AGENT)
    assert exc.value.code == "outputs_unavailable"
    assert "sensitive" not in str(exc.value)


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("requested", "awaiting"),
        ("approved", "approved"),
        ("changes_requested", "returned"),
        ("abstained", None),
    ],
)
def test_review_state_is_joined_from_the_task_review_projection(
    tmp_path: Path, state: str, expected: str | None
) -> None:
    manager, package, _ = _bound(tmp_path)
    task_id = package.draft.screens[0].producer_task_binding.identifier
    manager.value.reviews[REVIEW] = review(task_id, state)
    item = OutputReads(manager).list("task", task_id).items[0]
    assert item.state == "ready"
    assert item.review_state == expected


def test_absent_or_stale_review_reports_no_review_state(tmp_path: Path) -> None:
    manager, package, _ = _bound(tmp_path)
    task_id = package.draft.screens[0].producer_task_binding.identifier
    assert OutputReads(manager).list("task", task_id).items[0].review_state is None
    manager.value.reviews[REVIEW] = review(task_id, "approved", stale=True)
    assert OutputReads(manager).list("task", task_id).items[0].review_state is None
    assert OutputReads(manager).summary("task", task_id).total == 1


def test_newest_current_review_wins_and_never_follows_output_freshness(tmp_path: Path) -> None:
    manager, package, _ = _bound(tmp_path)
    task_id = package.draft.screens[0].producer_task_binding.identifier
    newer = "review." + "6" * 26
    manager.value.reviews[REVIEW] = review(
        task_id, "changes_requested", recorded_at="2026-09-07T00:00:00Z"
    )
    manager.value.reviews[newer] = review(
        task_id, "approved", identifier=newer, recorded_at="2026-09-08T00:00:00Z"
    )
    assert OutputReads(manager).list("task", task_id).items[0].review_state == "approved"
    # Another task's review never reaches this scope.
    manager.value.reviews.clear()
    manager.value.reviews[REVIEW] = review(
        package.draft.screens[1].producer_task_binding.identifier, "approved"
    )
    assert OutputReads(manager).list("task", task_id).items[0].review_state is None
    # A stale image is not a verdict: freshness must not invent one either way.
    manager.value.reviews[REVIEW] = review(task_id, "requested")
    task = dict(manager.value.tasks[task_id])
    task["effective_revision"] = "evt." + "8" * 26
    manager.value.tasks[task_id] = task
    stale = OutputReads(manager).list("task", task_id).items[0]
    assert stale.state == "stale"
    assert stale.reason == "producer_task_revision_changed"
    assert stale.review_state == "awaiting"


def test_malformed_or_foreign_review_rows_never_close_the_scope(tmp_path: Path) -> None:
    manager, package, _ = _bound(tmp_path)
    task_id = package.draft.screens[0].producer_task_binding.identifier
    broken = review(task_id, "approved", identifier="review.broken")
    broken["recorded_at"] = "not an instant"
    manager.value.reviews["review.broken"] = broken
    artifact_review = review(task_id, "approved", identifier="review." + "7" * 26)
    artifact_review["target_ref"] = {"kind": "artifact", "id": "artifact." + "7" * 26}
    manager.value.reviews[artifact_review["id"]] = artifact_review
    item = OutputReads(manager).list("task", task_id).items[0]
    assert item.state == "ready"
    assert item.review_state is None


def test_latest_agent_output_survives_replacement_by_another_producer(tmp_path: Path) -> None:
    manager, package, _ = _bound(tmp_path)
    old_screen = package.draft.screens[0]
    new_revision = "evt." + "B" * 26
    new_child = "session." + "C" * 26
    artifact = dict(manager.value.artifacts[old_screen.artifact_binding.identifier])
    artifact.update(revision=new_revision, effective_revision=new_revision)
    manager.value.artifacts[old_screen.artifact_binding.identifier] = artifact
    manager.value.entity_revision_actor_session_ids[
        ("artifact", old_screen.artifact_binding.identifier, new_revision)
    ] = new_child
    manager.value.delegations["delegation." + "D" * 26] = {
        **dict(manager.value.delegations[RUN]),
        "child_session_id": new_child,
        "agent_id": OTHER_AGENT,
    }
    new_screen = replace(
        old_screen, artifact_binding=replace(old_screen.artifact_binding, revision=new_revision)
    )
    manager.value.design_package_revisions[(package.design_package_id, package.revision)] = package
    manager.value.design_packages[package.design_package_id] = replace(
        package,
        revision="evt." + "E" * 26,
        draft=replace(package.draft, screens=(new_screen, package.draft.screens[1])),
    )
    reads = OutputReads(manager)
    assert reads.summary("agent", AGENT).total == 1
    previous = reads.list("agent", AGENT).items[0]
    assert previous.latest is True
    assert previous.package_revision == package.revision
    assert previous.producer_agent_id == AGENT
    assert previous.reason == "package_revision_superseded"
    assert reads.summary("agent", OTHER_AGENT).total == 1
