import type {
  ApiError,
  Catalog,
  ContextPackOption,
  ContextPackCatalog,
  ContextPackDetail,
  ContextPackDraft,
  ContextPackFact,
  ContextPackReferenceKind,
  RevisionBoundRef,
  JsonObject,
  LaunchOptions,
  Profile,
  ProviderAuthAction,
  ProviderAvailability,
  ProviderAvailabilityRefusalCode,
  ProviderCapabilityRefusalCode,
  ProviderAuthState,
  ProviderAuthStatus,
  RoleOption,
  SetupGuidance,
  SetupGuidanceBlockerCode,
  SetupGuidanceNextActionKey,
  SetupGuidanceTool,
  SetupStatus,
  StarterPack,
  StarterPackBlueprint,
  StarterPackCatalog,
  StarterPackRole,
  TaskOption,
  TrackerAttention,
  TrackerCapacity,
  TrackerEdge,
  TrackerFreshness,
  TrackerRun,
  TrackerSnapshot,
  TrackerSurfaceState,
  TrackerTask,
  WorkspaceData,
  WorkspaceMeta
} from "./contracts";

const API_BASE_STORAGE_KEY = "agent_commons.ui.api_base";
const API_BASE_PATTERN = /^\/api\/[A-Za-z0-9_-]{32,128}$/;
const SETUP_GUIDANCE_BLOCKER_CODES = new Set<SetupGuidanceBlockerCode>([
  "setup_not_a_repository",
  "setup_uninitialized",
  "setup_unconfigured",
  "setup_no_provider_found",
  "setup_support_binary_unresolved",
  "setup_config_rejected_by_loader"
]);
const SETUP_GUIDANCE_TOOLS = new Set<SetupGuidanceTool>([
  "Claude",
  "Codex",
  "git",
  "agent-commons-mcp"
]);
const SETUP_GUIDANCE_ACTION_KEYS = new Set<SetupGuidanceNextActionKey>([
  "choose_git_repository",
  "initialize_workspace",
  "install_provider_and_check_again",
  "install_support_tool_and_check_again",
  "configure_runtime",
  "repair_workspace_configuration",
  "setup_ready"
]);
const PROVIDER_AUTH_STATES = new Set<ProviderAuthState>([
  "ready",
  "authentication_required",
  "authenticating",
  "timed_out",
  "cancelled",
  "failed",
  "unsupported",
  "credential_store_unavailable"
]);
const PROVIDER_AUTH_ACTIONS = new Set<ProviderAuthAction>([
  "authenticate",
  "cancel_authentication",
  "check_again",
  "continue_launch"
]);
const PROVIDER_AVAILABILITY_REFUSALS = new Set<ProviderAvailabilityRefusalCode>([
  "provider_installation_unavailable",
  "provider_initialization_failed",
  "provider_qualification_required",
  "provider_qualification_failed",
  "provider_authentication_required",
  "provider_authentication_unconfirmed"
]);
const PROVIDER_CAPABILITY_REFUSALS = new Set<ProviderCapabilityRefusalCode>([
  "provider_resume_unavailable",
  "provider_skill_projection_unavailable",
  "provider_monetary_budget_unavailable"
]);
const PROVIDER_AVAILABILITY_AUTH_STATES = new Set([
  ...PROVIDER_AUTH_STATES,
  "not_checked"
]);
const PROVIDER_AVAILABILITY_PROFILES = new Set([
  "codex-builder",
  "codex-independent-reviewer",
  "claude-builder",
  "claude-independent-reviewer",
  "grok-builder",
  "grok-independent-reviewer"
]);
const PROVIDER_BY_PROFILE: Readonly<Record<string, "codex" | "claude" | "grok">> = {
  "codex-builder": "codex",
  "codex-independent-reviewer": "codex",
  "claude-builder": "claude",
  "claude-independent-reviewer": "claude",
  "grok-builder": "grok",
  "grok-independent-reviewer": "grok"
};
const SANDBOXES_BY_PROFILE: Readonly<Record<string, ReadonlySet<string>>> = {
  "codex-builder": new Set(["os_enforced"]),
  "codex-independent-reviewer": new Set(["os_enforced"]),
  "claude-builder": new Set(["trusted_workspace", "none"]),
  "claude-independent-reviewer": new Set(["trusted_workspace", "none"]),
  "grok-builder": new Set(["os_enforced"]),
  "grok-independent-reviewer": new Set(["os_enforced"])
};
const PROVIDER_CANCELLATION_MODES = new Set(["broker"]);
const PROVIDER_USAGE_REPORTING = new Set(["none"]);
const PROVIDER_SANDBOX_BOUNDARIES = new Set([
  "os_enforced",
  "trusted_workspace",
  "none"
]);
const PROVIDER_AVAILABILITY_REMEDIATION: Readonly<Record<ProviderAvailabilityRefusalCode, readonly string[]>> = {
  provider_installation_unavailable: ["verify_provider_installation"],
  provider_initialization_failed: ["repair_provider_initialization", "rerun_provider_canary"],
  provider_qualification_required: ["run_provider_canary"],
  provider_qualification_failed: ["inspect_failed_provider_probe", "rerun_provider_canary"],
  provider_authentication_required: ["authenticate_provider"],
  provider_authentication_unconfirmed: ["check_provider_authentication"]
};
const PROVIDER_CAPABILITY_REMEDIATION: Readonly<Record<ProviderCapabilityRefusalCode, readonly string[]>> = {
  provider_resume_unavailable: ["start_new_run"],
  provider_skill_projection_unavailable: ["remove_skill_requirement", "use_manual_workflow"],
  provider_monetary_budget_unavailable: ["use_provider_unit_budget", "choose_monetary_budget_profile"]
};
const SAFE_MODEL = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$/;
const SAFE_TIMESTAMP = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{6}))?Z$/;
const TRACKER_SURFACE_STATES = new Set<TrackerSurfaceState>([
  "loading",
  "empty",
  "ready",
  "partial",
  "stale",
  "error"
]);
const TRACKER_MAX_TASKS = 512;
const TRACKER_MAX_EDGES = 4096;
const TRACKER_MAX_RUNS = 2000;
const TRACKER_MAX_ATTENTION = 4000;
const TRACKER_IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$/;
const TRACKER_TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/;
const TRACKER_TASK_STATES = new Set([
  "ready", "assigned", "active", "blocked", "completed", "review", "accepted", "cancelled"
]);
const TRACKER_READINESS_STATES = new Set([
  "ready", "blocked", "terminal_dependency_failure", "policy_unknown", "in_progress",
  "human_attention", "complete", "cancelled", "unknown"
]);
const TRACKER_RUN_PHASES = new Set([
  "requested", "reserved", "launching", "running", "cancellation_requested", "input_needed",
  "succeeded", "failed", "cancelled", "timed_out", "needs_operator", "unknown"
]);
const TRACKER_NEXT_ACTIONS = new Set([
  "wait_for_run", "start_ready_work", "resolve_dependencies", "answer_operator_request",
  "inspect_failure", "retry_new_run", "request_review", "wait_for_review", "revise_work",
  "accept_task", "inspect_missing_evidence", "none"
]);
const TRACKER_FRESHNESS_STATES = new Set(["fresh", "stale", "unknown"]);
const TRACKER_EVIDENCE_STATES = new Set(["complete", "partial", "missing", "stale"]);
const TRACKER_CAPACITY_STATES = new Set(["available", "saturated", "backpressure", "unknown"]);
const TRACKER_PROVIDERS = new Set(["codex", "claude", "grok"]);
const TRACKER_PROFILES = new Set([
  "codex-builder", "codex-independent-reviewer", "claude-builder", "claude-independent-reviewer",
  "grok-builder", "grok-independent-reviewer"
]);
const TRACKER_ATTENTION_REASONS = new Set([
  ...TRACKER_RUN_PHASES,
  "missing_review", "stale_review", "target_revision_mismatch", "non_independent_review",
  "changes_requested", "review_evidence_missing"
]);
const MAX_LAUNCH_CONTEXT_PACKS = 256;
const MAX_CONTEXT_PACK_SUMMARY_CODE_POINTS = 4096;
const MAX_CONTEXT_PACK_SEMANTIC_BYTES = 65_536;
const CONTEXT_PACK_ID = /^context_pack\.[0-9A-HJKMNP-TV-Z]{26}$/;
const EVENT_ID = /^evt\.[0-9A-HJKMNP-TV-Z]{26}$/;
const CONTEXT_PACK_REF_ID = /^(artifact|finding|task|thread|verification|decision)\.[0-9A-HJKMNP-TV-Z]{26}$/;
const CONTEXT_PACK_SOURCE_KINDS = new Set<ContextPackReferenceKind>([
  "artifact", "finding", "task", "thread", "verification"
]);
const TRACKER_GAPS = new Set([
  "projection_missing", "projection_partial", "projection_stale", "projection_unavailable",
  "attempts_missing", "attempts_partial", "task_malformed", "dependency_missing",
  "terminal_dependency_failure", "dependency_policy_unknown", "dependencies_truncated",
  "focus_input_malformed", "focus_task_missing", "plan_truncated", "edge_limit_exceeded",
  "cycle_detected", "graph_malformed", "graph_truncated", "graph_stale", "resume_gap",
  "capacity_missing", "capacity_malformed", "tracker_snapshot_too_large",
  "tracker_sequence_regressed", "tracker_sequence_reused",
  "missing_review", "stale_review", "target_revision_mismatch", "non_independent_review",
  "changes_requested", "review_evidence_missing"
]);

