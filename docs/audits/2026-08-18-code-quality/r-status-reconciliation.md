# R-status reconciliation: A3–A8

**Observed:** 2026-08-25  
**Source baseline:** `ae258349bf4e60ded6611627f4a9c62a3d3fdfae`  
**Reconciliation task:** `task.3KS7ZAB6SMKAWHMY88PXTMHYHH`

## Purpose and method

The audit plan records A3–A8 as unfinished, but its status is no longer enough
to tell whether a slice may be started. This reconciliation compares the
projected task state and exact task revision, the Git commit cited by that task,
the commit's presence in the source baseline, and the canonical review state.

`agent-commons doctor` reported `ok: true`. For every listed A3–A7 task,
`review list` found no `review.requested` record and no verdict. A task state of
`review` therefore means **submitted work without a review request**, not work
which has been independently reviewed or accepted.

Task summaries cite a Git commit in free text but do not bind a registered
artifact revision. That is an evidence gap. Before exact review, the coordinator
must create an immutable/pinned worktree at the cited commit and bind the
reviewer to that exact source; a mutable current checkout is not an acceptable
substitute when the files have changed since the cited commit.

## Matrix

`review_request` and `review_verdict` are `null` for every row below.

| Audit slice | Task and exact revision | Source commit | Status at reconciliation |
| --- | --- | --- | --- |
| A3 roles | `task.05Q8ZW3WB18HRG5NAK8DG3MG0H` / `evt.01M0N7919M574R965RAQKTGZZ4` | `784ead96c4a32a34af82f6436b0f42598d35f00a` | `completed_unaccepted` |
| A4 MCP scoped reader | `task.1Q5SZ6GEM9YKJ00680WEJT66NB` / `evt.01M0N8AFZVA9GRPK99SGWA0PZT` | `38c8f43d9a86be1bc1dbd5145a2cee365f7759af` | `completed_unaccepted`; A4 only started |
| A4 UI reads/actions | `task.79VTZMP8A03H48E03JK4A7JH4F` / `evt.01M0N8C7WTFND7VM13Z601P4RT` | `a455200a6ca01d6fa9801f0ff12aeb90d25a7ac3` | `completed_unaccepted` |
| A4 CLI package/workspace | `task.6DVTP4864VPZ83RZY8HB4MEV25` / `evt.01M0NCW18KE5RH0A1GRTW5MWYR` | `7f91803fe7a4d5ef2440b8832fbe15a9d0d0725b` | `completed_unaccepted`; later CLI moves remain frozen |
| A4.5 instruction composition | `task.62M1D8R7GQ7X78Y3D309G031D4` / `evt.01M0N7B444VV0VZGYMTC301S8D` | `e4134a1b781a9ee6aae363b2ae6224f42c1462b8` | `completed_unaccepted` |
| A5.1 delegation/maintenance envelopes | `task.1KKE992Z8ECPFFTFKTVEJS5CXH` / `evt.01M0NZWXGGP0WQKD97VX6QVR10` | `7a3e0f7ccc107104e615b5baaa12945d6c139a36` | `completed_unaccepted` |
| A5.2 task/review envelopes | `task.4ZRNSZ33ZMKYG6DTFCD10T7SEK` / `evt.01M0P0Q85375WF28G8FF7AT63P` | `7daa8cab5d951a4889d8b77616ae97c3a7d51294` | `completed_unaccepted` |
| A5.3 thread/handoff envelopes | `task.7Z39VCTRX25HGQ84SSMXQ5PJJC` / `evt.01M0P1DP770JA650ZCHPHQ6AKR` | `42a22c9da9839e873366eb32d522c853862ebe2e` | `completed_unaccepted` |
| A5.4 truth/evidence envelopes | `task.08NM11PA5C990W8VZYVN1FANY2` / `evt.01M0P296TWEHK4J14DYQNVJMZH` | `02dcb092017cfa80c24636bde2e9796460c043e4` | `completed_unaccepted` |
| A5.5 role envelopes | `task.7BW08D22PR9591MH985JVQTX93` / `evt.01M0PQE9HY1V8RR7XJ7M4EQGAN` | `9a1beaef4b4dbe1e5e60f6f2ab2e98758cb236d2` | `completed_unaccepted` |
| A5.6 verification projection | `task.72TSQB1V8YBMMH1543S7X8AGD0` / `evt.01M0SXMGHGC5D78DED0N822MZG` | `df5ba8b5ee0718e5fd5b95195e559dfcd30ff11f` | `submitted_without_review_request` |
| A5.7 transition spec | `task.72CEA2EWRWWWTXW7MBAD4X3DJ6` / `evt.01M0T2REQWB6V20E2VPY144S38` | `d1661decc70502752941c7b95bdeaa0f3f7064a9` | `submitted_without_review_request` |
| A5 frozen review record | `task.07QF30Z1VH5SPWHK8WVX604PJ7` / `evt.01M0T967R39DT43S3A6C1J9EVR` | `6bd1dad6c724aba7cfc93ad00c0f4c34b2d2b519` | `submitted_without_review_request` |
| A5 frozen task record | `task.31ACFFPZ5J4H6HYQAVKSRJVT5T` / `evt.01M0T4CRNE02104Z5AC8RP68X5` | `b766c9a795413f2a4cbca5e15708d2628c5bbaa8` | `submitted_without_review_request` |
| A5 frozen handoff record | `task.4R62ZGCJQRWJDS10NSFJWP6NM7` / `evt.01M0TC7AV3F2MC79TXKVVPD0M4` | `20401de272590c92e777e52c4e6ae24a1fab00ea` | `submitted_without_review_request` |
| A5 frozen artifact record | `task.3TJ6T2DDKE4RVN6D087WPT436B` / `evt.01M0TDT743YMYMZKR9KTG0F2BV` | `a96b1950ddc6e39c6aacfbb99302130a240928ee` | `submitted_without_review_request` |
| A5 frozen thread record | `task.6EVFXS7WAR4A9S8RAYAT9KXVDN` / `evt.01M0TASFAMPWD39JSMXFH3C85B` | `8f597c0313f3a52e14886e12457ef935621018f2` | `submitted_without_review_request` |
| A5 frozen agent record | `task.79TWN3D9JPRDMJCFJBHH8GNQG4` / `evt.01M0T7PPAFCBWS9939WGD5HC58` | `9c16f538058adaaaa6502aac85d9a06e0aefd278` | `submitted_without_review_request` |
| A5 frozen finding record | `task.79C36BJ9PCVJETJ7SR0HK2JQHB` / `evt.01M0TKGXZC45PWYS7KX4HM8S3V` | `2367f990e53834990fd49a22d6cfe2cc3cd7ba30` | `submitted_without_review_request` |
| A5 frozen objective record | `task.221406G3FT9Z9BX6NKK4MH3DNY` / `evt.01M0TNCWRGAZ9A4EHAEW6418HY` | `cf52bf2020e592d9a9b6d07fa455ffac3db3c90b` | `submitted_without_review_request` |
| A5 frozen decision record | `task.6N1KDQH6DFB16P3VX5811DNF05` / `evt.01M0TFT80402Q8NCE1PNE4Y7XJ` | `f998e333482e203f56b162b5abea02b5769ad525` | `submitted_without_review_request` |
| A5 frozen delegation record | `task.132BM9TEYE202XX5EWHQD3S8GB` / `evt.01M0TS9663SVEBAKRAGTTDAMMC` | `4844fdbc95adc12ed9b11937d1eb6415f3fb3ba6` | `submitted_without_review_request` |
| A6.1 profile | `task.6JZP6J57782Y8ERHT2K140DJ13` / `evt.01M0PX94X1HMFR6929ZJ6ECZ9T` | `503d37faea0ef800fe8222136a7805f9b4ffe172` | `completed_unaccepted` |
| A6.2 verified-read optimisation | `task.0WHKPFJNYSQ9Y2YFFSCQEVWX98` / `evt.01M0ST7RDMQHZQGN78A47WXJHB` | `27b6eaaded0585a51e791cd8928a2cede261bcfb` | `completed_unaccepted`; permitted profiling exception |
| A7.1 UI read DTOs | `task.24GYS8JEZ01A7S5N8AB8BX30RQ` / `evt.01M0SX3ZMJTCGM43TDN0V8RPFA` | `8225f1ca2f606b6c57a0b37c74b9cf01b30e386e` | `completed_unaccepted`; UI-local only |
| A7.2 deep-freeze UI DTOs | `task.7E2F6NNNKFHZK5HDXM7F1F112H` / `evt.01M0T052B70EE52PQ3RDKVB1CJ` | `d71b066494000b5a483477e6c7f4c61fcdf46eda` | `completed_unaccepted`; UI-local only |
| A8 collaborators | no task | no commit | `ready_not_started` |

