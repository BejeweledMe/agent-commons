# Grok terminal outcome hardening

Generated: 2026-09-02

Task: `task.6XC7JQJMS5CFMWD1M0V3885DXA`

## Context

CentOS 9 L1 qualification on commit `5b6556db322d24d681059aa22e73d906bd8a19d6`
remained fail-closed for both Grok profiles. The diagnostic result for
`grok-builder` showed `canonical_state=succeeded` with
`terminal_tool_calls=0`, which is consistent with a native shell control-plane
bypass rather than an audited worker-MCP terminal outcome.

## Change

- Grok builder no longer grants native `run_terminal_cmd`.
- Grok native shell environment policy no longer includes
  `AGENT_COMMONS_DELEGATION_ID`, `AGENT_COMMONS_SESSION_ID`, or
  `AGENT_COMMONS_STATE_ROOT`.
- Grok version parsing accepts the observed canonical form
  `grok 1.0.13 (5e9a58528b76)` and still rejects non-version provider text.

## Verification

- Focused runtime tests:
  `uv run --locked pytest tests/runtime/test_profiles_policy.py::test_grok_profiles_build_fixed_headless_prompt_and_isolated_tools tests/runtime/test_profiles_policy.py::test_grok_profile_validation_keeps_builder_trusted_and_reviewer_read_only tests/runtime/test_provider_canary.py::test_grok_provider_version_accepts_canonical_shapes tests/runtime/test_provider_canary.py::test_grok_provider_version_drops_noncanonical_provider_content`
  passed with `11 passed`.
- Full green contract: `make check` passed with `1828 passed, 13 skipped, 2 warnings`.

## Remaining qualification boundary

This local fix does not itself claim L1 success. L1 needs a fresh CentOS 9 live
canary rerun on the commit containing this change, especially for both Grok
profiles, and that evidence must be recorded against the canonical L1 task.