export class ApiProblem extends Error {
  readonly status: number;
  readonly apiError: ApiError | null;

  constructor(status: number, apiError: ApiError | null) {
    super(apiError?.code ?? "request_unavailable");
    this.name = "ApiProblem";
    this.status = status;
    this.apiError = apiError;
  }
}

function isObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasExactKeys(value: JsonObject, keys: readonly string[]): boolean {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length
    && actual.every((key, index) => key === expected[index]);
}

function sameStrings(left: readonly string[], right: readonly string[]): boolean {
  return left.length === right.length && left.every((item, index) => item === right[index]);
}

function isCanonicalUtcTimestamp(value: unknown): value is string {
  if (typeof value !== "string") {
    return false;
  }
  const match = SAFE_TIMESTAMP.exec(value);
  if (match === null) {
    return false;
  }
  const [year, month, day, hour, minute, second] = match.slice(1, 7).map(Number);
  const fraction = match[7];
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return year >= 1
    && month >= 1 && month <= 12
    && day >= 1 && day <= days[month - 1]
    && hour >= 0 && hour <= 23
    && minute >= 0 && minute <= 59
    && second >= 0 && second <= 59
    && (fraction === undefined || fraction !== "000000");
}

function isSafeNonNegativeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

function stringAt(value: JsonObject, key: string, fallback = ""): string {
  const item = value[key];
  return typeof item === "string" ? item : fallback;
}

function booleanAt(value: JsonObject, key: string, fallback = false): boolean {
  const item = value[key];
  return typeof item === "boolean" ? item : fallback;
}

function stringsAt(value: JsonObject, key: string): readonly string[] {
  const item = value[key];
  return Array.isArray(item) ? item.filter((entry): entry is string => typeof entry === "string") : [];
}

function boundedStringsAt(value: JsonObject, key: string, maximum: number): readonly string[] {
  const raw = value[key];
  if (!Array.isArray(raw) || raw.length > maximum || raw.some((item) => typeof item !== "string")) {
    throw new ApiProblem(502, null);
  }
  return raw;
}

function requiredBooleanAt(value: JsonObject, key: string): boolean {
  const item = value[key];
  if (typeof item !== "boolean") {
    throw new ApiProblem(502, null);
  }
  return item;
}

function parseApiError(value: unknown): ApiError | null {
  if (!isObject(value) || !isObject(value.error)) {
    return null;
  }
  const error = value.error;
  const code = stringAt(error, "code");
  if (!code) {
    return null;
  }
  return {
    code,
    message: stringAt(error, "message"),
    safeNextActions: stringsAt(error, "safe_next_actions")
  };
}

function parseSetup(value: unknown): SetupStatus {
  if (!isObject(value)) {
    throw new ApiProblem(502, null);
  }
  return {
    state: stringAt(value, "state", "setup_unavailable"),
    launchEnabled: booleanAt(value, "launch_enabled"),
    writesEnabled: booleanAt(value, "writes_enabled", true)
  };
}

function parseSetupGuidance(value: unknown): SetupGuidance {
  if (!isObject(value)) {
    throw new ApiProblem(502, null);
  }
  const blockerCode = value.blocker_code;
  const nextActionKey = value.next_action_key;
  const locationLabel = value.location_label;
  const rawTools = value.tools;
  if (
    (blockerCode !== null && (typeof blockerCode !== "string" || !SETUP_GUIDANCE_BLOCKER_CODES.has(blockerCode as SetupGuidanceBlockerCode)))
    || typeof nextActionKey !== "string"
    || !SETUP_GUIDANCE_ACTION_KEYS.has(nextActionKey as SetupGuidanceNextActionKey)
    || (locationLabel !== null && locationLabel !== "workspace_configuration")
    || !Array.isArray(rawTools)
    || rawTools.some((tool) => typeof tool !== "string" || !SETUP_GUIDANCE_TOOLS.has(tool as SetupGuidanceTool))
  ) {
    throw new ApiProblem(502, null);
  }
  return {
    blockerCode: blockerCode as SetupGuidanceBlockerCode | null,
    tools: rawTools as SetupGuidanceTool[],
    nextActionKey: nextActionKey as SetupGuidanceNextActionKey,
    locationLabel
  };
}

function shouldLoadSetupGuidance(setup: SetupStatus): boolean {
  return setup.state !== "setup_uninitialized" && setup.state !== "setup_not_a_repository";
}

function parseMeta(value: unknown): WorkspaceMeta {
  if (!isObject(value)) {
    throw new ApiProblem(502, null);
  }
  const repo = stringAt(value, "repo");
  if (!repo) {
    throw new ApiProblem(502, null);
  }
  return { repo, writesEnabled: booleanAt(value, "writes_enabled") };
}

