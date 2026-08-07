"""A deterministic provider double for harness-contract tests.

It accepts only catalog case identities.  It intentionally cannot receive a
prompt, response, transcript, secret, or arbitrary command.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from agent_commons.errors import ValidationError

from .model import EvalCase, EvalStatus


@dataclass(frozen=True)
class FakeProviderTrace:
    """Safe trace metadata; action names are a fixed, low-cardinality vocabulary."""

    case_id: str
    action_codes: tuple[str, ...]
    terminal_code: str

    @property
    def digest(self) -> str:
        value = "\n".join((self.case_id, *self.action_codes, self.terminal_code)).encode("utf-8")
        return hashlib.sha256(value).hexdigest()


class DeterministicFakeProvider:
    """Produces a stable trace used to test the eval harness, not a real model."""

    def run(self, case: EvalCase) -> FakeProviderTrace:
        if case.status is not EvalStatus.IMPLEMENTED:
            raise ValidationError("fake provider runs implemented cases only")
        return FakeProviderTrace(
            case_id=case.case_id,
            action_codes=("isolated_fixture", "deterministic_grader"),
            terminal_code="completed",
        )
