# LLM runtime architect assessment: provider adapters

- Date: 2026-08-29
- Author: delegated LLM systems architect run (delegation.74TX6EDWAK96HBB54CBD88AB1K, claude-builder)
- Subject: `docs/provider-adapter-architecture-plan.md` against the runtime as of `main` (working tree at commit `dd65bdb` plus uncommitted UI/MCP edits that do not touch the runtime files below)
- Task: task.3CKP1QJA4S1Z8AMFZ3M8E9MF42 @ evt.01M16CT6XF3QQZM3CGT445FKFE

Every claim is labelled **Fact** (verified at file:line), **Inference** (reasoned from facts), or **Decision needed** (requires an owner ruling). Line numbers refer to the working tree read for this assessment.

## 1. Verdict in one paragraph

The proposed `ProviderAdapter`/`AdapterRegistry`/`LaunchPlan` contract is implementable as a thin re-partitioning of code that already exists, with two genuinely new components: `decode_result` (no provider stream parsing exists today at all) and `SkillProjector` (skills today are instruction text plus byte-identical installed files, with no projection identity or digest). The highest-risk finding is that the plan's `decode_result(process_output) -> ProviderOutcome` can conflict with the load-bearing invariant that provider output never decides success — today success is decided exclusively by the MCP terminal tool plus canonical state, and the exit code (`subprocess_runner.py:472-477`) plus `_transition_after_attempt` (`delegation_runtime.py:957-968`) deliberately classify a clean provider exit without a terminal tool as `invalid_result`. `decode_result` must be specified as diagnostic-only. The second material seam risk is that the launch invocation is currently built twice with non-identical inputs (§4.3), which the adapter contract should eliminate by making `ValidatedPlan` the single artifact that is later launched.

## 2. Current runtime lifecycle: the factual map

### 2.1 Profile and invocation layer (`src/agent_commons/runtime/model.py`)

- **Fact.** Providers and profiles are closed enums: `Provider` (model.py:29-31), `BuiltinProfileId` with provider/reviewer derivation (model.py:48-62). The registry accepts only these four IDs (`ProfileRegistry.from_mapping`, model.py:837-861) and rejects unknown fields per provider (`_CODEX_FIELDS`/`_CLAUDE_FIELDS`, model.py:781-802).
- **Fact.** The current provider-neutral protocol is `RunnerProfile` with exactly `profile_id`, `provider`, `supports_budget`, and `build_invocation(...) -> RunnerInvocation` (model.py:495-514). `RunnerInvocation` is argv-plus-stdin only, NUL-checked (model.py:479-492).
- **Fact.** Codex launch shape: `--ask-for-approval never --sandbox <mode> [--model X] exec --ignore-user-config --strict-config -c mcp_servers.agent-commons.{command,args,enabled_tools,required} --json --color never -` with the instruction on stdin (model.py:590-629). Claude launch shape: `--print --verbose --output-format stream-json --permission-mode <mode> --no-session-persistence --disable-slash-commands --setting-sources "" --strict-mcp-config --mcp-config <json> [--model X] [--max-budget-usd N] [--tools/--disallowed-tools ...] --allowed-tools ...` (model.py:736-772). These two methods are the entire provider-specific compile step the plan proposes to wrap.
- **Fact.** Worker MCP wiring is shared: `_resolved_worker_mcp` builds `--repo/--state-root/--delegation-id/--git-executable/--session-id` argv for the scoped MCP server (model.py:236-275); Codex projects it into TOML literals (model.py:605-623), Claude into a strict JSON `--mcp-config` (model.py:723-735).
- **Fact.** Tool scoping is provider-neutral data with per-provider projection: the fixed tool sets and purpose/grant composition live in `_worker_tools` (model.py:155-198) with the constants at model.py:80-133; Codex strips the `mcp__agent-commons__` prefix into `enabled_tools` (model.py:586-589), Claude passes prefixed names to `--allowed-tools` and adds native-tool denylists (reviewer: model.py:761-769; builder: model.py:771).
- **Fact.** Trusted-executable resolution rejects workspace-resident, non-regular, group/world-writable, and foreign-owned binaries (`resolve_trusted_executable`, model.py:325-378), with a typed `ExecutableResolutionError` carrying an `ExecutableRole` (model.py:40-46). Demo builds substitute the inert `/dev/null/...` placeholder only on that exact error (model.py:385-408).
- **Fact.** Capability differences already enforced: Codex `supports_budget = False` and refuses `max_budget_microusd` with `ConfigurationError("Codex CLI cannot enforce a monetary launch budget")` (model.py:550-551, 575-576); Claude `supports_budget = True` and takes `min(profile, request)` (model.py:670-672, 697-703). Reviewer invariants are frozen in `__post_init__`: Codex reviewer must be `read-only` sandbox (model.py:542-543), Claude reviewer must be `dontAsk` (model.py:658-664). `validate_model_name` is the single exported model rule shared by hire-time and launch-time (model.py:294-311).
- **Fact.** The instruction is bounded to 1 MB and stdin-only (`_instruction_bytes`, model.py:411-417).