function profileLabel(profileId: string, profileInfo: unknown): string {
  if (!isObject(profileInfo) || !isObject(profileInfo[profileId])) {
    return profileId;
  }
  const profile = profileInfo[profileId];
  return stringAt(profile, "title", stringAt(profile, "provider", profileId));
}

function parseCatalog(value: unknown): Catalog {
  if (!isObject(value)) {
    throw new ApiProblem(502, null);
  }
  const profiles = stringsAt(value, "profiles").map(
    (id): Profile => ({ id, label: profileLabel(id, value.profile_info) })
  );
  return {
    profiles,
    contextModes: stringsAt(value, "context_modes"),
    grantLevels: stringsAt(value, "grant_levels")
  };
}

function parseRole(value: unknown): RoleOption | null {
  if (!isObject(value)) {
    return null;
  }
  const id = stringAt(value, "id");
  if (!id) {
    return null;
  }
  const contextMode = stringAt(value, "context_mode");
  if (contextMode !== "fresh" && contextMode !== "accumulated") {
    throw new ApiProblem(502, null);
  }
  return {
    id,
    name: stringAt(value, "name", id),
    profileId: stringAt(value, "profile_id"),
    contextMode
  };
}

function parseTask(value: unknown): TaskOption | null {
  if (!isObject(value)) {
    return null;
  }
  const id = stringAt(value, "id");
  if (!id) {
    return null;
  }
  return { id, title: stringAt(value, "title", id), state: stringAt(value, "state") };
}

function parseContextPack(value: unknown): ContextPackOption {
  if (!isObject(value)) {
    throw new ApiProblem(502, null);
  }
  const contextPackId = requiredStringAt(value, "context_pack_id");
  const revision = requiredStringAt(value, "revision");
  const summary = requiredStringAt(value, "summary");
  const factCount = value.fact_count;
  const openQuestionCount = value.open_question_count;
  if (
    !CONTEXT_PACK_ID.test(contextPackId)
    || !EVENT_ID.test(revision)
    || Array.from(summary).length > MAX_CONTEXT_PACK_SUMMARY_CODE_POINTS
    || new TextEncoder().encode(summary).byteLength > MAX_CONTEXT_PACK_SEMANTIC_BYTES
    || typeof factCount !== "number"
    || !Number.isSafeInteger(factCount)
    || factCount < 0
    || factCount > 64
    || typeof openQuestionCount !== "number"
    || !Number.isSafeInteger(openQuestionCount)
    || openQuestionCount < 0
    || openQuestionCount > 32
  ) {
    throw new ApiProblem(502, null);
  }
  return { contextPackId, revision, summary, factCount, openQuestionCount };
}

function boundedCodePointString(value: unknown, maximum: number): string {
  if (
    typeof value !== "string"
    || value.length === 0
    || Array.from(value).length > maximum
    || new TextEncoder().encode(value).byteLength > MAX_CONTEXT_PACK_SEMANTIC_BYTES
  ) {
    throw new ApiProblem(502, null);
  }
  return value;
}

function parseRevisionBoundRef(value: unknown, decisionOnly: boolean): RevisionBoundRef {
  if (!isObject(value) || !isObject(value.ref) || Object.keys(value).length !== 2) {
    throw new ApiProblem(502, null);
  }
  const kind = value.ref.kind;
  const id = value.ref.id;
  const revision = value.revision;
  if (
    typeof kind !== "string"
    || typeof id !== "string"
    || typeof revision !== "string"
    || Object.keys(value.ref).length !== 2
    || !CONTEXT_PACK_REF_ID.test(id)
    || !id.startsWith(`${kind}.`)
    || !EVENT_ID.test(revision)
    || (decisionOnly ? kind !== "decision" : !CONTEXT_PACK_SOURCE_KINDS.has(kind as ContextPackReferenceKind))
  ) {
    throw new ApiProblem(502, null);
  }
  return { kind: kind as ContextPackReferenceKind, id, revision };
}

function parseContextPackFact(value: unknown): ContextPackFact {
  if (!isObject(value) || Object.keys(value).length !== 2) {
    throw new ApiProblem(502, null);
  }
  const refs = boundedArray(value.source_refs, 8);
  if (refs.length === 0) {
    throw new ApiProblem(502, null);
  }
  return {
    statement: boundedCodePointString(value.statement, 1024),
    sourceRefs: refs.map((item) => parseRevisionBoundRef(item, false))
  };
}

export function parseContextPackDetail(value: unknown): ContextPackDetail {
  if (
    !isObject(value)
    || value.schema !== "agent-commons.ui.context-pack.v1"
    || value.state !== "published"
    || typeof value.context_pack_id !== "string"
    || !CONTEXT_PACK_ID.test(value.context_pack_id)
    || typeof value.revision !== "string"
    || !EVENT_ID.test(value.revision)
    || (value.recorded_at !== null && typeof value.recorded_at !== "string")
  ) {
    throw new ApiProblem(502, null);
  }
  const facts = boundedArray(value.facts, 64).map(parseContextPackFact);
  const decisions = boundedArray(value.decision_refs, 32).map((item) => parseRevisionBoundRef(item, true));
  const questions = boundedArray(value.open_questions, 32).map((item) => boundedCodePointString(item, 1024));
  const parsed: ContextPackDetail = {
    schema: "agent-commons.ui.context-pack.v1",
    state: "published",
    contextPackId: value.context_pack_id,
    revision: value.revision,
    recordedAt: value.recorded_at as string | null,
    summary: boundedCodePointString(value.summary, 4096),
    facts,
    decisionRefs: decisions,
    openQuestions: questions
  };
  const semanticWire = {
    summary: parsed.summary,
    facts: parsed.facts.map((fact) => ({
      statement: fact.statement,
      source_refs: fact.sourceRefs.map((ref) => ({
        ref: { kind: ref.kind, id: ref.id },
        revision: ref.revision
      }))
    })),
    decision_refs: parsed.decisionRefs.map((ref) => ({
      ref: { kind: ref.kind, id: ref.id },
      revision: ref.revision
    })),
    open_questions: [...parsed.openQuestions]
  };
  if (new TextEncoder().encode(JSON.stringify(semanticWire)).byteLength > MAX_CONTEXT_PACK_SEMANTIC_BYTES) {
    throw new ApiProblem(502, null);
  }
  return parsed;
}

export function parseContextPackCatalog(value: unknown): ContextPackCatalog {
  if (
    !isObject(value)
    || value.schema !== "agent-commons.ui.context-packs.v1"
    || (value.state !== "empty" && value.state !== "ready")
    || typeof value.truncated !== "boolean"
  ) {
    throw new ApiProblem(502, null);
  }
  const packs = boundedArray(value.packs, 256).map(parseContextPack);
  if ((value.state === "empty") !== (packs.length === 0)) {
    throw new ApiProblem(502, null);
  }
  return {
    schema: "agent-commons.ui.context-packs.v1",
    state: value.state,
    packs,
    truncated: value.truncated
  };
}

function contextPackDraftWire(draft: ContextPackDraft): JsonObject {
  const refWire = (ref: RevisionBoundRef): JsonObject => ({
    ref: { kind: ref.kind, id: ref.id },
    revision: ref.revision
  });
  return {
    summary: draft.summary,
    facts: draft.facts.map((fact) => ({
      statement: fact.statement,
      source_refs: fact.sourceRefs.map(refWire)
    })),
    decision_refs: draft.decisionRefs.map(refWire),
    open_questions: [...draft.openQuestions]
  };
}

