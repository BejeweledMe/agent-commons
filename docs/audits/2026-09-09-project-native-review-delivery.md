# Project-native collaboration: review and delivery checkpoint

Date: 2026-09-09. Branch: `codex/service-self-improvement`.
Base: `e08488f392936c43d35f1013d6468fe45fa89b00`.

## Task review boundary

The operator explicitly authorized completing implementation and submitting all
nine wave tasks for independent review. All nine are now in `review`; none was
accepted by this operation. The original source boundary is
[the exact manifest](../evidence/2026-09-08/project-native-final-source-manifest.json),
SHA256 `ed1eeea12802af184fddaf4ed2867cbadcec9347068a0575e3b7212460656e84`.
Its 165 entries were independently verified. Later corrective source changes do
not retroactively change that evidence or promote historical approvals.
The original bytes are durably retrievable from Git commit
`405d8c690d3c7e33fedb63828db9d30e6a6185e9`; that commit contains the matching
manifest and does not contain the later runtime-refresh correction.
The [corrective delta manifest](../evidence/2026-09-08/project-native-corrective-source-manifest.json)
inherits that immutable commit and records only subsequent changed/deleted files,
avoiding another duplicate full-source inventory.

| Work | Task | Independent review request |
| --- | --- | --- |
| R0: Grok transport | `task.6J4R6XAAC6D0R6CJKBRR48NDXY` | `review.5Y3QKKDRHT0FKNVTK0A7E1R994` |
| R1: Project folders | `task.0J1BKNDK3TP58RQA96G4CBRE0H` | `review.52FW46YYMMQ8P4K33RM9QBBBQX` |
| R2: Library contracts | `task.6XMT2PEZS5SDWBE3ZB8ES1S95G` | `review.3YEBV5ZQD4F56FK6MX6GTBJ252` |
| R3A: Project shell | `task.4Q1WTPAP884D1JAG5GX8Y1MWQB` | `review.1CH1SRNRAS22JHJN8G78BD49RV` |
| R3B: Library UI | `task.4V87W14RHVYYX86591MAPN5CSN` | `review.4QPDWSJNSWTD0DSCXA87DG9M3A` |
| R4: Producer results | `task.591KMGYHCAEHMAJC2WZ2ET1V3H` | `review.6A96QW8D7HZ26EG6XAZ8MSE7SQ` |
| R5: Private delivery | `task.3SDZD4Z61PCKERKT09PFYE8MMY` | `review.50FYAXT2W7MPAV7RF9V2FCD9B6` |
| R6: Conversations UI | `task.3DX84XZHS5VSK4D7S98GPZT5NM` | `review.11WPVEKZ2WXEC87YPGGQ2VSDH6` |
| R7: Projection reads | `task.7V9NEEMZ8QPVZH4EYZDDTY722X` | `review.1HKAKRDKZMTHB2PYZ2EA2ZSM3Y` |

Eight original-boundary judgments were eventually approved; R6 remains pending.
The R5 judgment is explicitly contradicted by the later lock-open reproduction
below and must not support acceptance. The operator explicitly authorized reopening R5/R6 and returning them to review.
The partial lifecycle script was rejected by automatic approval review because
it ended in `active`; a complete review-to-review cycle with successor evidence
is being prepared. Their existing `review` states were not changed at this checkpoint.
Actual Claude review `review.47ZYFQ6GJ3X3GP2TCACTDGAH8V` approved the inspected
backend and evidence scope. Its bounded reader could not inspect the entire MCP
server and frontend; supplementary independent reviews covered those gaps.
Neither model agreement nor successful provider termination constitutes task
acceptance or release approval.

Settled read-only doctor and receipt status reported 3230 events and matching
receipts, no integrity issues and no orphan receipts. An earlier orphan warning
was an in-flight reservation that settled normally; no repair was performed.
Historical advisory warnings remain distinct from integrity failures.

## Corrective finding

Independent source review found a P2 race in runtime setup. A successful POST
ended the busy state before its refresh completed. A second Configure action
cancelled that refresh, received the definite `setup_configured` refusal, and
offered another write retry while the UI retained the old setup snapshot.

The correction handles only `runtime` plus typed HTTP409 `setup_configured`.
It starts a replacement GET and returns false for the refused POST. A failed
GET offers read-only recovery. A nullable notice fallback avoids inventing a
success notice, and project/generation guards discard responses from an old
project. The backend refusal occurs before configuration writes.

Independent review approved this corrective scope at these SHA256 values:

- `frontend/work/src/main.tsx`:
  `3ceca77b409e05ef279fa54304d2a307a154245f8c5d15fa37a9e49352262dbd`.
- `frontend/work/tests/confirmed-refresh.test.mjs`:
  `04244ab4e461461133803adf5a7dc4389bf6b2fc8b1e5056c432273b6d3e756e`.

All 224 Work tests passed on Node24, with no failures or skips. Five new
regressions cover the cancelled refresh, read-only recovery, honest notices,
project switching and unrelated refusals. The clean wheel contains exactly the
current Work index, CSS and `work-DV06NGBp.js`; all three matched source bytes.
The first corrective full `make check` failed: one failure, 2585 passes and
13 documented skips in 1076.77 seconds. The failure exposed a separate runtime
defect, rather than a frontend regression or a test timeout.

## Concurrent delivery lock correction

