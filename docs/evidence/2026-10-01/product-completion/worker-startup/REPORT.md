# Implementation startup bound to broker workspace/session

The real Astra builder canary on prior source 9b3368df failed mandatory CLI startup
with foreign operational state despite correctly selected child process environment:
`/private/tmp/commons-product-integration-20261001/final-canary-codex-builder-provisioned.json`.
A native login shell can re-export ambient AGENT_COMMONS_STATE_ROOT. Sanitizing the
provider's inherited environment therefore cannot bind subsequent native CLI calls.
This correction touches no shell configuration, installation or authority.

Implementation-only instructions now receive ephemeral broker-selected repository,
resolved exact state root, existing child session and optional trusted absolute CLI.
Each commons-start read uses shell-quoted explicit --repo/--state-root/--session-id/
--read-only arguments. The supported installed CLI is resolved as a sibling of the
configured resolved MCP entrypoint, with existing executable ownership/mode/location
checks, the same resolved installation bin directory and matching Python shebang.
An unrelated earlier PATH CLI is never substituted. This establishes the supported
paired wheel-entrypoint route; custom wrappers/binaries or mismatching installations
are reported unavailable rather than assumed compatible. Real installed source
compatibility remains the root's exact installed-MCP/provider qualification gate.

The startup instruction authorizes only doctor/session show/orient/inbox/task list/
claim list reads, never CLI session/claim/canonical writes or installation. Canonical
coordination and terminal outcomes stay on worker-scoped MCP. No nonce/credential
enters this context. Missing trusted CLI is disclosed with an honest needs-operator
outcome when mandatory checks cannot be completed, never fake success or ambient
fallback. Profiles granting native implementation tools keep those existing limits;
reviewer/verification restrictions and reviewer instruction bytes remain unchanged.

Pre-child static validation reserves an equal-sized session identity in the section.
After the broker registers its child, only the broker-owned instruction is recomposed
with the exact actual child identity. Existing frozen skill and executable facts are
preserved; final instruction size/context checks run before the single immutable
invocation build. The final bytes are covered by the existing launch fingerprint.
No plan/environment scope or provider catalog was widened.

Validation: 136 targeted tests passed in 50.73s: delegation instructions, service
launch plans, Grok launch limits, runtime launch plans, orchestration and profile
policy. New tests reproduce before-fix ambient foreign-workspace CLI refusal, then
execute all six generated commands under conflicting state root/state base/session
variables, with spaces/quotes/$()/backticks in repository/state/executable paths.
They prove successful explicit binding, no shell evaluation, no canonical or state
file changes, and no new session. Actual runtime invocation tests prove the registered
child identity replaces the placeholder. Missing/unsafe/other-interpreter/other-install
sibling tests refuse pairing even when another CLI occurs earlier on PATH. Existing
independent-review byte-stability SHA remains f6e97fad21818e0a21e151a966070e10a471ce3d76f864d26729accf51d0937f.
Ruff check/format and git apply --check against the preserved baseline passed.
No providers were launched; real after-fix canary remains required.

Only the five paths in WORKER_STARTUP_INTEGRATION.json are integration changes.
Base/final hashes cover current main at copy time. Work stayed in this isolated temp
copy; earlier G5/UI/history source and provenance remain frozen. No main checkout,
real ledger, shell/credentials, installed tools, dependencies or assets were changed.

Separate schema recommendation (not implemented): commons_delegation_needs_operator
in src/agent_commons/mcp/server.py annotates reason_code as str, while canonical
validation in domain/validation.py accepts a closed 11-value set. Its MCP schema
therefore advertises no enum. The prior canary invented workspace_state_mismatch,
received a validation rejection, then retried integrity_error: two terminal calls.
Expose a shared canonical typed Literal/enum for that schema in a separately scoped
contract change and test catalog/finalizer behavior. This patch adds no reason code,
changes no terminal outcome signature, and does not relax validation.