export function parseLaunch(value: unknown): LaunchOptions {
  if (!isObject(value)) {
    throw new ApiProblem(502, null);
  }
  const rawRoles = Array.isArray(value.roles) ? value.roles : [];
  const rawTasks = Array.isArray(value.tasks) ? value.tasks : [];
  const rawContextPacks = value.context_packs;
  const rawContextPackStatus = value.context_pack_options_status;
  if (!Array.isArray(rawContextPacks) || rawContextPacks.length > MAX_LAUNCH_CONTEXT_PACKS) {
    throw new ApiProblem(502, null);
  }
  if (!isObject(rawContextPackStatus)) {
    throw new ApiProblem(502, null);
  }
  const freshness = rawContextPackStatus.freshness;
  const truncated = rawContextPackStatus.truncated;
  const refusal = rawContextPackStatus.refusal;
  if (
    freshness !== "current"
    || typeof truncated !== "boolean"
    || (refusal !== null && refusal !== "context_pack_options_truncated")
    || (truncated !== (refusal === "context_pack_options_truncated"))
  ) {
    throw new ApiProblem(502, null);
  }
  return {
    launchEnabled: booleanAt(value, "launch_enabled"),
    roles: rawRoles.map(parseRole).filter((item): item is RoleOption => item !== null),
    tasks: rawTasks.map(parseTask).filter((item): item is TaskOption => item !== null),
    contextPacks: rawContextPacks.map(parseContextPack),
    contextPackOptionsStatus: {
      freshness,
      truncated,
      refusal
    }
  };
}

function parseProviderAuth(value: unknown): ProviderAuthStatus {
  if (!isObject(value)) {
    throw new ApiProblem(502, null);
  }
  const state = stringAt(value, "state") as ProviderAuthState;
  const provider = stringAt(value, "provider");
  const operation = stringAt(value, "operation");
  const freshness = stringAt(value, "freshness");
  const postStartRecovery = stringAt(value, "post_start_recovery");
  const actionIds = stringsAt(value, "action_ids");
  if (
    !PROVIDER_AUTH_STATES.has(state)
    || (provider !== "claude" && provider !== "codex" && provider !== "grok")
    || (operation !== "status" && operation !== "login")
    || (freshness !== "fresh" && freshness !== "stale")
    || postStartRecovery !== "new_run_only"
    || actionIds.some((action) => !PROVIDER_AUTH_ACTIONS.has(action as ProviderAuthAction))
  ) {
    throw new ApiProblem(502, null);
  }
  const profileId = requiredStringAt(value, "profile_id");
  const checkedAt = requiredStringAt(value, "checked_at");
  const freshForSeconds = value.fresh_for_seconds;
  if (typeof freshForSeconds !== "number" || !Number.isFinite(freshForSeconds)) {
    throw new ApiProblem(502, null);
  }
  return {
    profileId,
    provider,
    operation,
    state,
    supported: booleanAt(value, "supported"),
    blocksLaunch: booleanAt(value, "blocks_launch"),
    checkedAt,
    freshness,
    freshForSeconds,
    actionIds: actionIds as ProviderAuthAction[],
    postStartRecovery
  };
}

