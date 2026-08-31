from __future__ import annotations

from tests.ui.conftest import authorized


def _draft(
    summary: str = "Frozen product baseline",
    *,
    source: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "summary": summary,
        "facts": (
            []
            if source is None
            else [
                {
                    "statement": "This fact is bound to the exact canonical source.",
                    "source_refs": [{"ref": source["entity_ref"], "revision": source["revision"]}],
                }
            ]
        ),
        "decision_refs": [],
        "open_questions": ["Which bounded slice should run next?"],
    }


def test_work_context_pack_publish_read_revise_and_idempotency(writable_client, writable) -> None:  # type: ignore[no-untyped-def]
    source_path = writable.repo / "context-source.txt"
    source_path.write_text("exact bounded source\n", encoding="utf-8")
    source = writable.writer().register_artifact(
        source_path,
        media_type="text/plain",
        classification="internal",
        idempotency_key="work-context-source",
    )
    create_body = {
        "draft": _draft(source=source),
        "idempotency_key": "work-pack-create",
    }
    first = writable_client.post("/api/work/context-packs", json=create_body, headers=authorized())
    repeated = writable_client.post(
        "/api/work/context-packs", json=create_body, headers=authorized()
    )
    assert first.status_code == repeated.status_code == 200
    assert first.json() == repeated.json()
    created = first.json()
    assert set(created) == {
        "schema",
        "state",
        "context_pack_id",
        "revision",
        "recorded_at",
        "summary",
        "facts",
        "decision_refs",
        "open_questions",
    }
    assert "author_session_ids" not in created

    catalog = writable_client.get("/api/work/context-packs", headers=authorized()).json()
    assert catalog["schema"] == "agent-commons.ui.context-packs.v1"
    assert catalog["state"] == "ready"
    assert catalog["packs"][0]["revision"] == created["revision"]
    detail = writable_client.get(
        f"/api/work/context-packs/{created['context_pack_id']}", headers=authorized()
    )
    assert detail.status_code == 200
    assert detail.json() == created

    revise_body = {
        "expected_revision": created["revision"],
        "draft": _draft("Revised frozen baseline", source=source),
        "idempotency_key": "work-pack-revise",
    }
    revised = writable_client.post(
        f"/api/work/context-packs/{created['context_pack_id']}/revisions",
        json=revise_body,
        headers=authorized(),
    )
    repeated_revision = writable_client.post(
        f"/api/work/context-packs/{created['context_pack_id']}/revisions",
        json=revise_body,
        headers=authorized(),
    )
    assert revised.status_code == repeated_revision.status_code == 200
    assert revised.json() == repeated_revision.json()
    assert revised.json()["revision"] != created["revision"]

    before = len(writable.manager().snapshot().context_pack_revisions)
    stale_body = {
        **revise_body,
        "idempotency_key": "work-pack-stale",
        "draft": _draft("Stale write must not land"),
    }
    stale = writable_client.post(
        f"/api/work/context-packs/{created['context_pack_id']}/revisions",
        json=stale_body,
        headers=authorized(),
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "context_pack_stale"
    assert stale.json()["error"]["safe_next_actions"] == [
        "Reload the latest revision and retry with its exact revision."
    ]
    assert len(writable.manager().snapshot().context_pack_revisions) == before


def test_work_context_pack_envelope_is_closed_bounded_and_authenticated(
    writable_client, client
) -> None:  # type: ignore[no-untyped-def]
    unauthorized = writable_client.get("/api/work/context-packs")
    assert unauthorized.status_code == 401

    unknown = writable_client.post(
        "/api/work/context-packs",
        json={
            "draft": _draft(),
            "idempotency_key": "unknown-field",
            "transcript": "must never cross the API",
        },
        headers=authorized(),
    )
    assert unknown.status_code == 400
    assert unknown.json()["error"]["code"] == "invalid_request"

    malformed_revision = writable_client.post(
        "/api/work/context-packs/context_pack.not-exact/revisions",
        json={
            "expected_revision": "evt.not-exact",
            "draft": _draft(),
            "idempotency_key": "malformed-revision",
        },
        headers=authorized(),
    )
    assert malformed_revision.status_code == 400

    oversized = writable_client.post(
        "/api/work/context-packs",
        content=b'{"draft":{"summary":"' + (b"x" * 80_000) + b'"}}',
        headers={**authorized(), "content-type": "application/json"},
    )
    assert oversized.status_code == 400
    assert oversized.json()["error"]["code"] == "invalid_request"

    # A read-only panel exposes reads, but no canonical Context Pack writes.
    assert client.get("/api/work/context-packs", headers=authorized()).status_code == 200
    assert (
        client.post(
            "/api/work/context-packs",
            json={"draft": _draft(), "idempotency_key": "read-only"},
            headers=authorized(),
        ).status_code
        == 405
    )