### 2.2 Broker layer (`src/agent_commons/runtime/broker.py`, `subprocess_runner.py`)

- **Fact.** `LocalBroker.run` builds the invocation *before* reserving a durable attempt so malformed ephemeral content creates no durable state (broker.py:161-172), fingerprints `{provider, profile_id, argv}` as `launch_plan_sha256` (broker.py:173-183), reserves, transitions `LAUNCHING`, and records canonical `delegation.started` via the lifecycle hook inside `on_started` after the PID is durably known (broker.py:228-240).
- **Fact.** Budget capability is checked at the broker boundary too: `child_policy.max_budget_microusd` with a non-budget profile raises before build (broker.py:157-160).
- **Fact.** The subprocess runner spawns only the inert exec gate before canonical start (`gated_argv`, subprocess_runner.py:404-415), sanitizes the environment to a fixed allowlist and injects only `AGENT_COMMONS_SESSION_ID`/`AGENT_COMMONS_DELEGATION_ID`/`AGENT_COMMONS_STATE_ROOT` (subprocess_runner.py:21-39, 98-119), bounds output with a 4 KiB stderr tail reserved separately (subprocess_runner.py:41, 207-249), and classifies the run purely by exit code, cancellation, and deadline — `return_code == 0` → `SUCCEEDED/COMPLETED`, otherwise `FAILED/NONZERO_EXIT`, `CANCELLED`, or `TIMED_OUT` (subprocess_runner.py:458-488). **No stdout event parsing of any kind exists.** Codex `--json` and Claude `stream-json` output is drained into bounded buffers and then discarded (never persisted; stdout is explicitly never eligible, subprocess_runner.py:263-267).

### 2.3 Canonical join layer (`src/agent_commons/services/delegation_runtime.py`)

- **Fact.** `DelegationRuntimeService.run` is the only translation of a canonical delegation into a launch: requester-session ownership check (delegation_runtime.py:1033-1044), requested-revision binding (1045-1049), per-delegation launch-key idempotency with live-process refusal (1051-1080), `requested`-state requirement (1082-1090), role scope resolution at launch time (1094-1101, `_role_scope` 724-755), model substitution via re-validating `dataclasses.replace` (`_profile_for`, 757-780), pre-validation `build_invocation` dry run before any child session or reservation (1116-1128), child-session open with TTL margin (`_open_child_session`, 692-722), correlation and trace binding (1141-1156), broker run, canonical finalization, and child-session cleanup in `finally` (1194-1217).
- **Fact.** Terminal classification (`_transition_after_attempt`, 866-969) is the de-facto result decoder: exited `input_needed` → `needs_operator` because "this runtime has no resumable interactive channel" (878-889); non-terminal attempt → `needs_operator`/`orphaned` (891-902); `CANCELLED` before/after start (904-923); `TIMED_OUT` (924-931); `FAILED` pre-start preserves `requested` for explicit `--retry` within `max_attempts` (932-937), otherwise `launch_failed`/`runtime_error` with a safe diagnostic code (938-955); **process `SUCCEEDED` without a canonical terminal result → `needs_operator` with `invalid_result`** (957-968).
- **Fact.** Budget policy derivation: `provider_units` must cover `max_attempts`; `micro_usd` is divided across attempts; output cap fixed at 1 MiB (`_policies`, 648-690). Budget capability is checked a third time against the delegation's unit (1106-1110).
- **Fact.** Capability/read-model surface for UI is `profile_summaries` — launch mode, OS-sandbox flag with per-provider isolation note, supported budget units, operator limits (269-333) — and `profile_tool_summary` for the tools reference (model.py:201-233).
- **Fact.** The demo runner substitutes tolerant profiles keyed strictly on the `DemoRunner` binding (402-410).

