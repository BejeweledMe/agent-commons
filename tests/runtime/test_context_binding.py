from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from typing import cast

import pytest

from agent_commons.domain.context_pack import (
    ContextPackBinding,
    ContextPackDraft,
    ContextPackRecord,
    ContextPackRefusal,
    ContextPackRefusalCode,
)
from agent_commons.errors import IntegrityError
from agent_commons.runtime.capabilities import LaunchPlan, LaunchPurpose
from agent_commons.runtime.context_binding import (
    CONTEXT_BINDING_STATE_SCHEMA,
    ContextBinding,
    ContextBindingMetadata,
    ContextBindingMode,
    ContextBindingRefusal,
    ContextBindingRefusalCode,
    ContextBindingRequest,
    ContextBindingResolver,
    ContextBindingStore,
)
from agent_commons.runtime.model import BuiltinProfileId
from agent_commons.services.context_compiler import CompiledContext, ContextCompiler

PACK_ID = "context_pack." + "0" * 25 + "1"
REVISION_1 = "evt." + "0" * 25 + "1"
REVISION_2 = "evt." + "0" * 25 + "2"


def _record(*, revision: str = REVISION_1, summary: str = "Stable baseline") -> ContextPackRecord:
    return ContextPackRecord.create(
        context_pack_id=PACK_ID,
        revision=revision,
        source_event_id=revision,
        draft=ContextPackDraft.from_payload(
            {
                "summary": summary,
                "facts": [],
                "decision_refs": [],
                "open_questions": ["What remains open?"],
            }
        ),
        recorded_at="2026-08-30T00:00:00Z",
        author_session_ids=("session.context-author",),
    )


def _resolve(
    record: ContextPackRecord | None,
    *,
    authorized: bool = True,
    compiler: ContextCompiler | None = None,
) -> ContextBinding | ContextBindingRefusal:
    return ContextBindingResolver(compiler=compiler).resolve(
        ContextBindingRequest.accumulated(
            context_pack_id=PACK_ID,
            context_pack_revision=REVISION_1,
        ),
        load_exact=lambda _pack_id, _revision: record,
        authorize_exact=lambda _record: authorized,
    )


def test_fresh_context_has_no_binding_bytes_or_hidden_lookup() -> None:
    calls: list[str] = []

    result = ContextBindingResolver().resolve(
        ContextBindingRequest.fresh(),
        load_exact=lambda _pack_id, _revision: calls.append("lookup"),  # type: ignore[arg-type,return-value]
        authorize_exact=lambda _record: calls.append("authorize") is None,
    )

    assert result == ContextBinding.fresh()
    assert result.mode is ContextBindingMode.FRESH
    assert result.binding is None
    assert result.compiled_context_bytes is None
    assert result.compiled_context_fingerprint is None
    assert calls == []


def test_operational_binding_store_persists_only_bounded_metadata(tmp_path: Path) -> None:
    resolved = _resolve(_record(summary="secret baseline body"))
    assert isinstance(resolved, ContextBinding)
    metadata = ContextBindingMetadata.from_binding(resolved)
    store = ContextBindingStore(tmp_path / "state")
    delegation_id = "delegation." + "0" * 25 + "1"
    launch_key = "a" * 64

    first = store.bind(delegation_id, launch_key, metadata)
    second = store.bind(delegation_id, launch_key, metadata)

    assert first == second == store.get(delegation_id)
    path = next((tmp_path / "state" / "runtime" / "context-bindings").glob("*.json"))
    raw = path.read_text(encoding="utf-8")
    value = json.loads(raw)
    assert value["schema"] == CONTEXT_BINDING_STATE_SCHEMA
    assert value["binding"] == metadata.as_dict()
    assert resolved.compiled_context_bytes is not None
    assert resolved.compiled_context_bytes.decode("utf-8") not in raw
    assert "secret baseline body" not in raw
    assert path.stat().st_mode & 0o777 == 0o600


def test_operational_binding_store_rejects_symlink_document(tmp_path: Path) -> None:
    store = ContextBindingStore(tmp_path / "state")
    delegation_id = "delegation." + "0" * 25 + "2"
    path = store._path(delegation_id)
    target = tmp_path / "foreign.json"
    target.write_text("{}", encoding="utf-8")
    path.symlink_to(target)

    with pytest.raises(IntegrityError, match="symlink"):
        store.get(delegation_id)


