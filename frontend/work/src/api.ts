import { attachmentPath, boundedAttachmentBlob, conversationPath } from "./conversationTransport.js";
import type {
  ApiError,
  Catalog,
  ContextPackOption,
  ContextSourceCatalog,
  ContextSourceOption,
  TaskCreateResult,
  TaskDetail,
  ContextPackCatalog,
  ContextPackDetail,
  ContextPackDraft,
  ContextPackFact,
  ContextPackReferenceKind,
  DesignPackageOption,
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
  RolePreset,
  RoleCreateResult,
  SetupGuidance,
  SetupGuidanceBlockerCode,
  SetupGuidanceNextActionKey,
  SetupGuidanceTool,
  SetupStatus,
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
import { sanitizedWorkLocation } from "./appRouteState.js";
import { validateContextPackDraft } from "./contextPackDraftValidation.js";
import type { LibraryRef } from "./libraryTypes.js";

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
const TRACKER_SOURCE_REVISION = /^sha256:[0-9a-f]{64}$/;
const TRACKER_ATTENTION_REASONS = new Set([
  ...TRACKER_RUN_PHASES,
  "missing_review", "stale_review", "target_revision_mismatch", "non_independent_review",
  "changes_requested", "review_evidence_missing"
]);
const MAX_LAUNCH_CONTEXT_PACKS = 256;
const MAX_LAUNCH_DESIGN_PACKAGES = 256;
const MAX_CONTEXT_PACK_SUMMARY_CODE_POINTS = 4096;
const MAX_CONTEXT_PACK_SEMANTIC_BYTES = 65_536;
const MAX_DESIGN_PACKAGE_TITLE_CODE_POINTS = 256;
const CONTEXT_PACK_ID = /^context_pack\.[0-9A-HJKMNP-TV-Z]{26}$/;
const DESIGN_PACKAGE_ID = /^design_package\.[0-9A-HJKMNP-TV-Z]{26}$/;
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
  const label = stringAt(profile, "title", stringAt(profile, "provider", profileId));
  return label === profileId ? profileId : `${label} · ${profileId}`;
}

export function parseCatalog(value: unknown): Catalog {
  if (!isObject(value)) {
    throw new ApiProblem(502, null);
  }
  const profiles = stringsAt(value, "profiles").map(
    (id): Profile => {
      const info = isObject(value.profile_info) && isObject(value.profile_info[id]) ? value.profile_info[id] : {};
      return { id, label: profileLabel(id, value.profile_info), provider: stringAt(info, "provider", id.split("-")[0]), model: typeof info.model === "string" ? info.model : null,
        ...(isObject(value.profile_info) ? { configured: typeof info.provider === "string" } : {}) };
    }
  );
  const presets = value.presets ?? [];
  if (!Array.isArray(presets) || presets.length > 512) {
    throw new ApiProblem(502, null);
  }
  return {
    profiles,
    presets: presets.map((value): RolePreset => {
      const role = parseRole(value);
      if (!role || !isObject(value) || value.template !== true || value.state !== "active") {
        throw new ApiProblem(502, null);
      }
      return { ...role, skills: boundedStringsAt(value, "skills", 128) };
    }),
    contextModes: stringsAt(value, "context_modes"),
    grantLevels: stringsAt(value, "grant_levels"),
    modelOptions: isObject(value.model_options) ? Object.fromEntries(Object.entries(value.model_options)
      .filter(([provider, models]) => ["codex", "claude", "grok"].includes(provider) && Array.isArray(models) && models.length <= 128)
      .map(([provider, models]) => [provider, (models as unknown[]).filter((model): model is string => typeof model === "string" && SAFE_MODEL.test(model))])) : {}
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
    contextMode,
    model: typeof value.model === "string" ? value.model : null,
    specializationRef: roleSpecializationRef(value.specialization_ref)
  };
}

