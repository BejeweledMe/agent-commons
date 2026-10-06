# Work reconnect after same-origin server restart

The original browser session restoration treated every `/projects` 404 as proof
of a valid legacy host. After a server restart on the same origin, the saved
opaque API prefix no longer exists: both `/projects` and `/setup` return 404.
The fresh fragment exchange was skipped, leaving subsequent loads on the stale
prefix and displaying link expiry.

The two-file incremental patch changes only `frontend/work/src/api.ts` and adds
behavioral tests in `frontend/work/tests/task-inspector.test.mjs`. A saved prefix
still wins when `/projects` succeeds. If `/projects` returns 404, restoration
requires a successful same-prefix `/setup` response with the supported state and
launch_enabled fields before declaring a valid legacy session. A 401 or a second
404 clears that stale session and allows the fragment code already captured and
scrubbed before networking to be exchanged. With no code, reconnect refuses.

Valid project and legacy sessions reload with no fragment, and avoid consuming
an already-used fragment. Abort, network, 5xx and malformed-success failures
remain failures rather than fake expiry or automatic code consumption. Entity
404s after connection do not clear authentication. All requests retain existing
same-origin credentials; codes are not stored or exposed in navigation.

Validation: 91 focused Node 24 tests passed (task inspector, project workspace,
work shell). These include 13 new reconnection cases covering restart recovery,
valid reloads with and without a used code, legacy fallback, missing code,
unauthorized legacy sessions, abort/offline/server/protocol errors and entity404.
Strict Work TypeScript `tsc --noEmit` passed. The exact restart regression fails
against the preserved main API baseline (`BEFORE_FIX_TEST.log`) and passes with
the fix (`AFTER_FIX_TEST.log`). `git apply --check` passed against baseline.

Isolation: `/private/tmp/commons-ui-reconnect-20261001` copied current main Work
source (including G6); the older G5 checkout remains frozen. Dependencies were
copied into this new temp directory. The local tsc executable symlink points only
within that copied dependency tree. No main source/assets, ledger, credentials,
installed MCP or providers were modified. Base/final hashes are in
`UI_RECONNECT_INTEGRATION.json`. No generated assets were built. Root integration,
Work rebuild, merged makecheck and actual browser validation remain required.