Four synchronized writers reproduced `ENOENT` while opening `receipts.lock`
with a shared create-or-open operation. The independent reproduction ran outside
pytest and failed on cycle 3 in 0.97 seconds. The pinned directory remained
linked; its device/inode matched the existing path. A pytest-cleanup explanation
is therefore excluded for that reproduction. The underlying kernel cause is
not established, and loss of previously confirmed updates was not demonstrated.

The fix atomically creates the lock with `O_CREAT|O_EXCL`. Only `FileExistsError`
permits a subsequent open of the existing file without `O_CREAT`. Other failures,
including `ENOENT`, still fail closed. Symlink, file type, owner, hardlink, root
identity and post-flock inode checks remain in place.

Independent corrective review approved the following exact bytes:

- `src/agent_commons/runtime/message_delivery.py`:
  `1cea1d8f3f71cf98a4909bafa5202170cd6f56f117ac5f8540332f4285bbff20`.
- `tests/runtime/test_message_delivery.py`:
  `9d81cfab61e68bffab04ba737a99212ed25566ab67eff0df0292f4041e38d75b`.

The identical synchronized stress then passed 100 cycles, 400 processes and
2000 updates in 31.99 seconds. All 44 module tests passed independently, including
the strengthened simultaneous-first-write test and missing-file refusal checks.
Finding `finding.19V1E5CR2WZ5EBQ9E4GQVTFMMH` preserves the historical defect and
the R5 review caveat. No finding promotion or task acceptance is implied.

The updated installed wheel matches all 191 Python source files and all three
Work assets. Its Python fingerprint is
`88531e7f103f226cd653af465ceac9d8264f0b9e38d939e808cb41079e0ddf61`.
UI and MCP imports passed, and the owned UI was stopped and restarted on port8787.
An independent isolated Work rebuild reproduced the exact file set and bytes of
all three installed assets. The 611065-byte JS bundle produces a nonblocking
size warning; future code splitting remains a performance opportunity.
All six fresh Codex/Claude/Grok builder and independent-reviewer canaries passed
at this fingerprint. These are bounded process/protocol qualifications, not
proof of every native shell or media capability. All child sessions closed.

| Profile | Canonical outcome | Terminal completions/rejections | Process duration |
| --- | --- | --- | --- |
| `claude-builder` | `succeeded` | 1/0 | 48.58 s |
| `claude-independent-reviewer` | `succeeded` | 1/0 | 28.22 s |
| `codex-builder` | `succeeded` | 1/0 | 74.19 s |
| `codex-independent-reviewer` | `succeeded` | 1/0 | 38.92 s |
| `grok-builder` | `succeeded` | 1/0 | 104.53 s |
| `grok-independent-reviewer` | `succeeded` | 1/0 | 58.86 s |

A fresh actual Codex media run at the same source fingerprint also succeeded:
image and text-file bytes fetched through MCP, message acknowledged, both answer
checks correct, one source-matching result under the addressed task and agent,
and zero results under unrelated task/agent controls. The supplied synthetic
image was published; this run does not claim provider-generated pixels. It had
one terminal completion and zero rejections, and its disposable workspace was
removed. An earlier harness call refused before provider work because its
expected source SHA was stale; the explicit expected SHA was updated before the
successful attempt. No raw provider output or private source bytes were retained
in this audit. The full corrected `make check` passed: Ruff, 224 Work tests,
27 Gallery tests and 2589 Python tests, with 13 documented skips and two existing
warnings, in 1084.39 seconds for pytest. The skips comprise twelve withheld
automatic-grant cases and one CI-environment-only check. `git diff --check` passed.
The earlier failed run remains preserved above; it is not relabeled as a flake.


## Additional installed-browser verification

These journeys used the installed preceding `work-cJnL5_T-.js` asset; they do
not claim a browser reproduction of the setup race or its correction.

- Enter opened the native project menu; Tab moved focus to Rename; Enter
  opened its form and focused the input. Back cancelled without writing.
- Enter opened and closed the Product and design skill group.
- English and Russian Library layouts were visually inspected with a temporary
  narrow viewport override. The observed CSS width and scroll width were both
  354px, with no horizontal page overflow. The override was reset afterward.

This closes those bounded keyboard/layout checks, not a screen-reader or full
mobile certification. Native folder chooser selection and browser file-chooser
upload remain unverified; manual folder paths and actual provider media
consumption have separate evidence in the prior validation record.

After the lock correction was installed, the actual main-project browser loaded
`work-DV06NGBp.js`. The populated tracker showed 272 tasks with connected automatic
updates. Filtering and keyboard selection opened R5's review-state inspector.
The broad historical graph intentionally falls back to filtering/list guidance;
a focused nine-task wave view is not yet a saved navigation preset.

## Remaining gates and follow-up

- Bind the passed corrective full gate to the final exact source/asset delta.
- Complete the remaining independent judgments with an immutable, retrievable
  original source boundary; preserve the corrective review separately.
- Commit/push/merge only through the previously authorized Git delivery flow
  and verify CI. This checkpoint records no merge or release.
- Keep Linux L1 and release R2 open. macOS builder/reviewer canaries passed for
  Codex, Claude and Grok, but native build-command qualification for Claude/Grok
  is a separate capability and remains limited.
- Prioritize populated-ledger mutation latency: fast cached reads do not prove
  fast task lifecycle writes. This wave's nine review submissions were slow.
- Add an explicit empty custom-group removal/archive operation in a follow-up;
  group create/rename currently has no symmetric removal UI.
- Preserve immutable audit history and useful evidence during cleanup. Remove
  only validated disposable generated caches/build outputs.