def test_operational_binding_store_rejects_dangling_symlink_document(tmp_path: Path) -> None:
    store = ContextBindingStore(tmp_path / "state")
    delegation_id = "delegation." + "0" * 25 + "3"
    store._path(delegation_id).symlink_to(tmp_path / "missing.json")

    with pytest.raises(IntegrityError, match="symlink"):
        store.get(delegation_id)


def test_accumulated_context_binds_exact_deterministic_bytes_and_fingerprint() -> None:
    first = _resolve(_record())
    second = _resolve(_record())

    assert isinstance(first, ContextBinding)
    assert isinstance(second, ContextBinding)
    assert first == second
    assert first.mode is ContextBindingMode.ACCUMULATED
    assert first.binding is not None
    assert first.binding.context_pack_id == PACK_ID
    assert first.binding.context_pack_revision == REVISION_1
    assert first.compiled_context_bytes is not None
    assert first.compiled_context_bytes == second.compiled_context_bytes
    assert first.compiled_context_fingerprint == second.compiled_context_fingerprint


@pytest.mark.parametrize(
    ("record", "authorized", "expected"),
    [
        (None, True, ContextBindingRefusalCode.MISSING),
        (_record(revision=REVISION_2), True, ContextBindingRefusalCode.STALE),
        (_record(), False, ContextBindingRefusalCode.UNAUTHORIZED),
    ],
)
def test_missing_stale_and_unauthorized_context_fail_with_typed_refusal(
    record: ContextPackRecord | None,
    authorized: bool,
    expected: ContextBindingRefusalCode,
) -> None:
    result = _resolve(record, authorized=authorized)

    assert isinstance(result, ContextBindingRefusal)
    assert result.code is expected
    assert "Stable baseline" not in result.message
    assert "Stable baseline" not in result.remediation


def test_oversized_compilation_fails_with_typed_refusal_without_content_echo() -> None:
    result = _resolve(_record(), compiler=ContextCompiler(max_bytes=32))

    assert isinstance(result, ContextBindingRefusal)
    assert result.code is ContextBindingRefusalCode.OVERSIZED
    assert "Stable baseline" not in str(result.as_dict())


def test_unexpected_compiler_exception_becomes_safe_unavailable_refusal() -> None:
    class ExplodingCompiler:
        def compile(self, _record: ContextPackRecord) -> CompiledContext:
            raise RuntimeError("secret compiler detail")

    result = _resolve(_record(), compiler=cast(ContextCompiler, ExplodingCompiler()))

    assert isinstance(result, ContextBindingRefusal)
    assert result.as_dict() == {
        "code": "context_binding_unavailable",
        "message": "The selected Context Pack revision cannot be compiled safely.",
        "remediation": "Inspect the Context Pack and select a valid exact revision.",
    }
    assert "secret compiler detail" not in str(result.as_dict())


def test_hostile_context_pack_refusal_code_access_is_sanitized() -> None:
    class HostileRefusal(ContextPackRefusal):
        def __getattribute__(self, name: str) -> object:
            if name == "code":
                raise RuntimeError("secret refusal code detail")
            return super().__getattribute__(name)

    refusal = HostileRefusal(
        ContextPackRefusalCode.OVERSIZED,
        "secret refusal message",
        "secret refusal remediation",
    )

    class RefusingCompiler:
        def compile(self, _record: ContextPackRecord) -> CompiledContext:
            raise refusal

    result = _resolve(_record(), compiler=cast(ContextCompiler, RefusingCompiler()))

    assert isinstance(result, ContextBindingRefusal)
    assert result.code is ContextBindingRefusalCode.UNAVAILABLE
    assert result.remediation == "Inspect the Context Pack and select a valid exact revision."
    assert "secret refusal" not in str(result.as_dict())


@pytest.mark.parametrize(
    "malformed",
    [
        object(),
        CompiledContext(
            text=cast(str, object()),
            size_bytes=cast(int, "invalid"),
            binding=cast(object, object()),  # type: ignore[arg-type]
            source_refs=(),
            decision_refs=(),
        ),
    ],
)
def test_malformed_compiler_result_becomes_safe_unavailable_refusal(
    malformed: object,
) -> None:
    class MalformedCompiler:
        def compile(self, _record: ContextPackRecord) -> CompiledContext:
            return cast(CompiledContext, malformed)

    result = _resolve(_record(), compiler=cast(ContextCompiler, MalformedCompiler()))

    assert isinstance(result, ContextBindingRefusal)
    assert result.code is ContextBindingRefusalCode.UNAVAILABLE
    assert result.remediation == "Inspect the Context Pack and select a valid exact revision."


