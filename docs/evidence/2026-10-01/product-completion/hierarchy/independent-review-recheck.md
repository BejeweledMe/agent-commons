# G6 independent recheck

Verdict: reported findings resolved; no new findings in this bounded recheck. This does not constitute canonical task acceptance or a full release review.

Source root: `/private/tmp/commons-product-g6-20261001`.

- Updated `g6.patch` SHA-256: `e1948b6ac5848cc9be8b2dc124caace3d38c8730110ae39655ea4286da0e0f9f`.
- Separate `g6-manager-semantics.patch` SHA-256: `9f1220747a4b90123c49e26f3d4f8d772b8374b45badc10feda070ee0ed74214`.
- Updated integration manifest SHA-256: `cabfad11aa330749267d43d3280985109f63a8595bb4b6058c8436392f0f11a8`.
- WP20 manager base SHA-256: `36cb00f5742828885e5b297a45125f8a094bd3e830e1e1b418858a801dc62b2a`.
- Final manager SHA-256: `a950df257bfc88b6ae5adc150c0aa737a4d4a27ec8a999eb18b7e77701b55b89`.

All 45 ordinary manifest paths and the separate manager result match their manifest hashes. Independently reconstructed the captured baseline in a new private directory, used `git apply` to apply both patches, and verified all 46 resulting hashes. Manager semantics patch is based exactly on the verified WP20 overlay and does not duplicate its delta.

## Correction bypass resolved

The shared structural-correction helper used by supported writes and replay now rejects semantic kind conversion and top-level parent changes. It treats absent/default kind and absent/null creation parent as equivalent. It also compares both presence and value of nested `changes.parent_task_id`; adding, removing, changing, or converting an explicit detach through correction is rejected while wording corrections remain allowed.

Read the actual helper, writer policy, replay policy, and new `tests/services/test_task_hierarchy_corrections.py`. Independently ran tests covering correction rejection after cancellation/lifecycle progression, valid target/digest imported corrections rejected by replay, nested parent changes, and default-equivalent creation corrections in both directions. No remaining bypass was found in the reported paths.

## Compatibility floor resolved

Supported semantics is v7. Universal `record_event` raises the hierarchy floor after canonical validation and before append for component/parented creation and any revision containing a parent edit, including explicit detach. Direct-write tests establish stamp order and identical retry without duplicate stamps. Failed hierarchy validation, ordinary task creation, wording-only edits, and old identical retries do not raise the floor.

The upgrade test exercises the existing projection guard with a v6 reader. The original review's old strict-storage-reader limitation remains explicitly documented in ADR 0024: a stamp alone does not guarantee a friendly `doctor` upgrade message because schema validation can fail before projection sees it. The repaired report makes no stronger claim.

Independently retried the actual legacy task previously created with captured pre-G6 source. Both omitted defaults and explicit `task_kind="task", parent_task_id=None` returned its original event. The ledger remained at floor 1 and event count was unchanged.

## Checks and scope

106 targeted Python tests passed in 9.59 seconds: hierarchy correction/floor tests, task-edit regressions including same-manager/separate-manager cycle races, typed task envelopes, task projection, and the existing ahead-of-code projection guard. Test output is `/private/tmp/commons-g6-independent-recheck-tests.log`.

Used the existing venv read-only with isolated source PYTHONPATH, removed inherited Commons environment variables, disabled bytecode/cache writes, and kept fixtures/output under private temp paths. No source, main checkout, provider, installed tool, or main canonical state was changed.

Scope was ONLY the two original findings and their regression behavior. Did not repeat broad frontend, full make check, shipped-assets, wheel, broker canary, or live browser checks. Original review remains separately preserved at `/private/tmp/commons-review-g6-20261001.md`. Any change to the bound hashes makes this recheck stale.