function parseProviderAvailability(value: unknown): ProviderAvailability {
  if (!isObject(value) || !isObject(value.capabilities) || !isObject(value.qualification)
    || !isObject(value.authentication)
    || !hasExactKeys(value, [
      "profile_id", "provider", "model", "capabilities", "capability_refusals",
      "installation_state", "initialization_state", "qualification", "authentication",
      "launchable", "refusal"
    ])
    || !hasExactKeys(value.capabilities, [
      "mcp", "skills", "resume", "cancellation", "usage_reporting", "sandbox_boundary",
      "budget_units", "context_modes"
    ])
    || !hasExactKeys(value.qualification, ["state", "freshness", "fingerprint", "checked_at"])
    || !hasExactKeys(value.authentication, ["state", "freshness"])) {
    throw new ApiProblem(502, null);
  }
  const profileId = requiredStringAt(value, "profile_id");
  const provider = value.provider;
  const model = value.model;
  const installationState = value.installation_state;
  const initializationState = value.initialization_state;
  const launchable = value.launchable;
  const capabilities = value.capabilities;
  const budgetUnits = capabilities.budget_units;
  const contextModes = capabilities.context_modes;
  const rawCapabilityRefusals = value.capability_refusals;
  const refusal = value.refusal;
  if (
    !PROVIDER_AVAILABILITY_PROFILES.has(profileId)
    || (provider !== "claude" && provider !== "codex" && provider !== "grok")
    || provider !== PROVIDER_BY_PROFILE[profileId]
    || (model !== null && (typeof model !== "string" || !SAFE_MODEL.test(model)))
    || (installationState !== "installed" && installationState !== "unavailable")
    || !["ready", "failed", "passed_unqualified", "not_checked"].includes(String(initializationState))
    || typeof launchable !== "boolean"
    || capabilities.mcp !== true
    || typeof capabilities.skills !== "boolean"
    || capabilities.resume !== "unavailable"
    || typeof capabilities.cancellation !== "string"
    || !PROVIDER_CANCELLATION_MODES.has(capabilities.cancellation)
    || typeof capabilities.usage_reporting !== "string"
    || !PROVIDER_USAGE_REPORTING.has(capabilities.usage_reporting)
    || typeof capabilities.sandbox_boundary !== "string"
    || !PROVIDER_SANDBOX_BOUNDARIES.has(capabilities.sandbox_boundary)
    || !SANDBOXES_BY_PROFILE[profileId].has(capabilities.sandbox_boundary)
    || !Array.isArray(budgetUnits)
    || budgetUnits.length === 0
    || budgetUnits.some((item) => item !== "micro_usd" && item !== "provider_units")
    || new Set(budgetUnits).size !== budgetUnits.length
    || !Array.isArray(contextModes)
    || contextModes.length !== 2
    || contextModes[0] !== "fresh"
    || contextModes[1] !== "accumulated"
    || !Array.isArray(rawCapabilityRefusals)
    || rawCapabilityRefusals.length > 3
  ) {
    throw new ApiProblem(502, null);
  }
  const expectedBudgetUnits = provider === "claude"
    ? ["micro_usd", "provider_units"]
    : ["provider_units"];
  if (!sameStrings(budgetUnits as string[], expectedBudgetUnits)) {
    throw new ApiProblem(502, null);
  }
  const capabilityRefusals = rawCapabilityRefusals.map((item) => {
    if (!isObject(item) || !hasExactKeys(item, ["code", "remediation"])
      || typeof item.code !== "string"
      || !PROVIDER_CAPABILITY_REFUSALS.has(item.code as ProviderCapabilityRefusalCode)) {
      throw new ApiProblem(502, null);
    }
    const remediation = boundedStringsAt(item, "remediation", 4);
    if (!sameStrings(
      remediation,
      PROVIDER_CAPABILITY_REMEDIATION[item.code as ProviderCapabilityRefusalCode]
    )) {
      throw new ApiProblem(502, null);
    }
    return {
      code: item.code as ProviderCapabilityRefusalCode,
      remediation
    };
  });
  const capabilityRefusalCodes = capabilityRefusals.map((item) => item.code);
  if (
    new Set(capabilityRefusalCodes).size !== capabilityRefusalCodes.length
    || capabilityRefusalCodes.filter((code) => code === "provider_resume_unavailable").length !== 1
    || capabilityRefusalCodes.includes("provider_skill_projection_unavailable") === capabilities.skills
    || capabilityRefusalCodes.includes("provider_monetary_budget_unavailable") !== (provider !== "claude")
  ) {
    throw new ApiProblem(502, null);
  }
  const qualificationState = value.qualification.state;
  const qualificationFreshness = value.qualification.freshness;
  const fingerprint = value.qualification.fingerprint;
  const checkedAt = value.qualification.checked_at;
  const authState = value.authentication.state;
  const authFreshness = value.authentication.freshness;
  if (
    !["qualified", "required", "failed"].includes(String(qualificationState))
    || !["current", "missing", "invalid"].includes(String(qualificationFreshness))
    || (fingerprint !== null && (typeof fingerprint !== "string" || !/^[0-9a-f]{64}$/.test(fingerprint)))
    || (checkedAt !== null && !isCanonicalUtcTimestamp(checkedAt))
    || !PROVIDER_AVAILABILITY_AUTH_STATES.has(String(authState))
    || !["fresh", "stale", "unknown"].includes(String(authFreshness))
  ) {
    throw new ApiProblem(502, null);
  }
  let parsedRefusal: ProviderAvailability["refusal"] = null;
  if (refusal !== null) {
    if (!isObject(refusal) || !hasExactKeys(refusal, ["code", "remediation"])
      || typeof refusal.code !== "string"
      || !PROVIDER_AVAILABILITY_REFUSALS.has(refusal.code as ProviderAvailabilityRefusalCode)) {
      throw new ApiProblem(502, null);
    }
    const remediation = boundedStringsAt(refusal, "remediation", 4);
    if (!sameStrings(
      remediation,
      PROVIDER_AVAILABILITY_REMEDIATION[refusal.code as ProviderAvailabilityRefusalCode]
    )) {
      throw new ApiProblem(502, null);
    }
    parsedRefusal = {
      code: refusal.code as ProviderAvailabilityRefusalCode,
      remediation
    };
  }
  const expectedRefusalCode: ProviderAvailabilityRefusalCode | null =
    installationState === "unavailable"
      ? "provider_installation_unavailable"
      : initializationState === "failed"
        ? "provider_initialization_failed"
        : qualificationState === "required"
          ? "provider_qualification_required"
          : qualificationState === "failed"
            ? "provider_qualification_failed"
            : authFreshness !== "fresh"
              ? "provider_authentication_unconfirmed"
              : authState === "authentication_required"
              ? "provider_authentication_required"
              : (authState === "ready" || authState === "unsupported")
                ? null
                : "provider_authentication_unconfirmed";
  if ((parsedRefusal === null ? null : parsedRefusal.code) !== expectedRefusalCode
    || (installationState === "unavailable" && (
      qualificationState !== "failed" || qualificationFreshness !== "invalid"
      || fingerprint !== null || checkedAt !== null || initializationState !== "not_checked"
    ))
    || (qualificationState === "qualified" && (
      qualificationFreshness !== "current" || fingerprint === null || checkedAt === null
      || initializationState !== "ready"
    ))
    || (qualificationState === "required" && (
      qualificationFreshness !== "missing" || fingerprint !== null || checkedAt !== null
      || initializationState !== "not_checked"
    ))
    || (qualificationState === "failed" && qualificationFreshness === "invalid" && (
      fingerprint !== null || checkedAt !== null || initializationState !== "not_checked"
    ))
    || (qualificationState === "failed" && qualificationFreshness === "current" && (
      fingerprint !== null || checkedAt === null
      || (initializationState !== "failed" && initializationState !== "passed_unqualified")
    ))
    || (qualificationState === "failed"
      && qualificationFreshness !== "current" && qualificationFreshness !== "invalid")
    || (authState === "not_checked" ? authFreshness !== "unknown" : authFreshness === "unknown")
    || (profileId === "claude-builder" && capabilities.sandbox_boundary === "none" && (
      qualificationState !== "failed"
      || qualificationFreshness !== "invalid"
      || parsedRefusal?.code !== "provider_qualification_failed"
      || launchable
    ))
  ) {
    throw new ApiProblem(502, null);
  }
  if (launchable !== (parsedRefusal === null && installationState === "installed"
    && initializationState === "ready" && qualificationState === "qualified"
    && authFreshness === "fresh"
    && (authState === "ready" || authState === "unsupported"))) {
    throw new ApiProblem(502, null);
  }
  return {
    profileId,
    provider,
    model,
    capabilities: {
      mcp: capabilities.mcp,
      skills: capabilities.skills,
      resume: "unavailable",
      cancellation: capabilities.cancellation,
      usageReporting: capabilities.usage_reporting,
      sandboxBoundary: capabilities.sandbox_boundary,
      budgetUnits: budgetUnits as string[],
      contextModes: ["fresh", "accumulated"]
    },
    capabilityRefusals,
    installationState: installationState as ProviderAvailability["installationState"],
    initializationState: initializationState as ProviderAvailability["initializationState"],
    qualification: {
      state: qualificationState as ProviderAvailability["qualification"]["state"],
      freshness: qualificationFreshness as ProviderAvailability["qualification"]["freshness"],
      fingerprint,
      checkedAt
    },
    authentication: {
      state: authState as ProviderAvailability["authentication"]["state"],
      freshness: authFreshness as ProviderAvailability["authentication"]["freshness"]
    },
    launchable,
    refusal: parsedRefusal
  };
}

export function parseProviderAvailabilityList(value: unknown): readonly ProviderAvailability[] {
  if (!Array.isArray(value) || value.length > 6) {
    throw new ApiProblem(502, null);
  }
  const parsed = value.map(parseProviderAvailability);
  if (new Set(parsed.map((item) => item.profileId)).size !== parsed.length) {
    throw new ApiProblem(502, null);
  }
  return parsed;
}

function requiredStringAt(value: JsonObject, key: string): string {
  const item = value[key];
  if (typeof item !== "string" || item === "") {
    throw new ApiProblem(502, null);
  }
  return item;
}

function parseStarterPackRole(value: unknown): StarterPackRole {
  if (
    !isObject(value)
    || value.context_mode !== "fresh"
    || !Array.isArray(value.skills)
    || value.skills.some((skill) => typeof skill !== "string" || skill === "")
  ) {
    throw new ApiProblem(502, null);
  }
  return {
    id: requiredStringAt(value, "id"),
    name: requiredStringAt(value, "name"),
    purpose: requiredStringAt(value, "purpose"),
    contextMode: "fresh",
    skills: value.skills as string[]
  };
}

function parseStarterPackBlueprint(value: unknown): StarterPackBlueprint {
  if (!isObject(value) || !Array.isArray(value.roles)) {
    throw new ApiProblem(502, null);
  }
  return {
    id: requiredStringAt(value, "id"),
    title: requiredStringAt(value, "title"),
    summary: requiredStringAt(value, "summary"),
    roles: value.roles.map(parseStarterPackRole)
  };
}