### 2.4 Instruction and skills (`services/delegation_instruction.py`, `catalog`, `integrations/installer.py`)

- **Fact.** `compose_delegation_instruction` is the single instruction owner; it is provider-agnostic and branches only on reviewer-vs-builder entry text (delegation_instruction.py:50-61), appends operator-authored skill text resolved by id (44-49), and hardcodes the result protocol including the literal tool names `mcp__agent-commons__commons_complete_review` / `commons_succeed_delegation` (98-107).
- **Fact.** Skills as installed files: both clients receive byte-identical `SKILL.md` and `agents/openai.yaml` for the same seven skill names, differing only in root (`.agents/skills` vs `.claude/skills`) (installer.py:36-49, 546-561). The installer is transactional (preimage CAS + atomic write + rollback, installer.py:304-361, 403-447, 563-591) and fails closed on locally modified skills without `replace_skills` (installer.py:222-249). There is no skill manifest, projection identity, version, or digest anywhere in the installer.
- **Fact.** Skills as launch inputs: a role's `skills` list resolves to `(name, text)` instruction tuples from the operator catalogue at launch (delegation_runtime.py:750-753); an undefined skill fails the launch closed (docs/THREAT_MODEL.md:219-221; RuntimeConfiguration comment delegation_runtime.py:154-157).

### 2.5 Governing documents

- **Fact.** ADR 0004 already fixes the runner contract verbs (`capabilities/prepare/launch/observe/provide_input/cancel/recover`, adr/0004:193-201), forbids arbitrary argv/env/credentials (adr/0004:174-182), specifies the gate/durable-start sequence (adr/0004:232-266), and notes `max_depth: 0` is the only supported creation path today (adr/0004:7-10). The plan's adapter verbs (plan:138-146) are a refinement of this, not a contradiction — but see §4.1 for the naming delta.
- **Fact.** `docs/BROKER_OPERATIONS.md` defines the split the plan asks for: static preflight is "not behavioral proof" and the real-stdio canary must prove MCP terminal completion and canonical finalization (BROKER_OPERATIONS.md:3-27), with an explicit paid real-provider canary command (29-54) and the release evidence gate (95-101). Operational attempt schema is at v4 with in-memory upgrade from v2/v3 and a documented rollback (120-129).
- **Fact.** The threat model pins the constraints the adapter layer must not weaken: confused-deputy launch (THREAT_MODEL.md:97-122), narrowing-only role tools and operator-only skill text (207-231), provider output/telemetry exclusion with the 4 KiB sanitized stderr tail and 32-entry tool-audit bound (464-480), provider drift handled by versioned contracts and opt-in real tests (482-488), and the residual that Claude's writable builder has no OS-enforced boundary while Codex does (514-520; surfaced in `profile_summaries` at delegation_runtime.py:299-308).

## 3. Mapping the proposed contract onto current code

