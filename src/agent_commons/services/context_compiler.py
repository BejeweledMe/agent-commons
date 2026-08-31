"""Deterministic, bounded compiler for one exact Context Pack revision."""

from __future__ import annotations

from dataclasses import dataclass

from agent_commons.core.canonical import canonical_json_bytes, sha256_bytes
from agent_commons.domain.context_pack import (
    ContextPackBinding,
    ContextPackRecord,
    ContextPackRefusal,
    ContextPackRefusalCode,
    RevisionBoundRef,
)

CONTEXT_COMPILER_VERSION = "context-pack-compiler.v1"
MAX_COMPILED_CONTEXT_BYTES = 65_536


@dataclass(frozen=True)
class CompiledContext:
    """Ephemeral compiler output; callers must not persist ``text``."""

    text: str
    size_bytes: int
    binding: ContextPackBinding
    source_refs: tuple[RevisionBoundRef, ...]
    decision_refs: tuple[RevisionBoundRef, ...]

    @property
    def compiled_context_fingerprint(self) -> str:
        return self.binding.compiled_context_fingerprint


class ContextCompiler:
    """Compile one deeply frozen record without clocks, I/O, or provider state."""

    def __init__(
        self,
        *,
        compiler_version: str = CONTEXT_COMPILER_VERSION,
        max_bytes: int = MAX_COMPILED_CONTEXT_BYTES,
    ) -> None:
        if not compiler_version.strip():
            raise ValueError("compiler_version must be non-empty")
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        self.compiler_version = compiler_version
        self.max_bytes = max_bytes

    def compile(self, record: ContextPackRecord) -> CompiledContext:
        lines = [
            "# Agent Commons Context Pack",
            f"Compiler: {self.compiler_version}",
            f"Pack: {record.context_pack_id}",
            f"Revision: {record.revision}",
            "",
            "## Summary",
            _render_string(record.draft.summary),
            "",
            "## Facts",
        ]
        if record.draft.facts:
            for index, fact in enumerate(record.draft.facts, start=1):
                lines.append(f"{index}. {_render_string(fact.statement)}")
                lines.append(
                    "   Sources: "
                    + canonical_json_bytes([ref.to_payload() for ref in fact.source_refs]).decode(
                        "utf-8"
                    )
                )
        else:
            lines.append("None.")
        lines.extend(("", "## Decisions"))
        if record.draft.decision_refs:
            for index, ref in enumerate(record.draft.decision_refs, start=1):
                lines.append(f"{index}. " + canonical_json_bytes(ref.to_payload()).decode("utf-8"))
        else:
            lines.append("None.")
        lines.extend(("", "## Open questions"))
        if record.draft.open_questions:
            for index, question in enumerate(record.draft.open_questions, start=1):
                lines.append(f"{index}. {_render_string(question)}")
        else:
            lines.append("None.")
        text = "\n".join(lines) + "\n"
        encoded = text.encode("utf-8")
        if len(encoded) > self.max_bytes:
            raise ContextPackRefusal(
                ContextPackRefusalCode.OVERSIZED,
                "The compiled Context Pack exceeds the configured byte limit.",
                "Publish a smaller Context Pack revision and select that exact revision.",
            )
        fingerprint = sha256_bytes(self.compiler_version.encode("utf-8") + b"\0" + encoded)
        return CompiledContext(
            text=text,
            size_bytes=len(encoded),
            binding=ContextPackBinding(
                context_pack_id=record.context_pack_id,
                context_pack_revision=record.revision,
                compiler_version=self.compiler_version,
                compiled_context_fingerprint=fingerprint,
            ),
            source_refs=record.draft.source_refs,
            decision_refs=record.draft.decision_refs,
        )


def _render_string(value: str) -> str:
    """Render text as a canonical JSON string so newlines cannot forge sections."""

    return canonical_json_bytes(value).decode("utf-8")
