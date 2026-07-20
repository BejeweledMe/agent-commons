from __future__ import annotations

import math

import pytest

from agent_commons.core.canonical import canonical_json_bytes, loads_json_strict
from agent_commons.core.ids import is_typed_id, new_sortable_id, stable_id
from agent_commons.core.refs import TypedRef, iter_typed_refs, normalize_ref, parse_ref
from agent_commons.errors import ValidationError


def test_canonical_json_is_stable_and_strict() -> None:
    assert canonical_json_bytes({"z": 1, "a": "é"}) == b'{"a":"\xc3\xa9","z":1}'
    with pytest.raises(ValidationError, match="non-finite"):
        canonical_json_bytes({"value": math.inf})
    with pytest.raises(ValidationError, match="unsupported JSON"):
        canonical_json_bytes((1, 2))
    with pytest.raises(ValidationError, match="duplicate JSON object key"):
        loads_json_strict('{"same": 1, "same": 2}')


def test_typed_ids_and_stable_ids() -> None:
    first = new_sortable_id("task")
    second = new_sortable_id("task")
    assert first != second
    assert is_typed_id(first, "task")
    assert not is_typed_id(first, "event")
    assert stable_id("workspace", "seed") == stable_id("workspace", b"seed")
    with pytest.raises(ValueError):
        new_sortable_id("Bad-Prefix")


def test_only_explicit_typed_objects_are_refs() -> None:
    explicit = TypedRef("task", "task.1")
    assert normalize_ref(explicit) == {"kind": "task", "id": "task.1"}
    assert parse_ref("task:task.1") == explicit
    value = {
        "looks_like_ref": "task.1",
        "artifact_ref": "mft.fake.sha256.abc",
        "nested": [{"kind": "task", "id": "task.1"}],
    }
    assert list(iter_typed_refs(value)) == [explicit]
    with pytest.raises(ValidationError, match="exactly"):
        normalize_ref({"kind": "task", "id": "task.1", "label": "extra"})