| Proposed (plan:138-146) | Current implementation | Gap class |
|---|---|---|
| `describe(profile) -> ProviderDescriptor` | `profile_summaries` dict (delegation_runtime.py:269-333) + `profile_tool_summary` (model.py:201-233) | Formalize: exists as ad-hoc dicts, not a typed descriptor |
| `capabilities(profile) -> CapabilitySet` | `supports_budget` property only (model.py:500, 550, 671); everything else implicit in `__post_init__` refusals | New type; data mostly exists |
| `validate(plan, capabilities) -> ValidatedPlan \| typed refusal` | Pre-validation `build_invocation` dry run (delegation_runtime.py:1116-1128) + scattered `ConfigurationError`s | Consolidate; make the validated plan first-class (§4.3) |
| `project_skills(skill_refs, provider) -> EphemeralSkillBundle` | `skill_instructions` tuples (delegation_runtime.py:750-753) + installer's identical files (installer.py:546-561) | **New component** (identity/digest/refusal missing) |
| `compile_instruction(plan, skill_bundle) -> EphemeralInstruction` | `compose_delegation_instruction` (delegation_instruction.py:33-118) | Near-complete; needs provider-aware tool naming (§7) |
| `build_invocation(plan, instruction) -> RunnerInvocation` | `CodexRunnerProfile.build_invocation` (model.py:553-629), `ClaudeRunnerProfile.build_invocation` (model.py:674-778) | Complete; relocate behind the adapter |
| `decode_result(process_output) -> ProviderOutcome` | **Does not exist.** Exit-code classification (subprocess_runner.py:472-477) + canonical-state check (delegation_runtime.py:957-968) + terminal-tool audit counters (delegation_runtime.py:531-572) | **New component**; must be diagnostic-only (§5) |
| `AdapterRegistry` (allowlisted) | `ProfileRegistry` (model.py:811-861) keyed by profile, with the provider branch inside `from_mapping` (855-860) | Reshape: registry keyed by `Provider`, resolved from profile |
| `LaunchPlan` (provider-neutral) | `BrokerRequest` + `CorrelationIds` + `RuntimePolicy` (broker.py:33-67; model.py:427-477) with `launch_plan_sha256` fingerprint (broker.py:173-183) | Near-complete; add `skill_refs`/capability requirements as typed fields |
| OS lifecycle stays in broker (`launch/observe/provide_input/cancel/recover`) | `SubprocessRunner.run` + gate (subprocess_runner.py:373-518), `stop_provider` (delegation_runtime.py:423-485), `reconcile` (1223-1290) | No change required — plan agrees (plan:99-101, 148-151) |

**Inference.** The plan's table of facts (plan:27-37) is accurate against the code with one refinement: "Codex и Claude уже получают разные argv и MCP-конфигурацию" understates that *tool projection* (prefix-stripped vs prefixed) and *result protocol naming in the instruction* also differ per provider today, and the second is currently wrong for Codex (§7).

## 4. Implementable contract design

### 4.1 Types and ownership

**Inference (design).** Implement in a new `runtime/adapters.py` (adapter protocol + registry) with providers keeping their compile logic:

```text
CapabilitySet (frozen): monetary_budget: bool, os_sandbox: bool,
    resume: Literal["none"] (this MVP), skill_projections: frozenset[str],
    launch_shape: str (opaque version tag), input_channel: Literal["stdin"]
ProviderDescriptor (frozen): provider, adapter_version, executable_role_map,
    safe display fields already emitted by profile_summaries
ValidatedPlan (frozen): the exact RunnerInvocation + instruction digest +
    capability digest + correlation — the thing that is launched, not a
    precursor of it
ProviderOutcome (frozen, diagnostic-only): bounded event-shape tags seen,
    terminal_tool_signal: bool | None, usage_totals: bounded ints | None,
    diagnostic_code: DiagnosticCode
```

Naming should reconcile with ADR 0004's verbs rather than fork them: `validate` == ADR `prepare`; `decode_result` is new and additive; `observe/provide_input/cancel/recover` remain broker/`SubprocessRunner` verbs and must **not** appear on the adapter (plan:148-151 already says this — keep it).

### 4.2 State and data flow

**Inference (design).** Order of operations, preserving every existing barrier:

