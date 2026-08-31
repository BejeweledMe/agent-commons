from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from agent_commons.domain.design_packages import DesignPackageDraft, DesignPackageRecord
from agent_commons.errors import ValidationError

PACKAGE_ID = "design_package." + "0" * 25 + "1"
SCREEN_ID = "screen." + "0" * 25 + "1"
EVENT_ID = "evt." + "0" * 25 + "1"
ARTIFACT_ID = "artifact." + "0" * 25 + "1"
TASK_ID = "task." + "0" * 25 + "1"
CONTENT_REVISION = "sha256:" + "a" * 64


def _payload() -> dict[str, object]:
    return {
        "title": "Checkout flow",
        "screens": [
            {
                "screen_id": SCREEN_ID,
                "ordinal": 1,
                "title": "Checkout",
                "artifact_binding": {
                    "ref": {"kind": "artifact", "id": ARTIFACT_ID},
                    "revision": EVENT_ID,
                },
                "artifact_content_revision": CONTENT_REVISION,
                "producer_task_binding": {
                    "ref": {"kind": "task", "id": TASK_ID},
                    "revision": EVENT_ID,
                },
                "classification": "internal",
                "media_type": "image/png",
                "safe_preview_eligible": True,
            }
        ],
    }


def test_design_package_deeply_owns_input_and_exposed_mappings() -> None:
    payload = _payload()
    draft = DesignPackageDraft.from_payload(payload)
    record = DesignPackageRecord.create(
        design_package_id=PACKAGE_ID,
        revision=EVENT_ID,
        source_event_id=EVENT_ID,
        draft=draft,
        producer_session_id="session.builder",
        recorded_at="2026-08-30T00:00:00Z",
        author_session_ids=("session.builder",),
    )

    screens = payload["screens"]
    assert isinstance(screens, list)
    screen = screens[0]
    assert isinstance(screen, dict)
    screen["title"] = "mutated"
    artifact_binding = screen["artifact_binding"]
    assert isinstance(artifact_binding, dict)
    artifact_binding["revision"] = "evt." + "0" * 25 + "2"

    assert record.draft.screens[0].title == "Checkout"
    assert record.draft.screens[0].artifact_binding.revision == EVENT_ID
    exposed = record.to_dict()
    exposed_screens = exposed["screens"]
    assert isinstance(exposed_screens, list)
    exposed_screens[0]["title"] = "also mutated"
    assert record.to_dict()["screens"][0]["title"] == "Checkout"
    with pytest.raises(FrozenInstanceError):
        record.revision = "evt." + "0" * 25 + "3"  # type: ignore[misc]


def test_design_package_requires_deterministic_unique_order() -> None:
    payload = _payload()
    screens = payload["screens"]
    assert isinstance(screens, list)
    second = dict(screens[0])
    second["screen_id"] = "screen." + "0" * 25 + "2"
    second["ordinal"] = 3
    second["artifact_binding"] = {
        "ref": {"kind": "artifact", "id": "artifact." + "0" * 25 + "2"},
        "revision": EVENT_ID,
    }
    screens.append(second)
    with pytest.raises(ValidationError, match="contiguous"):
        DesignPackageDraft.from_payload(payload)

    payload = _payload()
    screens = payload["screens"]
    assert isinstance(screens, list)
    screens.append({**screens[0], "ordinal": 2})
    with pytest.raises(ValidationError, match="identifiers must be unique"):
        DesignPackageDraft.from_payload(payload)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("classification", "restricted", "classification is not preview eligible"),
        ("media_type", "image/svg+xml", "media_type is not preview eligible"),
        ("safe_preview_eligible", False, "safe_preview_eligible must be true"),
        ("artifact_content_revision", "not-a-hash", "lowercase SHA-256"),
    ],
)
def test_design_package_refuses_non_preview_safe_claims(
    field: str, value: object, message: str
) -> None:
    payload = _payload()
    screens = payload["screens"]
    assert isinstance(screens, list) and isinstance(screens[0], dict)
    screens[0][field] = value

    with pytest.raises(ValidationError, match=message):
        DesignPackageDraft.from_payload(payload)


def test_design_package_stops_unbounded_hostile_list_without_echo() -> None:
    class UnboundedList(list[object]):
        yielded = 0

        def __iter__(self):  # type: ignore[no-untyped-def]
            while True:
                self.yielded += 1
                yield {"secret": "must-not-echo"}

    payload = _payload()
    hostile = UnboundedList()
    payload["screens"] = hostile

    with pytest.raises(ValidationError) as refused:
        DesignPackageDraft.from_payload(payload)

    assert hostile.yielded == 66
    assert "safe ownership limit" in str(refused.value)
    assert "must-not-echo" not in str(refused.value)


def test_design_package_sanitizes_hostile_mapping_exception() -> None:
    class HostileMapping(dict[str, object]):
        def items(self):  # type: ignore[no-untyped-def]
            raise RuntimeError("secret-from-hostile-container")

    with pytest.raises(ValidationError) as refused:
        DesignPackageDraft.from_payload(HostileMapping(_payload()))

    assert str(refused.value) == "design package mapping could not be safely copied"
    assert "secret-from-hostile-container" not in str(refused.value)
    assert refused.value.__cause__ is None
