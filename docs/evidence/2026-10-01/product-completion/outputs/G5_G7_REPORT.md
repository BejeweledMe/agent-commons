# G5/G7 integration handoff

Frozen implementation copy: `/private/tmp/commons-product-outputs-20261001`.
Main checkout, main ledger, installed MCP, credentials, provider profiles and
shipped assets were not changed by this worker. Dependencies were copied locally;
Python tests used the main interpreter read-only with this copy's `src` on
PYTHONPATH and inherited Commons state/session variables removed.

Use `G5_G7.patch` for source integration, not wholesale copies of overlapping
files. `G5_G7_INTEGRATION.json` lists all 27 paths, original SHA256 and final SHA256.
`BASE_HASHES.json` preserves the full initial source/docs/test boundary. No
manager.py or task hierarchy/TaskGraph changes are included. Potential G6 merge
seams: mcp/server.py, frontend/work/src/api.ts, i18n.json, docs/PROTOCOL.md,
FRONTEND_CONTRACT.md, ADR index and user guide append sections.

Implemented:

- Images are retained before canonical publication in local SHA256 content
  storage; overwritten/deleted sources and manager restart preserve earlier bytes.
- Static dist builds have bounded deterministic ZIP+file manifest retention and
  an exact, authenticated/scoped ZIP attachment endpoint. Gallery has Builds,
  retained-byte labels and explicit Download build action with hash/freshness
  verification. HTML is never executed on the Commons origin.
- Retention is bounded by 512 objects/256 MiB without eviction, guarded by its own
  process flock and per-root RLock. Publication lock order is canonical→content.
  Atomic-link interruption repairs only an identical verified partial object;
  unrelated partials remain quota charged. Corrupt/missing/symlink bytes refuse.
- Exact result review reuses artifact revision targets. It never inherits current
  task review or another result version's approval. Independent delegated approval
  is displayed only after a matching successful terminal outcome.
- Independent reviewers receive actual scope-frozen image pixels or bounded
  verified UTF-8 ZIP entries. Manifest-only reads and gaps cannot satisfy approval
  evidence; all text entries require full coverage. Finalizer, independence and
  result bindings remain existing manager lifecycle rules. Real request→read→
  finalize→delegation.succeeded is tested after original source files are deleted.
- EN/RU technical disclosure supplies an exact artifact review CLI operation with
  stable idempotency key and explicit project/session/state context. UI currently
  launches task review only; there is no inert result-review button.

Checks passed:

- 87 targeted Python tests across design publication, outputs, authenticated
  output routes, artifact previews and documentation map.
- 89 tests across publication, provider profile policy and provider-visible
  delegation instructions (overlaps previous coverage).
- Final publication suite: 46 tests; final source-deletion reviewer/atomic-link/
  mixed-build capture regression selection: 4 tests.
- Work outputs harness: 33 tests; Work TypeScript noEmit check, Node v24.20.0.
- Ruff check and format check for all 14 changed Python files.
- Existing MCP worker scope suite passed after adding the two exact reviewer read
  tool names; existing Codex/Claude/Grok provider allowlist tests passed.

Remaining boundaries:

- This is durable local retrieval, not a managed hosted preview. Download/extract
  and explicit local serving remain operator actions. Dynamic execution, visual
  build testing and a credential-isolated managed preview host are separate work.
- Build review renders no binary assets; hashes verify them, and text inspection
  does not imply execution or visual quality. Verdict criteria/summary must state
  this. Image reviews do receive actual PNG/JPEG MCP pixels.
- Backups must preserve state/output-content with the ledger metadata; historical
  metadata-only missing bytes cannot be recovered from hashes. No automatic GC or
  quota eviction exists.
- Three MCP additions alter the next model/profile contract: implementation and
  verification `commons_publish_static_build`; independent-review
  `commons_read_output_image`, `commons_read_build_file`. The independent review
  instruction fingerprint intentionally changes under ADR 0025. Root must rebuild
  UI assets, run full make check, reinstall installed MCP and re-canary the exact
  next profile/model/source contract, then obtain independent review.
- Root should add the combined CHANGELOG entry and coordinate G6/WP20 merges.
  Human product acceptance remains separate.
