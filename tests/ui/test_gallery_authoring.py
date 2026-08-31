from __future__ import annotations

from fastapi.testclient import TestClient

from agent_commons.services import CommonsManager
from agent_commons.services.design_authoring import build_authoring_snapshot
from agent_commons.ui.context import UIContext
from agent_commons.ui.security import SESSION_COOKIE_NAME
from agent_commons.ui.server import create_app
from tests.services.test_design_packages import _screen_work
from tests.ui.conftest import authorized


def _candidate_payload(writable: UIContext, writable_client):  # type: ignore[no-untyped-def]
    manager = writable.writer()
    _screen_work(manager)
    response = writable_client.get("/api/gallery/authoring", headers=authorized())
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["schema"] == "agent_commons.gallery-authoring.v1"
    assert payload["state"] == "ready"
    assert payload["writes_enabled"] is True
    assert len(payload["candidates"]) == 1
    return payload["candidates"][0]


def test_publish_and_revise_are_exact_idempotent_and_immediately_visible(
    writable: UIContext,
    writable_client,  # type: ignore[no-untyped-def]
) -> None:
    candidate = _candidate_payload(writable, writable_client)
    publish_body = {
        "title": "Checkout journey",
        "screens": [{"candidate_id": candidate["candidate_id"], "title": "Checkout"}],
        "idempotency_key": "gallery-authoring-publish",
    }

    assert writable_client.post("/api/gallery/packages", json=publish_body).status_code == 401
    published = writable_client.post(
        "/api/gallery/packages", json=publish_body, headers=authorized()
    )
    repeated = writable_client.post(
        "/api/gallery/packages", json=publish_body, headers=authorized()
    )
    assert published.status_code == 200, published.text
    assert repeated.json() == published.json()
    package_id = published.json()["design_package_id"]
    first_revision = published.json()["revision"]

    visible = writable_client.get("/api/gallery", headers=authorized()).json()
    assert visible["packages"][0]["design_package_id"] == package_id
    screen = visible["packages"][0]["screens"][0]
    assert screen["artifact_revision"] == candidate["artifact_revision"]
    assert screen["artifact_content_revision"] == candidate["artifact_content_revision"]
    assert screen["producer_task_revision"] == candidate["producer_task_revision"]

    revise_body = {
        **publish_body,
        "title": "Checkout journey approved",
        "expected_revision": first_revision,
        "idempotency_key": "gallery-authoring-revise",
    }
    revised = writable_client.post(
        f"/api/gallery/{package_id}/revisions",
        json=revise_body,
        headers=authorized(),
    )
    retry = writable_client.post(
        f"/api/gallery/{package_id}/revisions",
        json=revise_body,
        headers=authorized(),
    )
    assert revised.status_code == 200, revised.text
    assert retry.json() == revised.json()
    assert revised.json()["revision"] != first_revision
    assert (
        writable_client.get(f"/api/gallery/{package_id}", headers=authorized()).json()["packages"][
            0
        ]["title"]
        == "Checkout journey approved"
    )