def test_hostile_compiler_result_property_access_is_sanitized() -> None:
    class HostileCompiledContext(CompiledContext):
        def __getattribute__(self, name: str) -> object:
            if name in {"text", "binding", "size_bytes"}:
                raise RuntimeError("secret hostile property detail")
            return super().__getattribute__(name)

    hostile = object.__new__(HostileCompiledContext)

    class HostileCompiler:
        def compile(self, _record: ContextPackRecord) -> CompiledContext:
            return hostile

    result = _resolve(_record(), compiler=cast(ContextCompiler, HostileCompiler()))

    assert isinstance(result, ContextBindingRefusal)
    assert result.code is ContextBindingRefusalCode.UNAVAILABLE
    assert result.remediation == "Inspect the Context Pack and select a valid exact revision."
    assert "secret hostile property detail" not in str(result.as_dict())


def test_hostile_record_exact_field_access_is_sanitized() -> None:
    class HostileRecord(ContextPackRecord):
        def __getattribute__(self, name: str) -> object:
            if name in {"context_pack_id", "revision", "effective_revision"}:
                raise RuntimeError("secret record property detail")
            return super().__getattribute__(name)

    hostile = object.__new__(HostileRecord)
    result = _resolve(cast(ContextPackRecord, hostile))

    assert isinstance(result, ContextBindingRefusal)
    assert result.code is ContextBindingRefusalCode.UNAVAILABLE
    assert "secret record property detail" not in str(result.as_dict())


def test_compiler_text_subclass_cannot_run_hostile_encode() -> None:
    class HostileText(str):
        encode_calls = 0

        def encode(self, *args: object, **kwargs: object) -> bytes:
            type(self).encode_calls += 1
            raise RuntimeError("secret hostile encode detail")

    valid = ContextCompiler().compile(_record())
    compiled = CompiledContext(
        text=HostileText(valid.text),
        size_bytes=valid.size_bytes,
        binding=valid.binding,
        source_refs=(),
        decision_refs=(),
    )

    class StaticCompiler:
        def compile(self, _record: ContextPackRecord) -> CompiledContext:
            return compiled

    result = _resolve(_record(), compiler=cast(ContextCompiler, StaticCompiler()))

    assert isinstance(result, ContextBindingRefusal)
    assert result.code is ContextBindingRefusalCode.UNAVAILABLE
    assert HostileText.encode_calls == 0
    assert "secret hostile encode detail" not in str(result.as_dict())


def test_binding_string_subclass_cannot_run_hostile_equality_or_encode() -> None:
    class HostileString(str):
        equality_calls = 0
        encode_calls = 0

        def __eq__(self, other: object) -> bool:
            type(self).equality_calls += 1
            raise RuntimeError("secret hostile equality detail")

        def encode(self, *args: object, **kwargs: object) -> bytes:
            type(self).encode_calls += 1
            raise RuntimeError("secret hostile binding encode detail")

        __hash__ = str.__hash__

    valid = ContextCompiler().compile(_record())
    compiled = CompiledContext(
        text=valid.text,
        size_bytes=valid.size_bytes,
        binding=ContextPackBinding(
            context_pack_id=HostileString(PACK_ID),
            context_pack_revision=REVISION_1,
            compiler_version=HostileString(valid.binding.compiler_version),
            compiled_context_fingerprint=valid.compiled_context_fingerprint,
        ),
        source_refs=(),
        decision_refs=(),
    )

    class StaticCompiler:
        def compile(self, _record: ContextPackRecord) -> CompiledContext:
            return compiled

    result = _resolve(_record(), compiler=cast(ContextCompiler, StaticCompiler()))

    assert isinstance(result, ContextBindingRefusal)
    assert result.code is ContextBindingRefusalCode.UNAVAILABLE
    assert HostileString.equality_calls == 0
    assert HostileString.encode_calls == 0
    assert "secret hostile" not in str(result.as_dict())


