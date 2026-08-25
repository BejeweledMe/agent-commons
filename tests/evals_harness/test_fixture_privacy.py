from __future__ import annotations

from pathlib import Path

import pytest

from agent_commons.errors import ValidationError

from .fixture_loader import FIXTURE_NOW, fixture_path, load_work_metrics_fixture


def _unsafe_fixture(tmp_path: Path, replacement: tuple[str, str]) -> Path:
    original, updated = replacement
    content = fixture_path().read_text(encoding="utf-8")
    assert original in content
    target = tmp_path / "work_metrics.json"
    target.write_text(content.replace(original, updated, 1), encoding="utf-8")
    return target


def test_fixture_load_is_deterministic_and_contains_only_closed_synthetic_codes() -> None:
    first = load_work_metrics_fixture()
    second = load_work_metrics_fixture()

    assert first == second
    assert first.fixture_sha256 == second.fixture_sha256
    assert first.fixed_now.isoformat().replace("+00:00", "Z") == FIXTURE_NOW
    assert all(
        abs(event.offset_seconds) <= 2_592_000 for case in first.cases for event in case.events
    )
    assert all("/" not in case.case_id for case in first.cases)


@pytest.mark.parametrize(
    "replacement",
    [
        ('"schema":', '"unknown": true, "schema":'),
        (
            '"case_id": "review_current_pair",',
            '"prompt": "unsafe", "case_id": "review_current_pair",',
        ),
        (
            '"case_id": "review_current_pair",',
            '"provider_output": "unsafe", "case_id": "review_current_pair",',
        ),
        ('"fixed_now": "2026-08-25T12:00:00Z"', '"fixed_now": "2026-08-25T12:00:01Z"'),
        ('"case_id": "review_current_pair"', '"case_id": "/private/unsafe"'),
        ('"task_ref": "task:main"', '"task_ref": "unsafe_ref"'),
        ('"task_revision": "task:main@4"', '"task_revision": "review:main@4"'),
    ],
)
def test_fixture_loader_fails_closed_for_unknown_private_or_nonfixed_input(
    tmp_path: Path, replacement: tuple[str, str]
) -> None:
    with pytest.raises(ValidationError):
        load_work_metrics_fixture(_unsafe_fixture(tmp_path, replacement))


def test_fixture_loader_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    target = tmp_path / "duplicate.json"
    target.write_text(
        '{"schema":"agent_commons.work_metrics_fixture.v1","schema":"agent_commons.work_metrics_fixture.v1"}',
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="duplicate JSON object key"):
        load_work_metrics_fixture(target)