function roleSpecializationRef(value: unknown): LibraryRef | null {
  if (value === undefined || value === null) return null;
  if (!isObject(value) || value.kind !== "role" || !["builtin", "custom"].includes(String(value.source))
    || typeof value.id !== "string" || !/^[a-z][a-z0-9-]{0,63}$/.test(value.id)
    || typeof value.version !== "string" || !/^[a-f0-9]{64}$/.test(value.version)) throw new ApiProblem(502, null);
  return { kind: "role", source: value.source as "builtin" | "custom", id: value.id, version: value.version };
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

function parseDesignPackage(value: unknown): DesignPackageOption {
  if (!isObject(value)) {
    throw new ApiProblem(502, null);
  }
  const designPackageId = requiredStringAt(value, "design_package_id");
  const revision = requiredStringAt(value, "revision");
  const title = requiredStringAt(value, "title");
  const screenCount = value.screen_count;
  if (
    !DESIGN_PACKAGE_ID.test(designPackageId)
    || !EVENT_ID.test(revision)
    || Array.from(title).length > MAX_DESIGN_PACKAGE_TITLE_CODE_POINTS
    || new TextEncoder().encode(title).byteLength > MAX_CONTEXT_PACK_SEMANTIC_BYTES
    || typeof screenCount !== "number"
    || !Number.isSafeInteger(screenCount)
    || screenCount < 1
    || screenCount > 64
  ) {
    throw new ApiProblem(502, null);
  }
  return { designPackageId, revision, title, screenCount };
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
  const validated = validateContextPackDraft(draft);
  const refWire = (ref: RevisionBoundRef): JsonObject => ({
    ref: { kind: ref.kind, id: ref.id },
    revision: ref.revision
  });
  return {
    summary: validated.summary,
    facts: validated.facts.map((fact) => ({
      statement: fact.statement,
      source_refs: fact.sourceRefs.map(refWire)
    })),
    decision_refs: validated.decisionRefs.map(refWire),
    open_questions: [...validated.openQuestions]
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
  const rawDesignPackages = value.design_packages;
  const rawDesignPackageStatus = value.design_package_options_status;
  if (!Array.isArray(rawContextPacks) || rawContextPacks.length > MAX_LAUNCH_CONTEXT_PACKS) {
    throw new ApiProblem(502, null);
  }
  if (
    !Array.isArray(rawDesignPackages)
    || rawDesignPackages.length > MAX_LAUNCH_DESIGN_PACKAGES
  ) {
    throw new ApiProblem(502, null);
  }
  if (!isObject(rawContextPackStatus) || !isObject(rawDesignPackageStatus)) {
    throw new ApiProblem(502, null);
  }
  const freshness = rawContextPackStatus.freshness;
  const truncated = rawContextPackStatus.truncated;
  const refusal = rawContextPackStatus.refusal;
  const designFreshness = rawDesignPackageStatus.freshness;
  const designTruncated = rawDesignPackageStatus.truncated;
  const designRefusal = rawDesignPackageStatus.refusal;
  if (
    freshness !== "current"
    || typeof truncated !== "boolean"
    || (refusal !== null && refusal !== "context_pack_options_truncated")
    || (truncated !== (refusal === "context_pack_options_truncated"))
    || designFreshness !== "current"
    || typeof designTruncated !== "boolean"
    || (designRefusal !== null && designRefusal !== "design_package_options_truncated")
    || (designTruncated !== (designRefusal === "design_package_options_truncated"))
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
    },
    designPackages: rawDesignPackages.map(parseDesignPackage),
    designPackageOptionsStatus: {
      freshness: designFreshness,
      truncated: designTruncated,
      refusal: designRefusal
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
  const suggestionKeys = ["suggested_agent_id", "suggested_role_name", "suggested_provider"];
  const hasSuggestion = suggestionKeys.some((key) => key in value);
  let suggestedAgentId: string | null = null;
  let suggestedRoleName: string | null = null;
  let suggestedProvider: TrackerTask["suggestedProvider"] = null;
  if (hasSuggestion) {
    if (!suggestionKeys.every((key) => key in value) || typeof value.suggested_agent_id !== "string"
      || !/^agent\.[0-9A-HJKMNP-TV-Z]{26}$/.test(value.suggested_agent_id)) throw new ApiProblem(502, null);
    suggestedAgentId = value.suggested_agent_id;
    suggestedRoleName = trackerTextAt(value, "suggested_role_name", 160);
    suggestedProvider = trackerEnumAt(value, "suggested_provider", new Set(["codex", "claude", "grok"])) as TrackerTask["suggestedProvider"];
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
    suggestedAgentId, suggestedRoleName, suggestedProvider,
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
    // Additive field: a payload from a server that predates it is still valid
    // and reads as "limit not provided", never as a default.
    wallTimeSeconds: "wall_time_seconds" in value ? nullableNumberAt(value, "wall_time_seconds") : null,
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
    || (value.source_revision !== null && (
      typeof value.source_revision !== "string"
      || !TRACKER_SOURCE_REVISION.test(value.source_revision)
    ))
    || typeof value.truncated !== "boolean"
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
    sourceRevision: value.source_revision,
    truncated: value.truncated,
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

function parseTaskRevisionFromGraph(value: unknown, taskId: string): string {
  if (!isObject(value)) {
    throw new ApiProblem(502, null);
  }
  for (const node of boundedArray(value.nodes, TRACKER_MAX_TASKS + TRACKER_MAX_RUNS)) {
    if (!isObject(node) || node.kind !== "task" || node.id !== taskId) {
      continue;
    }
    const revision = node.effective_revision ?? node.revision;
    if (typeof revision !== "string" || !EVENT_ID.test(revision)) {
      throw new ApiProblem(502, null);
    }
    return revision;
  }
  throw new ApiProblem(409, {
    code: "tracker_task_revision_unavailable",
    message: "the selected task is no longer present in the current graph",
    safeNextActions: ["Refresh the tracker and select the current task before retrying."]
  });
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

const TASK_ID = /^task\.[0-9A-HJKMNP-TV-Z]{26}$/;
const TRUNCATED_TEXT = /…\[truncated\]$/;
const TRUNCATED_ITEMS = /^\[truncated: \d+ items omitted\]$/;

export function parseTaskCreateResult(value: unknown): TaskCreateResult {
  if (!isObject(value) || value.event_type !== "task.created" || !isObject(value.entity_ref)
    || value.entity_ref.kind !== "task" || typeof value.entity_ref.id !== "string"
    || !TASK_ID.test(value.entity_ref.id) || typeof value.revision !== "string"
    || !EVENT_ID.test(value.revision) || value.event_id !== value.revision) {
    throw new ApiProblem(502, null);
  }
  return { taskId: value.entity_ref.id, revision: value.revision };
}

export function parseTaskDetail(value: unknown, taskId: string): TaskDetail {
  if (!TASK_ID.test(taskId) || !isObject(value)
    || value.schema !== "agent_commons.ui.entity.v1" || value.kind !== "task"
    || value.id !== taskId || !isObject(value.record)) {
    throw new ApiProblem(502, null);
  }
  const record = value.record;
  const revision = record.effective_revision ?? record.revision;
  if (record.id !== taskId || typeof revision !== "string" || !EVENT_ID.test(revision)
    || typeof record.state !== "string" || !TRACKER_TASK_STATES.has(record.state)) {
    throw new ApiProblem(502, null);
  }
  let truncated = false;
  let remainingTextBytes = 65_536;
  function detailText(raw: unknown): string {
    if (typeof raw !== "string" || new TextEncoder().encode(raw).byteLength > 4096) {
      throw new ApiProblem(502, null);
    }
    remainingTextBytes -= new TextEncoder().encode(raw).byteLength;
    if (remainingTextBytes < 0) throw new ApiProblem(502, null);
    if (TRUNCATED_TEXT.test(raw)) truncated = true;
    return raw;
  }
  function detailArray(raw: unknown): unknown[] {
    if (!Array.isArray(raw) || raw.length > 33
      || (raw.length === 33 && (typeof raw[32] !== "string" || !TRUNCATED_ITEMS.test(raw[32])))) {
      throw new ApiProblem(502, null);
    }
    return raw.filter((item, index) => {
      if (typeof item === "string" && TRUNCATED_ITEMS.test(item)) {
        if (index !== raw.length - 1) throw new ApiProblem(502, null);
        truncated = true;
        return false;
      }
      return true;
    });
  }
  const title = detailText(record.title);
  const description = detailText(record.description);
  const acceptanceCriteria = detailArray(record.acceptance_criteria).map(detailText);
  const summary = record.summary === undefined ? null : detailText(record.summary);
  const evidenceRefs = detailArray(record.artifact_bindings ?? []).map((raw): RevisionBoundRef => {
    if (!isObject(raw) || !isObject(raw.ref) || raw.ref.kind !== "artifact"
      || typeof raw.ref.id !== "string" || !/^artifact\.[0-9A-HJKMNP-TV-Z]{26}$/.test(raw.ref.id)
      || typeof raw.revision !== "string" || !EVENT_ID.test(raw.revision)) {
      throw new ApiProblem(502, null);
    }
    return { kind: "artifact", id: raw.ref.id, revision: raw.revision };
  });
  return { taskId, revision, title, description, acceptanceCriteria,
    state: record.state, summary, evidenceRefs, truncated };
}

export function parseContextSourceCatalog(value: unknown): ContextSourceCatalog {
  if (!isObject(value) || !hasExactKeys(value, ["schema", "state", "sources", "truncated"])
    || value.schema !== "agent-commons.ui.context-sources.v1"
    || (value.state !== "empty" && value.state !== "ready")
    || typeof value.truncated !== "boolean") throw new ApiProblem(502, null);
  const seen = new Set<string>();
  const sources = boundedArray(value.sources, 256).map((raw): ContextSourceOption => {
    if (!isObject(raw) || !hasExactKeys(raw, ["ref", "label", "freshness"])
      || !isObject(raw.ref) || !hasExactKeys(raw.ref, ["kind", "id", "revision"])
      || raw.freshness !== "current" || typeof raw.label !== "string" || raw.label.trim().length === 0
      || new TextEncoder().encode(raw.label).byteLength > 256
      || /[\p{C}\p{Zl}\p{Zp}]/u.test(raw.label) || /[^\S ]/u.test(raw.label)
      || typeof raw.ref.kind !== "string" || typeof raw.ref.id !== "string"
      || typeof raw.ref.revision !== "string" || !CONTEXT_PACK_REF_ID.test(raw.ref.id)
      || !raw.ref.id.startsWith(`${raw.ref.kind}.`) || !EVENT_ID.test(raw.ref.revision)
      || (raw.ref.kind !== "decision" && !CONTEXT_PACK_SOURCE_KINDS.has(raw.ref.kind as ContextPackReferenceKind))
      || seen.has(raw.ref.id)) throw new ApiProblem(502, null);
    seen.add(raw.ref.id);
    return { ref: { kind: raw.ref.kind as ContextPackReferenceKind, id: raw.ref.id, revision: raw.ref.revision },
      label: raw.label, freshness: "current" };
  });
  if ((sources.length === 0) !== (value.state === "empty")) throw new ApiProblem(502, null);
  return { schema: "agent-commons.ui.context-sources.v1", state: value.state, sources, truncated: value.truncated };
}

export class WorkApi {
  private taskWrites = new Map<string, { signature: string; body: Promise<string> }>();
  private apiBase = "";

  /** A scoped client is immutable: an in-flight request can never follow a later project selection. */
  constructor(private readonly scopedProjectId: string | null = null, apiBase = "") {
    this.apiBase = apiBase;
  }

  forProject(projectId: string): WorkApi {
    if (!/^project\.[a-f0-9]{32}$/.test(projectId) || !this.apiBase) {
      throw new ApiProblem(400, { code: "project_invalid", message: "", safeNextActions: [] });
    }
    return new WorkApi(projectId, this.apiBase);
  }

  async hostRequestData(path: string, options: { method?: "GET" | "POST"; body?: unknown; signal: AbortSignal }): Promise<unknown> {
    if (options.method === "POST") return this.hostPost(path, options.body ?? {}, options.signal);
    return this.hostGet(path, options.signal);
  }

  forgetTaskWrite(key: string): void {
    this.taskWrites.delete(key);
  }

  private async postTaskWrite(
    path: string,
    input: JsonObject,
    key: string,
    signal: AbortSignal,
    taskId?: string
  ): Promise<unknown> {
    // Capture semantic input before the first await. A retry owns the exact
    // serialized body, including its original CAS, even after server commit.
    const signature = JSON.stringify([path, input]);
    let saved = this.taskWrites.get(key);
    if (saved && saved.signature !== signature) {
      throw new ApiProblem(409, { code: "task_retry_intent_changed", message: "", safeNextActions: [] });
    }
    if (!saved) {
      const owned = JSON.parse(JSON.stringify(input)) as JsonObject;
      const body = (async () => {
        const revision = taskId === undefined ? undefined : await this.currentTaskRevision(taskId, signal);
        return JSON.stringify({
          ...owned,
          ...(revision === undefined ? {} : { expected_revision: revision }),
          idempotency_key: key
        });
      })();
      saved = { signature, body };
      this.taskWrites.set(key, saved);
      // A failed read did not prepare or send a write and can safely be retried.
      void body.catch(() => this.taskWrites.delete(key));
    }
    return this.request(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: await saved.body,
      signal
    });
  }

  async connect(signal: AbortSignal): Promise<void> {
    const exchangeCode = exchangeCodeFromFragment();
    window.history.replaceState(null, "", sanitizedWorkLocation(window.location.pathname, window.location.search));

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
      // A project host has no unscoped /setup. A legacy host deliberately
      // answers 404 here and remains a valid authenticated fallback.
      await this.hostGet("/projects", signal);
      return true;
    } catch (error: unknown) {
      if (error instanceof DOMException && error.name === "AbortError") {
        throw error;
      }
      if (error instanceof ApiProblem && error.status === 401) {
        clearStoredApiBase();
        this.apiBase = "";
        return false;
      }
      if (error instanceof ApiProblem && error.status === 404) return true;
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

  async loadContextSources(signal: AbortSignal): Promise<ContextSourceCatalog> {
    return parseContextSourceCatalog(await this.get("/work/context-sources", signal));
  }

  async loadTaskDetail(taskId: string, signal: AbortSignal): Promise<TaskDetail> {
    if (!TASK_ID.test(taskId)) throw new ApiProblem(400, null);
    return parseTaskDetail(await this.get(`/entities/task/${encodeURIComponent(taskId)}`, signal), taskId);
  }

  async loadTracker(signal: AbortSignal): Promise<TrackerSnapshot> {
    return parseTrackerSnapshot(await this.get("/work/tracker", signal));
  }

  async currentTaskRevision(taskId: string, signal: AbortSignal): Promise<string> {
    return parseTaskRevisionFromGraph(await this.get("/graph", signal), taskId);
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
    const stream = new EventSource(`${this.apiBase}${this.scopedPath("/work/tracker/stream")}`, {
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
      fromPresetId?: string;
      specializationRef?: LibraryRef | null;
      model?: string;
      modelMode?: "profile" | "explicit";
    },
    signal: AbortSignal,
    idempotencyKey = crypto.randomUUID()
  ): Promise<RoleCreateResult> {
    if (input.fromPresetId && input.specializationRef) throw new ApiProblem(400, { code: "role_source_conflict", message: "", safeNextActions: [] });
    const specialization = roleSpecializationRef(input.specializationRef);
    const result = await this.post(
      "/agents",
      {
        name: input.name,
        rationale: input.rationale,
        ...(input.fromPresetId ? { from_preset_id: input.fromPresetId } : {
          profile_id: input.profileId,
          context_mode: input.contextMode,
          ...(specialization ? { specialization_ref: specialization } : {}),
          ...(input.modelMode === "explicit" && input.model?.trim() ? { model: input.model.trim() } : {})
        }),
        idempotency_key: idempotencyKey
      },
      signal
    );
    if (!isObject(result) || !isObject(result.entity_ref) || result.entity_ref.kind !== "agent"
      || typeof result.entity_ref.id !== "string" || !/^agent\.[0-9A-HJKMNP-TV-Z]{26}$/.test(result.entity_ref.id)) throw new ApiProblem(502, null);
    return { agentId: result.entity_ref.id };
  }

  async createTask(
    input: {
      title: string;
      description: string;
      criteria: readonly string[];
      dependencyIds: readonly string[];
    },
    signal: AbortSignal,
    idempotencyKey: string
  ): Promise<TaskCreateResult> {
    return parseTaskCreateResult(await this.postTaskWrite(
      "/tasks",
      {
        title: input.title,
        description: input.description,
        acceptance_criteria: [...input.criteria],
        dependencies: [...input.dependencyIds]
      },
      idempotencyKey,
      signal
    ));
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
      designPackageId: string | null;
      designPackageRevision: string | null;
      wallTimeSeconds: number;
    },
    idempotencyKey: string,
    signal: AbortSignal
  ): Promise<void> {
    await this.post(
      "/delegations",
      {
        agent_id: input.agentId,
        task_id: input.taskId,
        wall_time_seconds: input.wallTimeSeconds,
        ...(input.contextPackId === null ? {} : {
          context_pack_id: input.contextPackId,
          context_pack_revision: input.contextPackRevision
        }),
        ...(input.designPackageId === null ? {} : {
          design_package_id: input.designPackageId,
          design_package_revision: input.designPackageRevision
        }),
        idempotency_key: idempotencyKey
      },
      signal
    );
  }

  async requestTaskReview(
    taskId: string,
    criteria: readonly string[],
    idempotencyKey: string,
    signal: AbortSignal
  ): Promise<void> {
    await this.postTaskWrite(
      `/tasks/${encodeURIComponent(taskId)}/review-request`,
      { criteria: criteria.map((item) => item.trim()).filter((item) => item.length > 0) },
      idempotencyKey, signal, taskId
    );
  }

  async acceptTask(
    taskId: string,
    summary: string,
    idempotencyKey: string,
    signal: AbortSignal
  ): Promise<void> {
    await this.postTaskWrite(
      `/tasks/${encodeURIComponent(taskId)}/accept`,
      { summary: summary.trim() }, idempotencyKey, signal, taskId
    );
  }

  async reopenTask(
    taskId: string,
    reason: string,
    idempotencyKey: string,
    signal: AbortSignal
  ): Promise<void> {
    await this.postTaskWrite(
      `/tasks/${encodeURIComponent(taskId)}/reopen`,
      { reason: reason.trim() }, idempotencyKey, signal, taskId
    );
  }

  /** The workspace graph projection; the board keeps only its role structure. */
  async readGraph(signal: AbortSignal): Promise<unknown> {
    return this.get("/graph", signal);
  }

  /** The operator's board arrangement for this project: operational state, never a canonical fact. */
  async readBoardLayout(signal: AbortSignal): Promise<unknown> {
    return this.get("/board", signal);
  }

  async writeBoardLayout(body: JsonObject, signal: AbortSignal): Promise<unknown> {
    return this.post("/board", body, signal);
  }

  /** Open a recorded link between two standing roles; the ledger, not the canvas, holds the edge. */
  async openAgentLink(input: { fromAgentId: string; toAgentId: string; allowedAction: "ask" | "delegate"; reason: string }, signal: AbortSignal, idempotencyKey = crypto.randomUUID()): Promise<unknown> {
    const agent = /^agent\.[0-7][0-9A-HJKMNP-TV-Z]{25}$/;
    if (!agent.test(input.fromAgentId) || !agent.test(input.toAgentId) || input.fromAgentId === input.toAgentId) throw new ApiProblem(400, null);
    return this.post("/agent-links", {
      from_agent_id: input.fromAgentId, to_agent_id: input.toAgentId, allowed_action: input.allowedAction,
      reason: input.reason, idempotency_key: idempotencyKey
    }, signal);
  }

  async readOutputs(kind: "task" | "agent", id: string, versions: "latest" | "all", summary: boolean, signal: AbortSignal): Promise<unknown> {
    if ((kind !== "task" && kind !== "agent") || !new RegExp(`^${kind}\\.[0-7][0-9A-HJKMNP-TV-Z]{25}$`).test(id)
      || (versions !== "latest" && versions !== "all")) throw new ApiProblem(400, null);
    const query = new URLSearchParams({ scope_kind: kind, scope_id: id });
    if (!summary) query.set("versions", versions);
    return this.get(`/outputs${summary ? "/summary" : ""}?${query}`, signal);
  }

  async readOutputImage(artifactId: string, signal: AbortSignal): Promise<Blob> {
    if (!/^artifact\.[0-7][0-9A-HJKMNP-TV-Z]{25}$/.test(artifactId)) throw new ApiProblem(400, null);
    if (!this.apiBase) throw new ApiProblem(401, null);
    const response = await fetch(`${this.apiBase}${this.scopedPath(`/artifacts/${encodeURIComponent(artifactId)}/preview`)}`, {
      method: "GET", credentials: "same-origin", redirect: "error", signal,
    });
    if (!response.ok) {
      if (response.status === 401) { clearStoredApiBase(); this.apiBase = ""; }
      throw new ApiProblem(response.status, null);
    }
    const type = (response.headers.get("Content-Type") ?? "").split(";", 1)[0].trim();
    if (type !== "image/png" && type !== "image/jpeg") { await response.body?.cancel(); throw new ApiProblem(502, null); }
    const limit = 10 * 1024 * 1024;
    const declared = response.headers.get("Content-Length");
    if (declared !== null && (!/^\d+$/.test(declared) || Number(declared) > limit)) { await response.body?.cancel(); throw new ApiProblem(502, null); }
    if (!response.body) throw new ApiProblem(502, null);
    const reader = response.body.getReader(), chunks: BlobPart[] = [];
    let total = 0;
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        total += value.byteLength;
        if (total > limit || signal.aborted) { await reader.cancel(); throw new ApiProblem(502, null); }
        chunks.push(Uint8Array.from(value));
      }
    } finally { reader.releaseLock(); }
    if (total === 0 || signal.aborted) throw new ApiProblem(502, null);
    return new Blob(chunks, { type });
  }

  async requestConversation(path: string, options: { method: "GET" | "POST"; body?: unknown; operationId?: string; signal: AbortSignal }): Promise<unknown> {
    const file = typeof File !== "undefined" && options.body instanceof File ? options.body : null;
    try { conversationPath(path, options.method, file); } catch { throw new ApiProblem(400, null); }
    if (file) {
      if (!options.operationId || !/^[A-Za-z0-9._-]{1,128}$/.test(options.operationId) || file.size < 1 || file.size > 15_000_000) throw new ApiProblem(400, null);
      return this.request(path, { method: "POST", headers: { "Content-Type": file.type || "application/octet-stream", "Idempotency-Key": options.operationId }, body: file, signal: options.signal, redirect: "error" });
    }
    return this.request(path, options.method === "POST" ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(options.body), signal: options.signal, redirect: "error" } : { method: "GET", signal: options.signal, redirect: "error" });
  }
  async conversationAttachment(thread: string, message: string, attachment: string, signal: AbortSignal): Promise<Blob> {
    let path: string;
    try { path = attachmentPath(thread, message, attachment); } catch { throw new ApiProblem(400, null); }
    if (!this.apiBase) throw new ApiProblem(401, null);
    const response = await fetch(`${this.apiBase}${this.scopedPath(path)}`, { credentials: "same-origin", signal, redirect: "error" });
    if (!response.ok) {
      if (response.status === 401) { clearStoredApiBase(); this.apiBase = ""; }
      throw new ApiProblem(response.status, parseApiError(await responsePayload(response)));
    }
    try { return await boundedAttachmentBlob(response); } catch { throw new ApiProblem(502, null); }
  }

  async requestData(path: string, options: { method?: "GET" | "POST"; body?: unknown; signal: AbortSignal }): Promise<unknown> {
    let decoded: string;
    const checkedPath = options.method !== "POST" && path === "/library/blueprints?include_archived=true" ? "/library/blueprints" : path;
    try { decoded = decodeURIComponent(checkedPath); } catch { throw new ApiProblem(400, null); }
    if (!/^\/(library|work)(\/|$)/.test(checkedPath) || /[?#\\\x00-\x1f]/.test(decoded)
      || decoded.split("/").slice(1).some((part) => !part || part === "." || part === "..")) throw new ApiProblem(400, null);
    return this.request(path, options.method === "POST" ? {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(options.body), signal: options.signal
    } : { method: "GET", signal: options.signal });
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

  private scopedPath(path: string): string {
    if (this.scopedProjectId === null) return path;
    return `/projects/${encodeURIComponent(this.scopedProjectId)}${path}`;
  }

  private async hostGet(path: string, signal: AbortSignal): Promise<unknown> {
    return this.hostRequest(path, { signal });
  }

  private async hostPost(path: string, body: unknown, signal: AbortSignal): Promise<unknown> {
    return this.hostRequest(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal });
  }

  private async hostRequest(path: string, init: RequestInit): Promise<unknown> {
    if (!this.apiBase) {
      throw new ApiProblem(401, { code: "unauthorized", message: "", safeNextActions: [] });
    }
    const response = await fetch(`${this.apiBase}${path}`, {
      credentials: "same-origin",
      ...init
    });
    const payload = await responsePayload(response);
    if (!response.ok) {
      if (response.status === 401) {
        clearStoredApiBase();
        this.apiBase = "";
      }
      throw new ApiProblem(response.status, parseApiError(payload));
    }
    return payload;
  }

  private async request(path: string, init: RequestInit): Promise<unknown> {
    return this.hostRequest(this.scopedPath(path), init);
  }
}