@pytest.mark.parametrize(
    "field_overrides",
    [
        {"context_pack_revision": object()},
        {"compiler_version": "x" * 129},
        {"compiled_context_fingerprint": object()},
        {"compiled_context_fingerprint": "A" * 64},
    ],
)
def test_binding_fields_require_owned_bounded_plain_shape(
    field_overrides: dict[str, object],
) -> None:
    valid = ContextCompiler().compile(_record())
    values: dict[str, object] = {
        "context_pack_id": PACK_ID,
        "context_pack_revision": REVISION_1,
        "compiler_version": valid.binding.compiler_version,
        "compiled_context_fingerprint": valid.compiled_context_fingerprint,
    }
    values.update(field_overrides)
    binding = ContextPackBinding(**values)  # type: ignore[arg-type]
    compiled = CompiledContext(
        text=valid.text,
        size_bytes=valid.size_bytes,
        binding=binding,
        source_refs=(),
        decision_refs=(),
    )

    class StaticCompiler:
        def compile(self, _record: ContextPackRecord) -> CompiledContext:
            return compiled

    result = _resolve(_record(), compiler=cast(ContextCompiler, StaticCompiler()))

    assert isinstance(result, ContextBindingRefusal)
    assert result.code is ContextBindingRefusalCode.UNAVAILABLE
    assert result.remediation == "Inspect the Context Pack and select a valid exact revision."


def test_binding_subclass_hostile_property_access_is_sanitized() -> None:
    class HostileBinding(ContextPackBinding):
        def __getattribute__(self, name: str) -> object:
            if name in {
                "context_pack_id",
                "context_pack_revision",
                "compiler_version",
                "compiled_context_fingerprint",
            }:
                raise RuntimeError("secret binding property detail")
            return super().__getattribute__(name)

    valid = ContextCompiler().compile(_record())
    hostile = object.__new__(HostileBinding)
    compiled = CompiledContext(
        text=valid.text,
        size_bytes=valid.size_bytes,
        binding=hostile,
        source_refs=(),
        decision_refs=(),
    )

    class StaticCompiler:
        def compile(self, _record: ContextPackRecord) -> CompiledContext:
            return compiled

    result = _resolve(_record(), compiler=cast(ContextCompiler, StaticCompiler()))

    assert isinstance(result, ContextBindingRefusal)
    assert result.code is ContextBindingRefusalCode.UNAVAILABLE
    assert "secret binding property detail" not in str(result.as_dict())


def test_binding_subclass_is_reconstructed_as_owned_plain_binding() -> None:
    class BindingSubclass(ContextPackBinding):
        pass

    valid = ContextCompiler().compile(_record())
    compiled = CompiledContext(
        text=valid.text,
        size_bytes=valid.size_bytes,
        binding=BindingSubclass(
            context_pack_id=PACK_ID,
            context_pack_revision=REVISION_1,
            compiler_version=valid.binding.compiler_version,
            compiled_context_fingerprint=valid.compiled_context_fingerprint,
        ),
        source_refs=(),
        decision_refs=(),
    )

    class StaticCompiler:
        def compile(self, _record: ContextPackRecord) -> CompiledContext:
            return compiled

    result = _resolve(_record(), compiler=cast(ContextCompiler, StaticCompiler()))

    assert isinstance(result, ContextBinding)
    assert type(result.binding) is ContextPackBinding
    assert result.compiled_context_fingerprint == valid.compiled_context_fingerprint


@pytest.mark.parametrize("failure", ["invalid_utf8", "fingerprint_mismatch"])
def test_compiler_result_utf8_and_fingerprint_failures_are_sanitized(failure: str) -> None:
    valid = ContextCompiler().compile(_record())
    if failure == "invalid_utf8":
        compiled = CompiledContext(
            text="secret invalid surrogate \ud800",
            size_bytes=1,
            binding=valid.binding,
            source_refs=(),
            decision_refs=(),
        )
    else:
        compiled = CompiledContext(
            text=valid.text,
            size_bytes=valid.size_bytes,
            binding=ContextPackBinding(
                context_pack_id=PACK_ID,
                context_pack_revision=REVISION_1,
                compiler_version=valid.binding.compiler_version,
                compiled_context_fingerprint="0" * 64,
            ),
            source_refs=(),
            decision_refs=(),
        )

    class StaticCompiler:
        def compile(self, _record: ContextPackRecord) -> CompiledContext:
            return compiled

    result = _resolve(_record(), compiler=cast(ContextCompiler, StaticCompiler()))

    assert isinstance(result, ContextBindingRefusal)
    assert result.code is ContextBindingRefusalCode.UNAVAILABLE
    assert result.remediation == "Inspect the Context Pack and select a valid exact revision."
    assert "secret invalid surrogate" not in str(result.as_dict())


