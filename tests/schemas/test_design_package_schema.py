from __future__ import annotations

import pytest

from agent_commons.core.schema_registry import SchemaRegistry
from agent_commons.domain.validation import validate_payload
from agent_commons.errors import ValidationError

PACKAGE_ID = "design_package." + "0" * 25 + "1"
SCREEN_ID = "screen." + "0" * 25 + "1"
EVENT_ID = "evt." + "0" * 25 + "1"
ARTIFACT_ID = "artifact." + "0" * 25 + "1"
TASK_ID = "task." + "0" * 25 + "1"


def _payload() -> dict[str, object]:
    return {
        "design_package_id": PACKAGE_ID,
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
                "artifact_content_revision": "sha256:" + "a" * 64,
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


def test_packaged_design_package_schema_and_domain_validator_agree() -> None:
    registry = SchemaRegistry()
    payload = _payload()

    assert "commons.payload.design_package.v1" in registry.schema_names
    registry.validate("commons.payload.design_package.v1", payload)
    validate_payload("design_package.created", payload)


def test_design_package_schema_rejects_unknown_unsafe_and_unordered_shapes() -> None:
    registry = SchemaRegistry()
    payload = _payload()
    payload["filesystem_path"] = "/private/design.png"
    with pytest.raises(ValidationError, match="Additional properties"):
        registry.validate("commons.payload.design_package.v1", payload)

    payload = _payload()
    screens = payload["screens"]
    assert isinstance(screens, list) and isinstance(screens[0], dict)
    screens[0]["media_type"] = "image/svg+xml"
    with pytest.raises(ValidationError, match="not one of"):
        registry.validate("commons.payload.design_package.v1", payload)

    payload = _payload()
    screens = payload["screens"]
    assert isinstance(screens, list) and isinstance(screens[0], dict)
    screens[0]["ordinal"] = 2
    registry.validate("commons.payload.design_package.v1", payload)
    with pytest.raises(ValidationError, match="contiguous"):
        validate_payload("design_package.created", payload)