function parseStarterPack(value: unknown): StarterPack {
  if (
    !isObject(value)
    || value.source_kind !== "bundled"
    || value.example !== true
    || !Array.isArray(value.blueprints)
  ) {
    throw new ApiProblem(502, null);
  }
  return {
    id: requiredStringAt(value, "id"),
    version: requiredStringAt(value, "version"),
    title: requiredStringAt(value, "title"),
    summary: requiredStringAt(value, "summary"),
    sourceKind: "bundled",
    example: true,
    blueprints: value.blueprints.map(parseStarterPackBlueprint)
  };
}

function parseStarterPackCatalog(value: unknown): StarterPackCatalog {
  if (!isObject(value) || !Array.isArray(value.packs)) {
    throw new ApiProblem(502, null);
  }
  return { packs: value.packs.map(parseStarterPack) };
}

function nullableNumberAt(value: JsonObject, key: string): number | null {
  const item = value[key];
  if (item === null) {
    return null;
  }
  if (typeof item !== "number" || !Number.isSafeInteger(item) || item < 0) {
    throw new ApiProblem(502, null);
  }
  return item;
}

function boundedArray(value: unknown, maximum: number): readonly unknown[] {
  if (!Array.isArray(value) || value.length > maximum) {
    throw new ApiProblem(502, null);
  }
  return value;
}

function trackerText(value: unknown, maximumBytes: number, allowEmpty = false): string {
  if (
    typeof value !== "string"
    || (!allowEmpty && value === "")
    || value.length > maximumBytes
    || new TextEncoder().encode(value).length > maximumBytes
    || [...value].some((character) => character.charCodeAt(0) < 32)
  ) {
    throw new ApiProblem(502, null);
  }
  return value;
}

function trackerTextAt(
  value: JsonObject,
  key: string,
  maximumBytes: number,
  allowEmpty = false
): string {
  return trackerText(value[key], maximumBytes, allowEmpty);
}

function trackerNullableTextAt(
  value: JsonObject,
  key: string,
  maximumBytes: number
): string | null {
  return value[key] === null ? null : trackerText(value[key], maximumBytes);
}

function trackerIdentifier(value: unknown): string {
  const result = trackerText(value, 256);
  if (!TRACKER_IDENTIFIER.test(result)) {
    throw new ApiProblem(502, null);
  }
  return result;
}

function trackerIdentifierAt(value: JsonObject, key: string): string {
  return trackerIdentifier(value[key]);
}

function trackerNullableIdentifierAt(value: JsonObject, key: string): string | null {
  return value[key] === null ? null : trackerIdentifier(value[key]);
}

function trackerIdentifiersAt(value: JsonObject, key: string, maximum: number): readonly string[] {
  return boundedStringsAt(value, key, maximum).map(trackerIdentifier);
}

function trackerEnumsAt(
  value: JsonObject,
  key: string,
  maximum: number,
  allowed: ReadonlySet<string>
): readonly string[] {
  return boundedStringsAt(value, key, maximum).map((item) => {
    const result = trackerText(item, 64);
    if (!allowed.has(result)) {
      throw new ApiProblem(502, null);
    }
    return result;
  });
}

function trackerEnumAt(value: JsonObject, key: string, allowed: ReadonlySet<string>): string {
  const result = trackerTextAt(value, key, 64);
  if (!allowed.has(result)) {
    throw new ApiProblem(502, null);
  }
  return result;
}

function trackerNullableEnumAt(
  value: JsonObject,
  key: string,
  allowed: ReadonlySet<string>
): string | null {
  return value[key] === null ? null : trackerEnumAt(value, key, allowed);
}

function trackerTimestampAt(value: JsonObject, key: string): string {
  const result = trackerTextAt(value, key, 64);
  if (!TRACKER_TIMESTAMP.test(result)) {
    throw new ApiProblem(502, null);
  }
  return result;
}

function trackerNullableTimestampAt(value: JsonObject, key: string): string | null {
  return value[key] === null ? null : trackerTimestampAt(value, key);
}

function parseTrackerTask(value: unknown): TrackerTask {
  if (!isObject(value)) {
    throw new ApiProblem(502, null);
  }
  return {
    taskId: trackerIdentifierAt(value, "task_id"),
    title: trackerTextAt(value, "title", 300, true),
    taskState: trackerEnumAt(value, "task_state", TRACKER_TASK_STATES),
    readiness: trackerEnumAt(value, "readiness", TRACKER_READINESS_STATES),
    dependencyTaskIds: trackerIdentifiersAt(value, "dependency_task_ids", TRACKER_MAX_TASKS),
    blockingDependencyIds: trackerIdentifiersAt(value, "blocking_dependency_ids", TRACKER_MAX_TASKS),
    ownerSessionId: trackerNullableIdentifierAt(value, "owner_session_id"),
    roleName: trackerNullableTextAt(value, "role_name", 160),
    provider: trackerNullableEnumAt(value, "provider", TRACKER_PROVIDERS),
    profileId: trackerNullableEnumAt(value, "profile_id", TRACKER_PROFILES),
    phase: trackerNullableEnumAt(value, "phase", TRACKER_RUN_PHASES),
    awaitsHuman: requiredBooleanAt(value, "awaits_human"),
    nextAction: trackerEnumAt(value, "next_action", TRACKER_NEXT_ACTIONS),
    freshness: trackerEnumAt(value, "freshness", TRACKER_FRESHNESS_STATES),
    evidenceState: trackerEnumAt(value, "evidence_state", TRACKER_EVIDENCE_STATES),
    gaps: trackerEnumsAt(value, "gaps", 24, TRACKER_GAPS)
  };
}

function parseTrackerEdge(value: unknown): TrackerEdge {
  if (!isObject(value)) {
    throw new ApiProblem(502, null);
  }
  return {
    prerequisiteTaskId: trackerIdentifierAt(value, "prerequisite_task_id"),
    dependentTaskId: trackerIdentifierAt(value, "dependent_task_id"),
    prerequisiteMissing: requiredBooleanAt(value, "prerequisite_missing")
  };
}

function parseTrackerRun(value: unknown): TrackerRun {
  if (!isObject(value)) {
    throw new ApiProblem(502, null);
  }
  return {
    delegationId: trackerIdentifierAt(value, "delegation_id"),
    taskId: trackerNullableIdentifierAt(value, "task_id"),
    agentId: trackerNullableIdentifierAt(value, "agent_id"),
    roleName: trackerNullableTextAt(value, "role_name", 160),
    provider: trackerNullableEnumAt(value, "provider", TRACKER_PROVIDERS),
    profileId: trackerNullableEnumAt(value, "profile_id", TRACKER_PROFILES),
    phase: trackerEnumAt(value, "phase", TRACKER_RUN_PHASES),
    attemptId: trackerNullableIdentifierAt(value, "attempt_id"),
    attemptNumber: nullableNumberAt(value, "attempt_number"),
    startedAt: trackerNullableTimestampAt(value, "started_at"),
    updatedAt: trackerNullableTimestampAt(value, "updated_at"),
    finishedAt: trackerNullableTimestampAt(value, "finished_at"),
    durationSeconds: nullableNumberAt(value, "duration_seconds"),
    awaitsHuman: requiredBooleanAt(value, "awaits_human"),
    nextAction: trackerEnumAt(value, "next_action", TRACKER_NEXT_ACTIONS),
    freshness: trackerEnumAt(value, "freshness", TRACKER_FRESHNESS_STATES),
    evidenceState: trackerEnumAt(value, "evidence_state", TRACKER_EVIDENCE_STATES)
  };
}

