"""Typed immutable envelopes for canonical Design Package events."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, cast

from .design_packages import DesignPackageDraft
from .envelopes import EventEnvelope


class DesignPackageEnvelope(EventEnvelope):
    design_package_id: str


@dataclass(frozen=True)
class DesignPackageCreatedEnvelope(DesignPackageEnvelope):
    design_package_id: str
    draft: DesignPackageDraft
    event_type: Literal["design_package.created"] = "design_package.created"

    def to_payload(self) -> Mapping[str, object]:
        return {"design_package_id": self.design_package_id, **self.draft.to_payload()}


@dataclass(frozen=True)
class DesignPackageRevisedEnvelope(DesignPackageEnvelope):
    design_package_id: str
    expected_revision: str
    draft: DesignPackageDraft
    event_type: Literal["design_package.revised"] = "design_package.revised"

    def to_payload(self) -> Mapping[str, object]:
        return {
            "design_package_id": self.design_package_id,
            "expected_revision": self.expected_revision,
            **self.draft.to_payload(),
        }


def parse_design_package_envelope(
    event_type: str, payload: Mapping[str, object]
) -> DesignPackageEnvelope | None:
    if event_type not in {"design_package.created", "design_package.revised"}:
        return None
    draft = DesignPackageDraft.from_payload(
        {"title": payload["title"], "screens": payload["screens"]}
    )
    if event_type == "design_package.created":
        return DesignPackageCreatedEnvelope(
            design_package_id=cast(str, payload["design_package_id"]),
            draft=draft,
        )
    return DesignPackageRevisedEnvelope(
        design_package_id=cast(str, payload["design_package_id"]),
        expected_revision=cast(str, payload["expected_revision"]),
        draft=draft,
    )
