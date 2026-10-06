# Grok WP20 verification handoff

One actual Grok CLI verification finished successfully: requested `grok-4.7`; provider `modelUsage` reports `grok-4.7-build`. CLI `grok 1.0.13 (5e9a58528b76) [stable]`. One provider attempt, zero provider retries, exit 0, `end_turn`, 28 turns, 921.726 seconds under a 1200-second supervisor. Before that attempt, one 0.108-second launch was refused by the outer sandbox before any model work (stdout zero bytes), because the CLI could not initialize its native hook registry. The identical bounded launch then succeeded under scoped escalation and the enforced native read-only policy; the failed startup is preserved separately.

The full visible provider answer is `grok-visible-final.md`; raw provider JSON/hidden reasoning remains private. Exact sanitized metadata is `provenance.json`, full operational metadata is `meta.json`, invocation prompt is `prompt.txt`, and immutable input hashes are `baseline-hashes.json` and `input-manifest.json`.

Grok executed once the unmodified `benchmarks/benchmark_work_mutation.py --profile large --repeats 2 --json` using the main existing read-only .venv interpreter, `PYTHONPATH` pointing to isolated src, all three operator state/session environment variables unset, and disposable synthetic temporary fixture. Actual JSON is `benchmark-large.json` (SHA-256 `d33d8779a67e7bcbde698e32c186562b2ef0c8a57cc2346eaa1f0db26ecab415`); machine summary is `benchmark-summary.json`. Fixture: 3206 events, 350 manifests, 390 tasks, 318 reviews, 350 artifacts, 80 decisions, 40 delegations; build 8.891 seconds excluded from timings. Benchmark execution around 257 seconds per visible report. Stderr contains only existing Starlette/httpx deprecation warning.

| Workload | Cold median seconds | Warm median seconds |
|---|---:|---:|
| task_create | 8.558 | 8.696 |
| task_take | 8.634 | 8.633 |
| task_complete | 13.233 | 13.291 |
| task_submit | 13.169 | 13.216 |
| decision_propose | 8.822 | 8.713 |
| panel_http_task_create | 8.914 | 8.977 |

The user-visible <=1s goal is unmet in every sample. Benchmark residual_elapsed_seconds near 0.07–0.14 seconds cannot substitute for full latency; exclusive lock_hold for task_create alone is 1.595/1.640 seconds. Timings are an independent diagnostic rerun while other isolated tests were active, not an idle release benchmark; with two samples, p95 is not a robust percentile. Earlier WP20 comparison only measured task_create, not all six workloads.

No material integrity/retry/thread defect identified. Own-append accepts only fully verified exact returned record identity/content and still hashes old files; same-manager RLock prevents other threads bypassing canonical lock or mutating a writer's view. Grok notes `_guard_integrity` calls event_file_digests without its own RLock but all current callers are already under canonical lock; this is a future-caller limitation, not a current defect. Focused tests were not independently rerun by Grok; supplied 384-test evidence was inspected. Full make check remains integrator gate.

Correction to author report wording: EventRecord.sha256 is the canonical full-document hash, including event_id and recorded_at, rather than semantic-document hash. Full body equality additionally binds the returned record.

Exact package Python source SHA-256 before/after: `194b08d49721b375aa7858a7db67b787134ff0cd7dfc26d377163dd7bf45b866`. Benchmark source SHA-256: `2fb9eddb8e779abf1d9cf554a414e012522d48e4d3cd1002db218a4068253917`. Two-file patch SHA-256: `83d88aa93c559127dd7762153c9039ff81b260c5ef791547b9ddba95f30a3611`. Full baseline checks report zero mismatches in immutable original and isolated copy (2249 files). Main checkout explicitly native read-only, main operational ledger and .git denied; no main writes, tool reinstall, assets, env changes, account/billing/auth changes, or canonical records were made. Canonical acceptance/truth promotion belongs to the integrator and was not performed by this worker.