def test_stale_revision_and_stale_candidate_refuse_without_new_package_revision(
    writable: UIContext,
    writable_client,  # type: ignore[no-untyped-def]
) -> None:
    candidate = _candidate_payload(writable, writable_client)
    body = {
        "title": "Current",
        "screens": [{"candidate_id": candidate["candidate_id"], "title": "Current"}],
        "idempotency_key": "authoring-current",
    }
    created = writable_client.post("/api/gallery/packages", json=body, headers=authorized()).json()
    package_id = created["design_package_id"]
    first = created["revision"]
    current = writable_client.post(
        f"/api/gallery/{package_id}/revisions",
        json={
            **body,
            "title": "New",
            "expected_revision": first,
            "idempotency_key": "authoring-current-revise",
        },
        headers=authorized(),
    ).json()

    stale = writable_client.post(
        f"/api/gallery/{package_id}/revisions",
        json={
            **body,
            "expected_revision": first,
            "idempotency_key": "authoring-stale-revise",
        },
        headers=authorized(),
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "design_package_stale"
    assert stale.json()["error"]["safe_next_actions"]
    assert writable.writer().design_packages.get(package_id).revision == current["revision"]

    # Changing the exact artifact revision removes the former opaque candidate.
    manager = writable.writer()
    artifact_id = candidate["artifact_id"]
    artifact = manager.snapshot().artifacts[artifact_id]
    source = manager.repo_root / "screens" / "checkout.png"
    manager.revise_artifact(
        artifact_id,
        str(artifact["revision"]),
        source,
        media_type="image/png",
        classification="internal",
        idempotency_key="authoring-artifact-revise",
    )
    before_replays = sum(
        1
        for event in manager.events.iter_events()
        if str(event.event.get("event_type", "")).startswith("design_package.")
    )
    replayed_create = writable_client.post(
        "/api/gallery/packages",
        json=body,
        headers=authorized(),
    )
    replayed_revise = writable_client.post(
        f"/api/gallery/{package_id}/revisions",
        json={
            **body,
            "title": "New",
            "expected_revision": first,
            "idempotency_key": "authoring-current-revise",
        },
        headers=authorized(),
    )
    after_replays = sum(
        1
        for event in manager.events.iter_events()
        if str(event.event.get("event_type", "")).startswith("design_package.")
    )
    assert replayed_create.status_code == 200
    assert replayed_create.json()["revision"] == first
    assert replayed_revise.status_code == 200
    assert replayed_revise.json()["revision"] == current["revision"]
    assert after_replays == before_replays

    refused = writable_client.post(
        "/api/gallery/packages",
        json={**body, "idempotency_key": "authoring-stale-candidate"},
        headers=authorized(),
    )
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "design_package_stale"


def test_authoring_bounds_unknown_fields_and_write_flag_fail_closed(
    writable: UIContext,
    writable_client,  # type: ignore[no-untyped-def]
) -> None:
    candidate = _candidate_payload(writable, writable_client)
    too_many = writable_client.post(
        "/api/gallery/packages",
        json={
            "title": "Too many",
            "screens": [
                {"candidate_id": candidate["candidate_id"], "title": str(index)}
                for index in range(65)
            ],
            "idempotency_key": "authoring-too-many",
        },
        headers=authorized(),
    )
    assert too_many.status_code == 409
    assert too_many.json()["error"]["code"] == "design_package_oversized"

    malicious = writable_client.post(
        "/api/gallery/packages",
        json={
            "title": "No paths",
            "screens": [
                {
                    "candidate_id": candidate["candidate_id"],
                    "title": "Screen",
                    "path": "/etc/passwd",
                }
            ],
            "idempotency_key": "authoring-malicious",
        },
        headers=authorized(),
    )
    assert malicious.status_code == 409
    assert malicious.json()["error"]["code"] == "design_package_invalid"
    assert "/etc/passwd" not in malicious.text

    create_with_revision = writable_client.post(
        "/api/gallery/packages",
        json={
            "title": "Wrong create shape",
            "screens": [{"candidate_id": candidate["candidate_id"], "title": "Screen"}],
            "expected_revision": "evt.01ARZ3NDEKTSV4RRFFQ69G5FAV",
            "idempotency_key": "authoring-create-with-revision",
        },
        headers=authorized(),
    )
    assert create_with_revision.status_code == 409
    assert create_with_revision.json()["error"]["code"] == "design_package_invalid"

    disabled = CommonsManager(
        writable.repo,
        state_root=writable.paths().state_root,
        read_only=True,
        design_package_writes_enabled=False,
    )
    snapshot = build_authoring_snapshot(
        disabled,
        producer_session_id=writable.writer_session_id,
    )
    assert snapshot["state"] == "unavailable"
    assert snapshot["writes_enabled"] is False
    assert snapshot["error"]["code"] == "design_package_unavailable"

    disabled_context = UIContext(
        writable.repo,
        state_root=writable.paths().state_root,
        writer_session_id=writable.writer_session_id,
        design_package_writes_enabled=False,
    )
    app = create_app(
        disabled_context,
        token="disabled-token",
        exchange_code="disabled-code",
        port=51235,
        api_base="/api",
    )
    with TestClient(app, base_url="http://127.0.0.1:51235") as disabled_client:
        headers = {"Cookie": f"{SESSION_COOKIE_NAME}=disabled-token"}
        options = disabled_client.get("/api/gallery/authoring", headers=headers)
        refused = disabled_client.post(
            "/api/gallery/packages",
            json={
                "title": "Disabled",
                "screens": [{"candidate_id": candidate["candidate_id"], "title": "Disabled"}],
                "idempotency_key": "authoring-disabled",
            },
            headers=headers,
        )
    assert options.json()["state"] == "unavailable"
    assert options.json()["error"]["code"] == "design_package_unavailable"
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "design_package_unavailable"