1. `DelegationRuntimeService.run` resolves role scope and profile exactly as today (delegation_runtime.py:1094-1110).
2. `AdapterRegistry.get(profile.provider)` — allowlist keyed by `Provider`, constructed from operator config only.
3. `adapter.capabilities(profile)`; refuse with typed codes before any durable state (this replaces the three scattered budget checks — broker.py:157-160, model.py:575-576, delegation_runtime.py:1106-1110 — with one authority; keep the inner two as assertions until P6).
4. `adapter.project_skills(...)` → refusal `skill_projection_unavailable` if a required projection is missing (today's fail-closed catalogue lookup generalized).
5. `adapter.compile_instruction(...)` → `adapter.build_invocation(...)` → `ValidatedPlan`, built **once**.
6. Child session open, then `LocalBroker.run(ValidatedPlan)` — broker keeps reserve/gate/hook/finish untouched (broker.py:184-266); `launch_plan_sha256` is computed from the `ValidatedPlan` so the fingerprint semantics are unchanged.
7. After `ProcessResult`, optionally `adapter.decode_result(bounded_output)` to enrich diagnostics; then `_transition_after_attempt` runs exactly as today and remains the only authority on canonical outcome.

### 4.3 Seam defect to fix during P2 (double build)

**Fact.** The invocation is built twice: the pre-validation dry run at delegation_runtime.py:1116-1126 omits `child_session_id`, then the broker rebuilds with it (broker.py:161-172 receiving `request.correlation.child_session_id` at 167). The validated argv is therefore not byte-identical to the launched argv (the MCP `--session-id` argument differs; model.py:273-274).

**Inference.** This is safe today only because both calls traverse identical validation. Under an adapter registry, two builds through evolving adapters is a divergence risk (validation passing on build #1 and failing or differing on build #2). The `ValidatedPlan` should be constructed once after the child session exists; the cost is moving the "validate before opening a child session" property (comment at delegation_runtime.py:1113-1115) into `validate()` proper, which checks everything except the session id. Preserve the property that a failed validation opens no child session and reserves no attempt.

## 5. Provider result parsing (`decode_result`)

**Fact.** Nothing parses provider streams today (§2.2). Success is already provably independent of provider prose: the behavioral canary requires "the final delegation state and result references, not provider prose, satisfy the grader" (BROKER_OPERATIONS.md:22-24), and a clean exit without a terminal tool is `needs_operator`/`invalid_result` (delegation_runtime.py:957-968).

**Inference (design constraint, load-bearing).** `decode_result` must be specified as *diagnostic-only*: it may map allowlisted event shapes to a bounded `DiagnosticCode` and safe usage totals (the SLO "cost visibility ≥95%" at BROKER_OPERATIONS.md:86 needs exactly this), but its output must never be an input to `_transition_after_attempt`'s success/failure branch. Two concrete rules:

1. `ProviderOutcome` never upgrades an attempt — it may only *downgrade* ambiguity into a better diagnostic code (e.g. distinguishing `provider_initialization_failed` from a generic nonzero exit).
2. The parser accepts only allowlisted event shapes and retains no transcript (plan:240-242 agrees; THREAT_MODEL.md:464-480 requires it). Parse from the existing bounded buffers; do not raise output caps for parsing's sake.

**Decision needed.** Whether `provider_terminal_result_missing` (plan:221) should replace today's `invalid_result` reason code or alias it. The tool-audit store already counts terminal-tool completions (delegation_runtime.py:531-572), so the signal exists without parsing; renaming a canonical reason code is a compatibility question for the domain owner, not the adapter owner.

## 6. Typed errors and failure matrix

**Fact.** Today's taxonomy: `ConfigurationError`/`ValidationError`/`LifecycleConflictError` with prose; `ExecutableResolutionError(role)` (model.py:40-46); `SafeDiagnostic`/`DiagnosticCode` + `workflow_diagnostic_code` with hints and safe next actions (delegation_runtime.py:1034-1044, 505-510); sanitized configuration failures (`sanitized_configuration_failure`, used at 1104-1105, 1127-1128); canonical reason codes `launch_failed`/`runtime_error`/`invalid_result`/`orphaned` (§2.3).

**Inference (design).** The plan's stable codes (plan:216-221) map cleanly; the matrix the implementation should test against:

| Phase | Failure | Typed code | Canonical effect | Attempt |
|---|---|---|---|---|
| registry | provider not allowlisted/installed | `provider_unavailable` (today `ExecutableResolutionError`) | none — refuse pre-reservation | none |
| capabilities | budget unit unsupported | `budget_not_enforceable` (today three `ConfigurationError`s) | none | none |
| capabilities | purpose/profile/capability mismatch | `provider_capability_unsupported` | none | none |
| project_skills | required projection missing | `skill_projection_unavailable` (new) | none | none |
| validate/build | malformed plan, unsafe model/id | existing `ValidationError` path, sanitized | none | none |
| spawn | gate spawn OSError | `START_FAILED` (subprocess_runner.py:410-415) | `requested` preserved for retry within `max_attempts`, else `failed`/`launch_failed` (delegation_runtime.py:932-955) | terminal failed |
| started-hook | canonical start write fails | `CONTROL_ERROR` — gate stopped, instruction undisclosed (subprocess_runner.py:434-443; broker.py:236-239) | `requested`/retryable | terminal failed |
| running | provider init dies in sandbox | `provider_initialization_failed` (new; the Codex read-only PATH-alias failure the plan cites at plan:43-45) | `failed`/`runtime_error` | terminal failed |
| running | wall time | `TIMEOUT` | `timed_out` (delegation_runtime.py:924-931) | timed_out |
| exit 0, no terminal tool | — | `provider_terminal_result_missing` / today `invalid_result` | `needs_operator` (957-968) | succeeded-process, unresolved |
| exit after `input_needed` | — | today's fixed summary (878-889) | `needs_operator` | terminal |
| ambiguous/restart | — | `broker_restart_ambiguous`/`operator_stop_requested` (1271-1280) | `needs_operator` | needs_operator |

**Inference.** No new canonical event or state is required for any row — the plan's "no schema growth" gate (plan:285) holds. New codes live in `DiagnosticCode`/UI copy, not the ledger, except the possible `invalid_result` rename (§5, Decision needed).

## 7. Instruction and skill projection findings

- **Fact.** The composed instruction tells every reviewer to call `mcp__agent-commons__commons_complete_review` (delegation_instruction.py:98-107), but the Codex MCP config exposes tools with the prefix stripped (`enabled_tools`, model.py:586-589), so the literal name in the instruction does not match the tool name a Codex worker sees. **Inference:** this is exactly the class of provider difference the plan predicts will leak into shared text (plan:42-44); `compile_instruction` moving behind the adapter (with a provider-aware tool-name renderer) fixes it, and it is worth fixing in P2 rather than P6 because it affects live Codex reviewer runs.
- **Fact.** Installed skill bytes are identical across clients (§2.4). The plan's rule 5 — "same bytes are proven compatibility, not assumed" (plan:194-195) — currently has no proof mechanism; `tests/integrations/test_installer.py` verifies transactional behavior (e.g. `test_locally_modified_skill_requires_explicit_replacement`, test_installer.py:232; symlink/rollback cases at 265-372) but nothing about per-client interpretation.
- **Inference (design).** `SkillBundle` (plan:175-184) needs: a neutral manifest checked into the package resources; installer emitting per-projection digests into its `InstallationReport` (installer.py:62-73 already returns a typed report to extend); and the launch-time `project_skills` consulting the same manifest so the instruction-text skills (delegation_runtime.py:750-753) and the installed-file skills can no longer drift apart silently. Canonical events are untouched, as the plan requires (plan:199).

## 8. Capability negotiation and the real-launch canary

- **Fact.** Static preflight already compares generated flags, worker tool allowlist, catalog digest, and source fingerprint without model work (adr/0004:223-230; `tests/runtime/test_preflight.py` exists). The behavioral gate is separate and real (`tests/runtime/test_real_stdio_contract.py:63` — `test_behavioral_canary_crosses_generated_real_mcp_stdio_and_finalizes_canonically`; paid canary at BROKER_OPERATIONS.md:29-54, and `tests/runtime/test_provider_canary.py`).
- **Fact.** The gap the plan names is real and specific: the Codex reviewer passed MCP preflight but died creating PATH aliases/app-server in a read-only sandbox (plan:43-45) — i.e. preflight proves the *contract*, not *initialization inside the sandbox*.
- **Inference (design).** The missing artifact is a per-provider *initialization probe* between static preflight and paid canary: launch the real provider executable under the profile's exact sandbox/permission flags with an instruction whose only obligation is one MCP no-op call and immediate exit, classified by the same terminal-tool audit. For Claude, `--max-budget-usd` at a floor value bounds cost; for Codex there is no monetary bound (model.py:575-576), so the probe consumes one `provider_units` attempt and must remain operator-confirmed exactly like the current canary flag (BROKER_OPERATIONS.md:33-40). **Decision needed** (matches plan open decision 3, plan:333): whether a zero-cost Codex probe exists at all; if not, G4's "preflight and real-launch canary separated and both green" (plan:311) must be budgeted per release, not per launch.
- **Inference.** Capability negotiation itself needs no new negotiation *protocol*: capabilities are static per (provider, profile, adapter version) and can be a frozen `CapabilitySet` compiled at registry load, with the canary verifying that the declared set matches observed behavior. Dynamic runtime negotiation with the provider CLI would contradict the untrusted-output rule and is not needed for the first wave.

## 9. Context and resume semantics

- **Fact.** There is no resume: exited `input_needed` becomes `needs_operator` (delegation_runtime.py:878-889); Claude runs with `--no-session-persistence` (model.py:744); the onboarding contract states the headless MVP cannot reattach (.agent-commons/ONBOARDING.md:84-85); ADR 0004 stages `provide_input`/`recover` behind the versioned contract with fail-closed absence (adr/0004:203-208).
- **Inference (design).** The adapter contract should encode `resume: "none"` in `CapabilitySet` for both adapters now, so the UI's typed refusal ("no resumable run" rather than a broken promise — plan open decision 5, plan:335-336) is derived from the same object the launch uses. Provider-native resume (Claude session IDs, Codex thread continuation) should not be adopted until there is a design for binding a resumed provider session to the same delegation, attempt journal, and child Commons session — otherwise reconciliation's identity proofs (THREAT_MODEL.md:410-417) are silently weakened. Defer; do not emulate.

## 10. Cost and budget behavior

- **Fact.** `provider_units` is admission-only: one unit per process attempt, `max_attempts` must fit inside the budget (delegation_runtime.py:665-668), operator caps and queue backpressure are shared through the state root (BROKER_OPERATIONS.md:58-71; `OperatorLimits` consumed at delegation_runtime.py:387-395). `micro_usd` is divided across attempts and reaches only Claude as `--max-budget-usd` (delegation_runtime.py:683-689; model.py:754-755), with the profile ceiling winning via `min` (model.py:697-703). Budget is charged against the delegation tree root (`CorrelationIds.budget_scope`, model.py:472-477).
- **Inference.** The adapter refactor must keep the *unit semantics in the broker* and only the *flag rendering in the adapter*. The one behavioral addition worth making in P3: `decode_result` extracting safe usage totals from the final provider event (both CLIs emit them) to close the cost-visibility SLO (BROKER_OPERATIONS.md:86) — bounded integers only, telemetry-layer only, per ADR 0004's "provider-reported budget totals remain future additions" (adr/0004:354-355).
- **Fact.** The plan's rule "budget incompatibility = typed refusal" (plan:161) is already implemented three times over (§4.2 item 3); consolidation, not creation.

## 11. Security constraints for the adapter layer

**Inference (binding constraints, all traceable to current enforcement points that must survive the refactor):**

1. Adapters never accept argv/env/credentials from callers, never emit env vars, and produce only `RunnerInvocation` (model.py:479-492; adr/0004:174-182; THREAT_MODEL.md:97-111). The `SafeEnvironment` allowlist stays broker-owned (subprocess_runner.py:21-39).
2. Adapters hold no canonical write path: `CommonsManager` transitions remain exclusively in `DelegationRuntimeService`/hook (plan:228-230; delegation_runtime.py:336-367).
3. Reviewer capability is fixed in the profile value object, not adapter code paths — the `__post_init__` invariants (model.py:542-543, 658-664) must survive wrapping so `dataclasses.replace` re-validation keeps working (`_profile_for` rationale, delegation_runtime.py:757-780; test at tests/runtime/test_profiles_policy.py:163).
4. Executable resolution stays mandatory per role (model.py:325-378) and the registry stays operator-config-only; no discovery of adapters from the workspace.
5. `decode_result` input is untrusted; bounded output, allowlisted shapes, no transcript persistence (THREAT_MODEL.md:464-480); stderr-tail sanitization pipeline unchanged.
6. Instruction stays stdin-only and ≤1 MB (model.py:411-417); skill text stays operator-catalogue-only (THREAT_MODEL.md:219-231).
7. The Codex/Claude isolation asymmetry (`os_enforced_sandbox`, delegation_runtime.py:299-308; THREAT_MODEL.md:514-520) must appear in `ProviderDescriptor` so the unified UI cannot present the two writable profiles as equivalent.

## 12. Performance and cost controls

**Fact.** Existing controls the refactor must not regress: build-before-reserve (broker.py:161), single live attempt per delegation (delegation_runtime.py:1059-1064; adr/0004:255), per-delegation flock + in-process mutex (delegation_runtime.py:104-131), bounded FIFO queue with explicit backpressure (BROKER_OPERATIONS.md:66-69), 1 MiB output cap (delegation_runtime.py:681), 8 s termination grace (subprocess_runner.py:284-286), finalization SLO p95 ≤ 5 s (BROKER_OPERATIONS.md:81). **Inference.** Adapter dispatch adds one in-process indirection and zero I/O; the only measurable-cost additions are skill-digest computation (hash of a few KiB, negligible) and `decode_result` (parse of an already-in-memory ≤1 MiB buffer). No new process, service, or lock is needed — consistent with the plan's modular-monolith non-goal (plan:63-65).

## 13. Migration order, rollback, and test gates

### 13.1 Consumers that must be inventoried for G0 (plan:286)

**Fact.** Direct `RunnerProfile`/registry consumers found: `LocalBroker.run` (broker.py:156-172), `DelegationRuntimeService` (`_profile_for` 757-780, `_profiles_with` 782-794, dry-run 1116-1128, summaries 269-333), demo wrapper (`demo_tolerant_profiles`, 402-410), preflight (`runtime/preflight` per adr/0004:223-230), the canary, `profile_tool_summary` (model.py:201) for the UI, and `skill_instructions` consumers (delegation_runtime.py:750-753; instruction composer). The UI launch path reuses `DelegationRuntimeService` (THREAT_MODEL.md:285-296), so it inherits the refactor for free — a strong reason to keep the service's public signature frozen through P6.

### 13.2 Order (endorsing plan:258-274 with two amendments)

**Inference.** P2 (protocol + registry wrapping current behavior byte-for-byte) is correctly first and is low-risk because the golden argv tests exist (`test_profiles_build_fixed_argv_and_keep_instruction_on_stdin`, tests/runtime/test_profiles_policy.py:46; scope-parity tests at 313-452). Amendments:

1. Fold the double-build fix (§4.3) into P2, not later — it changes internal call shape and is cheapest while both paths are still assertable byte-for-byte.
2. Fix the Codex tool-name mismatch in the instruction (§7) in P2/P3, because it is a live correctness issue, not a projection nicety.

Rollback: because adapters are constructed from the same operator profile config and `launch_plan_sha256` derives from argv (broker.py:173-183), reverting P2-P5 restores identical fingerprints; nothing in the canonical ledger records adapter identity, and operational schema v4 is untouched (BROKER_OPERATIONS.md:120-129). P6 (deleting the profile classes) is the first irreversible-ish step and should wait for the release evidence gate (BROKER_OPERATIONS.md:95-101).

### 13.3 Test gates (concretizing plan G1-G4, plan:288-316)

- G1: golden argv byte-equality for both providers (extend test_profiles_policy.py:46); fake-provider MCP terminal flow (existing `test_real_stdio_contract.py:63` plus a Codex-shaped fake); typed refusal reaches no process (extend tests/runtime/test_orchestration_security.py); exit-0-without-terminal-tool stays `invalid_result` with `decode_result` active — a *new* required test asserting decode_result cannot upgrade an outcome.
- G2: projection digest tests in tests/integrations/test_installer.py; paired per-client skill semantics tests; refusal test for a missing projection.
- G3: one UI action → two provider plans via `DelegationRuntimeService` only; assert no argv/env/raw stderr in any UI DTO (extend existing `tests/ui/test_work_app_contract.py` route-surface assertions).
- G4: separated static preflight + initialization probe + paid canary, each green independently; `make check` in the locked environment; independent review at the exact merged revision.

## 14. Open decisions surfaced by this assessment

1. `invalid_result` vs `provider_terminal_result_missing` naming (§5) — domain owner.
2. Zero-cost Codex initialization probe feasibility (§8) — adapter owner; affects G4 cadence.
3. Whether `CapabilitySet.resume` should be declared per adapter version now with value `"none"` (§9, recommended) or omitted until a resume design exists — the former is safer for the UI contract.
4. Where `CapabilitySet` lives relative to `OperatorLimits`: capability is per (provider, profile, adapter), limits are per operator; keeping them separate objects avoids the config-file-writer split the threat model relies on (THREAT_MODEL.md:225-231).

## 15. What this assessment did not do

No source, tests, plans, or UI files were modified; only this review file was created. No commands other than read-only inspection were run; the full test suite was not executed (line references were verified by reading, not by running the suite). The uncommitted working-tree changes to `cli/__init__.py`, `mcp/scoped_repo.py`, `ui/server.py`, and four test files were not assessed beyond confirming they do not touch the runtime modules cited here.
