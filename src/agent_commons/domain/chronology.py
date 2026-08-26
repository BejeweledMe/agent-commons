"""Chronological ordering keys for validated persisted records."""

from __future__ import annotations

from datetime import UTC, datetime


def chronological_key(recorded_at: object, identifier: object) -> tuple[datetime, str]:
    """Return an instant-first stable ordering key for a persisted record.

    Event storage validates ``recorded_at`` as a timezone-aware ISO instant, but
    historic records use both whole-second and fractional-second ``Z`` forms.
    Those forms are not lexicographically chronological within one second.
    """

    parsed = datetime.fromisoformat(str(recorded_at).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("persisted record timestamp must include a timezone")
    return parsed.astimezone(UTC), str(identifier)