def test_authorization_exception_fails_closed_without_compiling() -> None:
    def refused(_record: ContextPackRecord) -> bool:
        raise RuntimeError("secret-authorization-detail")

    result = ContextBindingResolver().resolve(
        ContextBindingRequest.accumulated(
            context_pack_id=PACK_ID,
            context_pack_revision=REVISION_1,
        ),
        load_exact=lambda _pack_id, _revision: _record(),
        authorize_exact=refused,
    )

    assert isinstance(result, ContextBindingRefusal)
    assert result.code is ContextBindingRefusalCode.UNAUTHORIZED
    assert "secret-authorization-detail" not in str(result.as_dict())


def test_lookup_exception_fails_closed_without_echoing_source_detail() -> None:
    def unavailable(_pack_id: str, _revision: str) -> ContextPackRecord | None:
        raise RuntimeError("secret-storage-detail")

    result = ContextBindingResolver().resolve(
        ContextBindingRequest.accumulated(
            context_pack_id=PACK_ID,
            context_pack_revision=REVISION_1,
        ),
        load_exact=unavailable,
        authorize_exact=lambda _record: True,
    )

    assert isinstance(result, ContextBindingRefusal)
    assert result.code is ContextBindingRefusalCode.UNAVAILABLE
    assert "secret-storage-detail" not in str(result.as_dict())


def test_later_pack_revision_cannot_mutate_existing_binding() -> None:
    store = {REVISION_1: _record()}
    resolver = ContextBindingResolver()
    bound = resolver.resolve(
        ContextBindingRequest.accumulated(
            context_pack_id=PACK_ID,
            context_pack_revision=REVISION_1,
        ),
        load_exact=lambda _pack_id, revision: store.get(revision),
        authorize_exact=lambda _record: True,
    )
    assert isinstance(bound, ContextBinding)
    original_bytes = bound.compiled_context_bytes
    original_fingerprint = bound.compiled_context_fingerprint

    store[REVISION_1] = _record(revision=REVISION_2, summary="Later baseline")

    assert bound.compiled_context_bytes == original_bytes
    assert bound.compiled_context_fingerprint == original_fingerprint
    with pytest.raises(FrozenInstanceError):
        bound.compiled_context_bytes = b"changed"  # type: ignore[misc]


def test_same_baseline_can_feed_two_plans_while_role_instruction_stays_separate() -> None:
    binding = _resolve(_record())
    assert isinstance(binding, ContextBinding)
    builder_plan = LaunchPlan(
        profile_id=BuiltinProfileId.CODEX_BUILDER,
        purpose=LaunchPurpose.IMPLEMENTATION,
        instruction="Implement the bounded change.",
    )
    reviewer_plan = LaunchPlan(
        profile_id=BuiltinProfileId.CODEX_INDEPENDENT_REVIEWER,
        purpose=LaunchPurpose.INDEPENDENT_REVIEW,
        instruction="Review the exact revision independently.",
    )

    assert builder_plan.instruction != reviewer_plan.instruction
    assert binding.compiled_context_bytes is not None
    assert builder_plan.instruction.encode() not in binding.compiled_context_bytes
    assert reviewer_plan.instruction.encode() not in binding.compiled_context_bytes


def test_binding_surface_contains_no_instruction_authority_or_mutable_state() -> None:
    binding = _resolve(_record())
    assert isinstance(binding, ContextBinding)

    assert {field.name for field in fields(binding)} == {
        "binding",
        "compiled_context_bytes",
    }
    assert not hasattr(binding, "__dict__")
    assert not hasattr(binding, "as_dict")
    forbidden = ("instruction", "transcript", "reasoning", "authority", "session", "state")
    assert all(token not in field.name for field in fields(binding) for token in forbidden)


def test_binding_rejects_tampered_compiled_bytes() -> None:
    compiled = ContextCompiler().compile(_record())

    with pytest.raises(ValueError, match="fingerprint"):
        ContextBinding(
            binding=compiled.binding,
            compiled_context_bytes=compiled.text.encode("utf-8") + b"tampered",
        )


def test_request_rejects_hidden_or_incomplete_selection() -> None:
    with pytest.raises(ValueError, match="fresh context"):
        ContextBindingRequest(
            mode=ContextBindingMode.FRESH,
            context_pack_id=PACK_ID,
            context_pack_revision=REVISION_1,
        )
    with pytest.raises(ValueError, match="one exact"):
        ContextBindingRequest(
            mode=ContextBindingMode.ACCUMULATED,
            context_pack_id=PACK_ID,
        )
    with pytest.raises(ValueError, match="typed pack"):
        ContextBindingRequest.accumulated(
            context_pack_id="context_pack.invalid",
            context_pack_revision=REVISION_1,
        )
