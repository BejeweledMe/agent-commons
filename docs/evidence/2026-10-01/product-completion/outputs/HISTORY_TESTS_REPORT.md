# Generated image history tests: legacy and retained contracts

The root merged makecheck log at
`/private/tmp/commons-product-integration-20261001/make-check.log` reported four
failures in the original five-case history file: the fixture now used the retained
image publisher, while mutation assertions still required source-only previews.
Those assertions remain correct for legacy metadata, and incorrect for retained
results. Production source is unchanged by this correction.

This one-file test increment models legacy publication through supported
`register_artifact` with the exact original five metadata fields and generated
idempotency-key derivation. It does not remove retention from an immutable modern
manifest or mock byte verification. Retained publication still uses the actual
worker MCP publisher. Both storage contracts run through completed/submitted task
history, authenticated HTTP preview, task/agent output listings, wire retained
flags, source overwrite/deletion and manager reopen. Legacy source mutations must
still refuse; retained preview bytes remain the originally published bytes.

Metadata-only summaries still must not construct a byte reader. Current
classification and exact producer restrictions run against both contracts.
Restoring an older source never substitutes an older version for latest: legacy
latest stays unavailable, retained latest remains hash-verified. Two additional
retained cases remove or tamper with the latest CAS object while its matching
source and an older retained object remain valid. Tampering preserves file length
to exercise digest verification. After reopening, task and agent listings keep the
exact latest identity unavailable, the older history remains verifiable, direct
latest preview refuses, and direct older preview returns its exact original
bytes. There is no source or older-version fallback.

Validation: all 16 history cases passed (27.64s). The combined G5-focused set
passed 147 tests (139.82s): `tests/security`, history, generated publication/MCP
reviewer tests, worker scope, and output routes. One existing Starlette/httpx
warning. Ruff check and format check passed for the changed test file.
`git apply --check` passed against the preserved baseline. Copied production src
hashes were checked against `FROZEN_SOURCE_HASHES.json` and remain unchanged.

Only `tests/mcp/test_generated_output_history.py` belongs to
`HISTORY_TESTS.patch`; its exact main base and final hash are in
`HISTORY_TESTS_INTEGRATION.json`. Work was isolated in
`/private/tmp/commons-output-history-tests-20261001`, copied from current merged
main. No main source, real ledger, assets, dependency installation, provider or
installed MCP changes were made. Earlier G5 and reconnect temp copies/patches
remain frozen. This repairs test coverage for the accepted retention contract;
full merged makecheck and browser/provider gates remain root integration work.