function parseTrackerAttention(value: unknown): TrackerAttention {
  if (!isObject(value) || (value.kind !== "run" && value.kind !== "review")) {
    throw new ApiProblem(502, null);
  }
  return {
    kind: value.kind,
    itemId: trackerIdentifierAt(value, "item_id"),
    taskId: trackerNullableIdentifierAt(value, "task_id"),
    reasonCode: trackerEnumAt(value, "reason_code", TRACKER_ATTENTION_REASONS),
    nextAction: trackerEnumAt(value, "next_action", TRACKER_NEXT_ACTIONS)
  };
}

function parseTrackerCapacity(value: unknown): TrackerCapacity {
  if (!isObject(value)) {
    throw new ApiProblem(502, null);
  }
  return {
    state: trackerEnumAt(value, "state", TRACKER_CAPACITY_STATES),
    active: nullableNumberAt(value, "active"),
    limit: nullableNumberAt(value, "limit"),
    queued: nullableNumberAt(value, "queued"),
    queueCapacity: nullableNumberAt(value, "queue_capacity")
  };
}

function parseTrackerFreshness(value: unknown): TrackerFreshness {
  if (!isObject(value)) {
    throw new ApiProblem(502, null);
  }
  return {
    generatedAt: trackerTimestampAt(value, "generated_at"),
    sourceUpdatedAt: trackerNullableTimestampAt(value, "source_updated_at"),
    state: trackerEnumAt(value, "state", TRACKER_FRESHNESS_STATES),
    resumeGap: requiredBooleanAt(value, "resume_gap")
  };
}

export function parseTrackerSnapshot(value: unknown): TrackerSnapshot {
  if (
    !isObject(value)
    || value.schema !== "agent-commons.tracker.v1"
    || typeof value.sequence !== "number"
    || !Number.isSafeInteger(value.sequence)
    || value.sequence < 0
    || typeof value.state !== "string"
    || !TRACKER_SURFACE_STATES.has(value.state as TrackerSurfaceState)
    || value.critical_path_basis !== "dependency_depth_only"
    || value.critical_path_predictive !== false
  ) {
    throw new ApiProblem(502, null);
  }
  return {
    schema: "agent-commons.tracker.v1",
    sequence: value.sequence,
    state: value.state as TrackerSurfaceState,
    tasks: boundedArray(value.tasks, TRACKER_MAX_TASKS).map(parseTrackerTask),
    edges: boundedArray(value.edges, TRACKER_MAX_EDGES).map(parseTrackerEdge),
    runs: boundedArray(value.runs, TRACKER_MAX_RUNS).map(parseTrackerRun),
    attention: boundedArray(value.attention, TRACKER_MAX_ATTENTION).map(parseTrackerAttention),
    capacity: parseTrackerCapacity(value.capacity),
    freshness: parseTrackerFreshness(value.freshness),
    focusTaskIds: trackerIdentifiersAt(value, "focus_task_ids", 64),
    criticalPathTaskIds: trackerIdentifiersAt(value, "critical_path_task_ids", TRACKER_MAX_TASKS),
    criticalPathBasis: "dependency_depth_only",
    criticalPathPredictive: false,
    gaps: trackerEnumsAt(value, "gaps", 24, TRACKER_GAPS)
  };
}

function exchangeCodeFromFragment(): string | null {
  return new URLSearchParams(window.location.hash.slice(1)).get("c");
}

function clearStoredApiBase(): void {
  try {
    window.sessionStorage.removeItem(API_BASE_STORAGE_KEY);
  } catch {
    // A new fragment handoff still works if storage is disabled for this tab.
  }
}

function storedApiBase(): string {
  try {
    const value = window.sessionStorage.getItem(API_BASE_STORAGE_KEY);
    if (value !== null && API_BASE_PATTERN.test(value)) {
      return value;
    }
    if (value !== null) {
      clearStoredApiBase();
    }
  } catch {
    // There is no durable value to restore when storage is disabled.
  }
  return "";
}

function rememberApiBase(value: unknown): string {
  if (typeof value !== "string" || !API_BASE_PATTERN.test(value)) {
    throw new ApiProblem(502, null);
  }
  try {
    window.sessionStorage.setItem(API_BASE_STORAGE_KEY, value);
  } catch {
    // Keep the valid capability in this page's closure instead.
  }
  return value;
}