All cited commits are reachable from the source baseline. Their reachability is
not independent review evidence.

## Consequences and required order

No new structural source writer may start from the audit plan's stale checkbox.
The next permitted action is procedural and revision-bound:

1. submit A3 from `completed` and request independent review against a pinned
   worktree at `784ead96c4a32a34af82f6436b0f42598d35f00a` and the exact task
   revision `evt.01M0N7919M574R965RAQKTGZZ4`;
2. accept only after an approved, current verdict;
3. submit the remaining completed A4–A7 slices and request reviews for the
   already submitted A5 slices, each against its own cited commit; and
4. retain the audit's structure-only discipline throughout this backlog repair.

The source files of A4 UI and later A5–A7 work have changed after the cited
commits. Review in the current checkout would therefore be false exactness. Use
one pinned worktree per review batch and register/bind the checked commit before
the reviewer begins.

## First source write after the review gate

Only after the relevant early A3/A4 work is accepted, the next structural slice
is `mcp/entrypoint.py`: mechanically move `_parser` and `main` out of
`mcp/server.py`, preserving a compatibility re-export. Follow it with
`mcp/scope.py`, then thematic `mcp/tools/*` modules.

The slice must:

- add no MCP tool names, schemas, worker grants, payloads, or feature semantics;
- avoid growth of `mcp/server.py::build_server`, `CommonsManager`, and
  `UIContext`;
- preserve import compatibility for `agent_commons.mcp.server.main`;
- be a separate structural commit with `make check`; and
- receive independent exact-revision review before acceptance.

