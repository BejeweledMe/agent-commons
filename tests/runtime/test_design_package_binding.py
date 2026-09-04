from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_commons.domain.design_packages import (
    DesignPackageDraft,
    DesignPackageRecord,
)
from agent_commons.errors import IntegrityError
from agent_commons.runtime.design_package_binding import (
    DESIGN_PACKAGE_BINDING_STATE_SCHEMA,
    DesignPackageBindingMetadata,
    DesignPackageBindingRefusal,
    DesignPackageBindingRefusalCode,
    DesignPackageBindingRequest,
    DesignPackageBindingResolver,
    DesignPackageBindingStore,
)

PACKAGE_ID = "design_package." + "0" * 25 + "1"
REVISION_1 = "evt." + "0" * 25 + "1"
REVISION_2 = "evt." + "0" * 25 + "2"
ARTIFACT_ID = "artifact." + "0" * 25 + "1"
TASK_ID = "task." + "0" * 25 + "1"
SCREEN_ID = "screen." + "0" * 25 + "1"


def _record(*, revision: str = REVISION_1, effective: str | None = None) -> DesignPackageRecord:
    return (
        DesignPackageRecord.create(
            design_package_id=PACKAGE_ID,
            revision=revision,
            source_event_id=revision,
            draft=DesignPackageDraft.from_payload(
                {
                    "title": "Private checkout mocks",
                    "screens": [
                        {
                            "screen_id": SCREEN_ID,
                            "ordinal": 1,
                            "title": "Checkout",
                            "artifact_binding": {
                                "ref": {"kind": "artifact", "id": ARTIFACT_ID},
                                "revision": REVISION_1,
                            },
                            "artifact_content_revision": "sha256:" + "a" * 64,
                            "producer_task_binding": {
                                "ref": {"kind": "task", "id": TASK_ID},
                                "revision": REVISION_2,
                            },
                            "classification": "internal",
                            "media_type": "image/png",
                            "safe_preview_eligible": True,
                        }
                    ],
                }
            ),
            producer_session_id="session.design-author",
            recorded_at="2026-09-03T00:00:00Z",
            author_session_ids=("session.design-author",),
        )
        if effective is None
        else DesignPackageRecord(
            design_package_id=PACKAGE_ID,
            revision=revision,
            effective_revision=effective,
            source_event_id=revision,
            draft=DesignPackageDraft.from_payload(
                {
                    "title": "Private checkout mocks",
                    "screens": [
                        {
                            "screen_id": SCREEN_ID,
                            "ordinal": 1,
                            "title": "Checkout",
                            "artifact_binding": {
                                "ref": {"kind": "artifact", "id": ARTIFACT_ID},
                                "revision": REVISION_1,
                            },
                            "artifact_content_revision": "sha256:" + "a" * 64,
                            "producer_task_binding": {
                                "ref": {"kind": "task", "id": TASK_ID},
                                "revision": REVISION_2,
                            },
                            "classification": "internal",
                            "media_type": "image/png",
                            "safe_preview_eligible": True,
                        }
                    ],
                }
            ),
            producer_session_id="session.design-author",
            recorded_at="2026-09-03T00:00:00Z",
            author_session_ids=("session.design-author",),
        )
    )


def _resolve(
    record: DesignPackageRecord | None,
    *,
    authorized: bool = True,
) -> DesignPackageBindingMetadata | DesignPackageBindingRefusal:
    return DesignPackageBindingResolver().resolve(
        DesignPackageBindingRequest(
            design_package_id=PACKAGE_ID,
            design_package_revision=REVISION_1,
        ),
        load_exact=lambda _package_id, _revision: record,
        authorize_exact=lambda _record: authorized,
    )


def test_resolver_returns_metadata_only_for_exact_current_package() -> None:
    result = _resolve(_record())

    assert isinstance(result, DesignPackageBindingMetadata)
    assert result.design_package_id == PACKAGE_ID
    assert result.design_package_revision == REVISION_1
    assert result.screen_count == 1
    assert result.screens[0].artifact_id == ARTIFACT_ID
    assert result.screens[0].producer_task_id == TASK_ID


def test_resolver_refuses_missing_stale_and_unauthorized_packages() -> None:
    missing = _resolve(None)
    stale = _resolve(_record(revision=REVISION_1, effective=REVISION_2))
    unauthorized = _resolve(_record(), authorized=False)

    assert isinstance(missing, DesignPackageBindingRefusal)
    assert missing.code is DesignPackageBindingRefusalCode.MISSING
    assert isinstance(stale, DesignPackageBindingRefusal)
    assert stale.code is DesignPackageBindingRefusalCode.STALE
    assert isinstance(unauthorized, DesignPackageBindingRefusal)
    assert unauthorized.code is DesignPackageBindingRefusalCode.UNAUTHORIZED


def test_operational_binding_store_persists_no_design_source_or_titles(tmp_path: Path) -> None:
    metadata = DesignPackageBindingMetadata.from_record(_record())
    store = DesignPackageBindingStore(tmp_path / "state")
    delegation_id = "delegation." + "0" * 25 + "1"
    launch_key = "b" * 64

    first = store.bind(delegation_id, launch_key, metadata)
    second = store.bind(delegation_id, launch_key, metadata)

    assert first == second == store.get(delegation_id)
    path = next((tmp_path / "state" / "runtime" / "design-package-bindings").glob("*.json"))
    raw = path.read_text(encoding="utf-8")
    value = json.loads(raw)
    assert value["schema"] == DESIGN_PACKAGE_BINDING_STATE_SCHEMA
    assert value["binding"] == metadata.as_dict()
    assert "Private checkout mocks" not in raw
    assert "Checkout" not in raw
    assert path.stat().st_mode & 0o777 == 0o600


def test_operational_binding_store_rejects_symlink_document(tmp_path: Path) -> None:
    store = DesignPackageBindingStore(tmp_path / "state")
    delegation_id = "delegation." + "0" * 25 + "2"
    path = store._path(delegation_id)
    target = tmp_path / "foreign.json"
    target.write_text("{}", encoding="utf-8")
    path.symlink_to(target)

    with pytest.raises(IntegrityError, match="symlink"):
        store.get(delegation_id)
