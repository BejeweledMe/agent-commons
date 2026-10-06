# G5/G7 independent-review corrections

Scope: fixes the two P2 findings in `/private/tmp/commons-review-outputs-20261001.md`.
All source work remained in `/private/tmp/commons-product-outputs-20261001`.
No main checkout, real ledger, credentials, dependencies, installed MCP, provider,
or generated assets were changed. The original 27-file patch, integration manifest,
and report remain byte-identical. This is an incremental patch against the exact
reviewed snapshot, not a replacement for it.

## Corrections and compatibility

The global policy's bare assignment key branches now begin at token boundaries
and use possessive quantifiers (Python >=3.11 is already the project minimum).
A failed long identifier is no longer searched again from each suffix. Numeric
and punctuation prefixes preserve the old conservative `123password=x` detection;
quoted keys, values and cross-line whitespace retain their existing rules.
Both `scan` and `scan_text_lines` use the corrected patterns. This fixes the
reported bare-token quadratic case; timings are evidence for those inputs, not
a universal complexity guarantee for every other policy expression.

The build reader still scans/redacts the whole decoded entry before slicing.
It caches checked text by exact artifact + frozen manifest + entry digest for
one server session, under an RLock, limited to 128 entries and 20 MiB of text /
conservative redaction accounting. Each call still rechecks frozen artifact
binding, verifies CAS bytes and hashes the requested entry before using that
cache. Cache eviction affects only decoded text and never retained results.
Review coverage still counts character ranges of the sanitized text; gaps and
manifest-only reads cannot finalize approval. Hidden policy content remains an
explicit review limitation.

An unchanged historical image retry recognizes only the exact five-field legacy
metadata shape with valid canonical task/delegation revision bindings, unchanged
bytes/size/source/media/title/series/context and author. It returns the original
artifact event revision and `content_copied: false`; it performs no CAS put and
no history rewrite. Changed intent/provenance still conflicts. A new operation
key creates a retained version without altering the old artifact. Idempotent
entity IDs are already author-session scoped; unrelated authors cannot collide
with the original publication's ID. No schema, tool signature or role allowlist
changed in this correction.

## Validation

- 131 focused tests passed: all `tests/security`, publication/reviewer MCP tests,
  worker scope and output routes. One existing Starlette/httpx deprecation warning.
- New regressions use subprocess five-second deadlines at 65 KiB and 1 MiB;
  preserve numeric/punctuation/non-ASCII prefix credential detection; reconstruct
  multibyte character chunks; prove one whole-entry scan across chunks; refuse
  tampered retained bytes even after a cache hit; redact a cross-chunk, cross-line
  credential and finalize only honest sanitized-text coverage; preserve legacy
  retries across a newly constructed manager/tool; retain new versions under a
  new key; refuse changed bytes/title/source/producer and malformed legacy shapes.
- Ruff check and format check passed for the five changed Python files.
- Both original reviewer probe programs now pass, including the ten-second timed
  read that formerly timed out and the exact Unicode request/read/finalize flow.
- A separate deadline-bounded synthetic benchmark recorded: 65,557 UTF-8 bytes,
  three policy passes ~0.037s, first exact build read ~0.041s, cached read ~0.014s;
  1,048,598 bytes, three passes ~0.585s, first read ~0.419s, cached read ~0.016s.
  `/private/tmp/commons-output-fix-benchmark-20261001.py` and `.json` are supporting
  probes, not shipped source. Every operation was bounded by a five-second alarm.
- `git apply --check` passed against the separately preserved six-file baseline.

## Integration boundary

`G5_G7_FIX.patch` contains only these six paths (base/final SHA256 in
`G5_G7_FIX_INTEGRATION.json`):

- `src/agent_commons/security/policy.py`
- `src/agent_commons/mcp/server.py`
- `src/agent_commons/mcp/design_output_tools.py`
- `tests/security/test_policy.py`
- `tests/mcp/test_design_output_tools.py`
- `docs/PROTOCOL.md`

Apply incremental hunks, not wholesale overlapping files: main contains newer
G6/WP20 source and may have additional PROTOCOL/server edits. No manager.py,
hierarchy, frontend or generated assets occur in this patch. The untouched
original provenance files retain their historical meaning; use the new manifest
for the corrected snapshot. Full merged `make check`, independent re-review,
installed-MCP reinstall, exact provider/profile canary and browser validation
remain the root integrator's gates. No real provider was invoked here.
