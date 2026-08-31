from __future__ import annotations

from agent_commons.ui.context import UIContext
from tests.services.test_design_packages import SCREEN_ID, _draft, _screen_work
from tests.ui.conftest import authorized


def test_gallery_feedback_route_requires_auth_and_binds_exact_revision(
    writable: UIContext,
    writable_client,  # type: ignore[no-untyped-def]
) -> None:
    manager = writable.writer()
    artifact, task, _source = _screen_work(manager)
    package = manager.design_packages.publish(
        _draft(manager, artifact, task), idempotency_key="ui-feedback-package"
    )
    screen = package.draft.screens[0]
    path = f"/api/gallery/{package.design_package_id}/screens/{SCREEN_ID}/feedback"
    payload = {
        "design_package_revision": package.revision,
        "artifact_revision": screen.artifact_binding.revision,
        "producer_task_revision": screen.producer_task_binding.revision,
        "message": "Please align this state with the approved spacing scale.",
        "idempotency_key": "ui-gallery-feedback",
    }

    assert writable_client.post(path, json=payload).status_code == 401
    response = writable_client.post(path, json=payload, headers=authorized())

    assert response.status_code == 200, response.text
    thread_id = response.json()["entity_ref"]["id"]
    thread = manager.snapshot().threads[thread_id]
    assert thread["thread_type"] == "review_discussion"
    assert thread["extensions"]["design_feedback"]["design_package_revision"] == (package.revision)
    assert thread["extensions"]["design_feedback"]["screen_id"] == SCREEN_ID
    assert thread["messages"][0]["body"].startswith("Please align")


def test_gallery_feedback_route_refuses_stale_inspector_without_thread(
    writable: UIContext,
    writable_client,  # type: ignore[no-untyped-def]
) -> None:
    manager = writable.writer()
    artifact, task, _source = _screen_work(manager)
    package = manager.design_packages.publish(
        _draft(manager, artifact, task), idempotency_key="ui-stale-feedback-package"
    )
    screen = package.draft.screens[0]
    revised = manager.design_packages.revise(
        package.design_package_id,
        package.revision,
        package.draft.to_payload(),
        idempotency_key="ui-stale-feedback-revision",
    )
    before = len(manager.snapshot().threads)

    response = writable_client.post(
        f"/api/gallery/{package.design_package_id}/screens/{SCREEN_ID}/feedback",
        json={
            "design_package_revision": package.revision,
            "artifact_revision": screen.artifact_binding.revision,
            "producer_task_revision": screen.producer_task_binding.revision,
            "message": "This comment belongs only to the stale inspector.",
            "idempotency_key": "ui-stale-gallery-feedback",
        },
        headers=authorized(),
    )

    assert revised.revision != package.revision
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "design_package_stale"
    assert len(manager.snapshot().threads) == before