async function responsePayload(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

export class WorkApi {
  private apiBase = "";

  async connect(signal: AbortSignal): Promise<void> {
    const exchangeCode = exchangeCodeFromFragment();
    window.history.replaceState(null, "", window.location.pathname);

    if (await this.restoreStoredSession(signal)) {
      return;
    }

    if (exchangeCode === null) {
      throw new ApiProblem(401, { code: "unauthorized", message: "", safeNextActions: [] });
    }

    const response = await fetch("/api/auth/exchange", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ code: exchangeCode }),
      signal
    });
    const payload = await responsePayload(response);
    if (!response.ok || !isObject(payload)) {
      clearStoredApiBase();
      throw new ApiProblem(response.status, parseApiError(payload));
    }
    try {
      this.apiBase = rememberApiBase(payload.api_base);
    } catch (error: unknown) {
      clearStoredApiBase();
      throw error;
    }
  }

  private async restoreStoredSession(signal: AbortSignal): Promise<boolean> {
    const storedBase = storedApiBase();
    if (!storedBase) {
      return false;
    }
    this.apiBase = storedBase;
    try {
      await this.get("/setup", signal);
      return true;
    } catch (error: unknown) {
      if (error instanceof DOMException && error.name === "AbortError") {
        throw error;
      }
      if (error instanceof ApiProblem && (error.status === 401 || error.status === 404)) {
        clearStoredApiBase();
        this.apiBase = "";
        return false;
      }
      throw error;
    }
  }

  async load(signal: AbortSignal): Promise<WorkspaceData> {
    const [setupValue, metaValue] = await Promise.all([
      this.get("/setup", signal),
      this.get("/meta", signal)
    ]);
    const setup = parseSetup(setupValue);
    const meta = parseMeta(metaValue);
    const guidance = await this.loadSetupGuidance(setup, signal);
    if (setup.state !== "setup_configured") {
      return {
        meta,
        setup,
        guidance,
        catalog: null,
        launch: null,
        providerAuth: [],
        providerAuthErrors: [],
        providerAvailability: []
      };
    }
    const [catalogResult, launchResult, availabilityResult] = await Promise.allSettled([
      this.get("/catalog", signal),
      this.get("/launch", signal),
      this.get("/work/provider-availability", signal)
    ]);
    if (catalogResult.status === "rejected" && launchResult.status === "rejected") {
      throw catalogResult.reason;
    }
    const catalog = catalogResult.status === "fulfilled" ? parseCatalog(catalogResult.value) : null;
    const launch = launchResult.status === "fulfilled" ? parseLaunch(launchResult.value) : null;
    const providerAvailability = availabilityResult.status === "fulfilled"
      ? parseProviderAvailabilityList(availabilityResult.value)
      : [];
    const profileIds = new Set<string>([
      ...(catalog?.profiles.map((profile) => profile.id) ?? []),
      ...(launch?.roles.map((role) => role.profileId) ?? [])
    ]);
    const orderedProfileIds = [...profileIds];
    const authResults = await Promise.allSettled(
      orderedProfileIds.map(async (profileId) => ({
        profileId,
        status: await this.providerAuthStatus(profileId, signal)
      }))
    );
    return {
      meta,
      setup,
      guidance,
      catalog,
      launch,
      providerAuth: authResults.flatMap((result) => result.status === "fulfilled" ? [result.value.status] : []),
      providerAuthErrors: authResults.flatMap((result, index) => result.status === "rejected" ? [orderedProfileIds[index]] : []),
      providerAvailability
    };
  }

  private async loadSetupGuidance(
    setup: SetupStatus,
    signal: AbortSignal
  ): Promise<SetupGuidance | null> {
    if (!shouldLoadSetupGuidance(setup)) {
      return null;
    }
    try {
      return parseSetupGuidance(await this.get("/work/setup-guidance", signal));
    } catch (error: unknown) {
      if (error instanceof ApiProblem && error.status === 409) {
        return null;
      }
      throw error;
    }
  }

  async loadStarterPacks(signal: AbortSignal): Promise<StarterPackCatalog> {
    return parseStarterPackCatalog(await this.get("/work/starter-packs", signal));
  }

  async loadContextPacks(signal: AbortSignal): Promise<ContextPackCatalog> {
    return parseContextPackCatalog(await this.get("/work/context-packs", signal));
  }

  async loadContextPack(contextPackId: string, signal: AbortSignal): Promise<ContextPackDetail> {
    return parseContextPackDetail(
      await this.get(`/work/context-packs/${encodeURIComponent(contextPackId)}`, signal)
    );
  }

  async publishContextPack(
    draft: ContextPackDraft,
    idempotencyKey: string,
    signal: AbortSignal
  ): Promise<ContextPackDetail> {
    return parseContextPackDetail(await this.post(
      "/work/context-packs",
      { draft: contextPackDraftWire(draft), idempotency_key: idempotencyKey },
      signal
    ));
  }

  async reviseContextPack(
    contextPackId: string,
    expectedRevision: string,
    draft: ContextPackDraft,
    idempotencyKey: string,
    signal: AbortSignal
  ): Promise<ContextPackDetail> {
    return parseContextPackDetail(await this.post(
      `/work/context-packs/${encodeURIComponent(contextPackId)}/revisions`,
      {
        expected_revision: expectedRevision,
        draft: contextPackDraftWire(draft),
        idempotency_key: idempotencyKey
      },
      signal
    ));
  }

  async loadTracker(signal: AbortSignal): Promise<TrackerSnapshot> {
    return parseTrackerSnapshot(await this.get("/work/tracker", signal));
  }

  openTrackerStream(
    onSnapshot: (snapshot: TrackerSnapshot) => void,
    onConnection: (state: "connected" | "disconnected") => void,
    onProtocolError: () => void
  ): () => void {
    if (!this.apiBase) {
      onProtocolError();
      return () => undefined;
    }
    const stream = new EventSource(`${this.apiBase}/work/tracker/stream`, {
      withCredentials: true
    });
    const receive = (event: Event): void => {
      if (!(event instanceof MessageEvent) || typeof event.data !== "string") {
        onProtocolError();
        return;
      }
      try {
        onSnapshot(parseTrackerSnapshot(JSON.parse(event.data) as unknown));
      } catch {
        onProtocolError();
      }
    };
    stream.addEventListener("open", () => onConnection("connected"));
    stream.addEventListener("snapshot", receive);
    stream.addEventListener("error", (event) => {
      if (event instanceof MessageEvent) {
        receive(event);
      } else {
        onConnection("disconnected");
      }
    });
    return () => stream.close();
  }

  async setup(action: "initialize" | "runtime", signal: AbortSignal): Promise<void> {
    const path = action === "initialize" ? "/setup/initialize" : "/setup/runtime-config";
    await this.post(path, {}, signal);
  }

  async createRole(
    input: {
      name: string;
      profileId: string;
      rationale: string;
      contextMode: string;
    },
    signal: AbortSignal
  ): Promise<void> {
    await this.post(
      "/agents",
      {
        name: input.name,
        profile_id: input.profileId,
        rationale: input.rationale,
        context_mode: input.contextMode,
        idempotency_key: crypto.randomUUID()
      },
      signal
    );
  }

  async createTask(
    input: { title: string; description: string; criteria: readonly string[] },
    signal: AbortSignal
  ): Promise<void> {
    await this.post(
      "/tasks",
      {
        title: input.title,
        description: input.description,
        acceptance_criteria: input.criteria,
        idempotency_key: crypto.randomUUID()
      },
      signal
    );
  }

  async providerAuthStatus(profileId: string, signal: AbortSignal): Promise<ProviderAuthStatus> {
    return parseProviderAuth(await this.get(`/provider-auth/${encodeURIComponent(profileId)}`, signal));
  }

  async providerAuthAction(
    profileId: string,
    action: "login" | "cancel" | "check",
    signal: AbortSignal
  ): Promise<ProviderAuthStatus> {
    return parseProviderAuth(
      await this.post(
        `/provider-auth/${encodeURIComponent(profileId)}/${action}`,
        {},
        signal
      )
    );
  }

  async startRun(
    input: {
      agentId: string;
      taskId: string;
      contextPackId: string | null;
      contextPackRevision: string | null;
    },
    idempotencyKey: string,
    signal: AbortSignal
  ): Promise<void> {
    await this.post(
      "/delegations",
      {
        agent_id: input.agentId,
        task_id: input.taskId,
        ...(input.contextPackId === null ? {} : {
          context_pack_id: input.contextPackId,
          context_pack_revision: input.contextPackRevision
        }),
        idempotency_key: idempotencyKey
      },
      signal
    );
  }

  private async get(path: string, signal: AbortSignal): Promise<unknown> {
    return this.request(path, { method: "GET", signal });
  }

  private async post(path: string, body: JsonObject, signal: AbortSignal): Promise<unknown> {
    return this.request(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(body),
      signal
    });
  }

  private async request(path: string, init: RequestInit): Promise<unknown> {
    if (!this.apiBase) {
      throw new ApiProblem(401, { code: "unauthorized", message: "", safeNextActions: [] });
    }
    const response = await fetch(`${this.apiBase}${path}`, {
      credentials: "same-origin",
      ...init
    });
    const payload = await responsePayload(response);
    if (!response.ok) {
      if (response.status === 401 || response.status === 404) {
        clearStoredApiBase();
      }
      throw new ApiProblem(response.status, parseApiError(payload));
    }
    return payload;
  }
}
