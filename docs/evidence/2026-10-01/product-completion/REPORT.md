# Product completion — 1 October 2026

Status: implementation integrated and full make check passed. Canonical
acceptance is recorded separately against this frozen report. No cross-platform
release is declared.

## Delivered behavior

- An agent’s name/avatar opens that producer’s personal gallery. Image/build
  type filters, current/all versions, provenance and the task link are available.
- Published images retain immutable bytes after source replacement, deletion and
  restart. Static frontend builds are bounded ZIPs with a manifest, downloadable
  for local use. Missing/tampered retained content fails honestly; the old source
  or another version is never silently substituted. Quotas refuse new content
  rather than evicting prior results.
- Exact image/build review is separate from current task review. A newer version
  never inherits an older approval. Scoped reviewers see image pixels and must
  read all UTF-8 build content; listing a manifest alone cannot finalize review.
- The task map has distinct Structure and Prerequisite views. Project/component/
  task hierarchy is explicit, editable and guarded against cycles, orphaning and
  correction-based escapes. Search preserves ancestors; zoom, focused branches,
  keyboard navigation and a list alternative support large projects.
- Successful task creation clears the confirmed draft, while concurrent edits
  remain intact. Reopening Work after server restart recovers from a stale API
  prefix using a fresh login link without clearing browser storage.
- Implementation workers receive a trusted paired Commons CLI and explicit
  repository, exact state-root and child-session context. A user’s login-shell
  environment can no longer redirect those generated startup commands.

## Source and verification boundary

Final Python source fingerprint:
`bcb3d97817cc67d38e07bb7324336ff1d4b2c202017c297cf3087b0afe1d66a3`.
Work assets: `work-C0nYR3qF.js`, `work-CUlkmsY2.css`.
Gallery assets: `gallery-Djtn_TWU.js`, `gallery-C4hvTKLN.css`.
Existing user changes were retained. Nothing was staged, committed, pushed or deployed.

The installed MCP/CLI exactly matches the checkout and its six generated UI
files. The final wheel was separately installed; all 684 package files match
checkout and installed bytes, and both CLI entry points start successfully.
[Installed runtime](qualification/final-runtime-install-verification.json),
[wheel parity](qualification/wheel-final-parity.json).

The local operator configuration now pins `gpt-6-astra` and the permanently
installed CLI 0.159.3 in both Codex profiles. Only those four executable/model
fields changed; other settings and credentials are unchanged. The previous
configuration is backed up and hash-verified for rollback. Existing Claude and
Grok roles retain their profiles. [Pin verification](qualification/permanent-profile-pin.json).

Both final real Astra canaries use Codex CLI 0.159.3 and this final Python source:
builder 62.07 s, independent reviewer 67.72 s. Each has exactly one terminal call,
one completion, zero rejection, and a closed child session. These are compatibility
probes, not comparative model-quality or cost measurements.
[Builder](qualification/final-canary-codex-builder-bound-startup.json),
[reviewer](qualification/final-canary-codex-reviewer-bound-source.json).
Prior failed attempts remain in qualification evidence; they are not hidden by
these successful runs. The startup fix has independent review and 136 targeted
implementation tests plus 95 independently run checks.
[Startup review](worker-startup/independent-review.md).

Full `make check` passed: 2,852 Python tests, 429 Work tests and 27 Gallery tests;
Ruff lint and format checks passed. Fourteen Python skips are intentional and
two dependency deprecation warnings remain. The large benchmark was independently
run outside the opt-in default test. Earlier failing runs and the repairs remain
visible in the [validation summary](make-check-summary.json).

Existing Claude/Grok profiles were also requalified on the final source. The
Claude builder needed a diagnostic attempt after a timeout and completed through
scoped MCP after two native-shell refusals; those limitations are retained in
[provider evidence](qualification/other-providers.md). All six current local
profile receipts are qualified; this is not a Linux or reliability benchmark.

## Actual product paths

[Chrome evidence](browser/REPORT.md) covers personal galleries, retained versions,
restart, downloaded ZIP hashes, EN/RU, 320 CSS-pixel layouts, graph relationships,
parent edit/restore and keyboard use. The
[composer regression](browser/composer-addendum.md) was reproduced, fixed and
verified through both executable handler tests and real UI creation.
The exact downloaded ZIP was additionally [opened and interacted with locally](browser/downloaded-build-open.md).
These fixtures are explicitly synthetic. The separate
[real Astra frontend pilot](../council/after-login/product-pilot/REPORT.md)
contains actual worker-created frontend and published image results.

Two actual Astra artifact reviews on the immediately preceding `9b3368df…`
source approved image v2 and the static build after reading pixels/all text.
Image v1 remains unreviewed and task review remains requested. The final startup
patch does not alter reviewer instructions. These are exact-artifact judgments,
not claims of automatic task acceptance or browser execution by that reviewer.
[Canonical artifact evidence](artifact-reviews/REPORT.md).

## Independent reviews and repaired findings

- G5/G7: immutable retention, build validation, scoped content review and legacy
  retry were reviewed independently. A quadratic policy scan and historical
  identical-retry conflict were fixed and rechecked.
- G6: hierarchy invariants, semantic floor, legacy wire shape and composer behavior
  were independently reviewed. Existing golden fixtures were preserved.
- Reconnect: independent adversarial checks cover 401/404, malformed setup,
  transport failures and valid entity-level 404 without weakening server auth.
- WP-20: own-append validation/RLock and subsequent read reductions were reviewed
  with integrity, tamper, CAS and concurrency checks. No persistent trust cache
  or weaker validation was introduced.

Evidence directories: [outputs](outputs/), [hierarchy](hierarchy/),
[reconnect](reconnect/), [WP-20](wp20/), [worker startup](worker-startup/).
Canonical task reviews will bind this report and the final manifests before
acceptance; independent prose reviews alone do not silently accept a task.

## Remaining boundaries

1. WP-20’s engineering target of at most one second is unmet. Grok independently
   measured six large-fixture operations at 8.6–13.3 seconds on an earlier
   increment. Later shared-host samples are approximately 11–12 seconds with
   no matched baseline, so no extra speedup is claimed. The follow-up profile
   isolates schema validation, policy scans, receipts and path freshness.
   [Measured next steps](wp20/followup/followup-report.md).
2. L1 requires separately provisioned authenticated Linux. No available host was
   found; the Mac Docker daemon was unavailable. This is an external release
   gate, not a failed Mac test. [Availability check](linux-gate-availability.json).
3. WP-42’s blind study with external developers needs real participants.
   Five LLM reviews do not substitute for that study.
4. The narrow-screen automated pointer click on the agent icon remains
   unverified; Enter and desktop pointer click passed. No source defect was
   reproduced. Static ZIP review does not assert visual behavior of binary assets.
5. A typed `reason_code` enumeration in the worker MCP schema is a follow-up:
   the domain enforces an enum but the tool currently advertises a string.
   The failed diagnostic attempt that exposed it is preserved; no broad schema
   change was mixed into this final qualification.

The full five-model council, project/TODO/experience audit, dissent, identity
limits, failed attempts and recommendation are in the
[council record](../council/after-login/README.md). The
[execution plan](../../../plans/2026-10-01-gpt6-and-product-completion.md)
connects this delivered package to the existing backlog without mass-accepting
historical completed tasks or rewriting decisions.
