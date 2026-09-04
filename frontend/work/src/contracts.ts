export type JsonObject = Record<string, unknown>;

export type ApiError = {
  code: string;
  message: string;
  safeNextActions: readonly string[];
};

export type SetupStatus = {
  state: string;
  launchEnabled: boolean;
  writesEnabled: boolean;
};

export type SetupGuidanceTool = "Claude" | "Codex" | "git" | "agent-commons-mcp";

export type SetupGuidanceBlockerCode =
  | "setup_not_a_repository"
  | "setup_uninitialized"
  | "setup_unconfigured"
  | "setup_no_provider_found"
  | "setup_support_binary_unresolved"
  | "setup_config_rejected_by_loader";

export type SetupGuidanceNextActionKey =
  | "choose_git_repository"
  | "initialize_workspace"
  | "install_provider_and_check_again"
  | "install_support_tool_and_check_again"
  | "configure_runtime"
  | "repair_workspace_configuration"
  | "setup_ready";

export type SetupGuidance = {
  blockerCode: SetupGuidanceBlockerCode | null;
  tools: readonly SetupGuidanceTool[];
  nextActionKey: SetupGuidanceNextActionKey;
  locationLabel: "workspace_configuration" | null;
};

export type Profile = {
  id: string;
  label: string;
};

export type Catalog = {
  profiles: readonly Profile[];
  contextModes: readonly string[];
  grantLevels: readonly string[];
};

export type RoleOption = {
  id: string;
  name: string;
  profileId: string;
  contextMode: "fresh" | "accumulated";
};

export type TaskOption = {
  id: string;
  title: string;
  state: string;
};

export type ContextPackOption = {
  contextPackId: string;
  revision: string;
  summary: string;
  factCount: number;
  openQuestionCount: number;
};

export type ContextPackOptionsStatus = {
  freshness: "current";
  truncated: boolean;
  refusal: "context_pack_options_truncated" | null;
};

export type DesignPackageOption = {
  designPackageId: string;
  revision: string;
  title: string;
  screenCount: number;
};

export type DesignPackageOptionsStatus = {
  freshness: "current";
  truncated: boolean;
  refusal: "design_package_options_truncated" | null;
};

export type ContextPackReferenceKind =
  | "artifact"
  | "finding"
  | "task"
  | "thread"
  | "verification"
  | "decision";

export type RevisionBoundRef = {
  kind: ContextPackReferenceKind;
  id: string;
  revision: string;
};

export type ContextPackFact = {
  statement: string;
  sourceRefs: readonly RevisionBoundRef[];
};

export type ContextPackDraft = {
  summary: string;
  facts: readonly ContextPackFact[];
  decisionRefs: readonly RevisionBoundRef[];
  openQuestions: readonly string[];
};

export type ContextPackDetail = ContextPackDraft & {
  schema: "agent-commons.ui.context-pack.v1";
  state: "published";
  contextPackId: string;
  revision: string;
  recordedAt: string | null;
};

export type ContextPackCatalog = {
  schema: "agent-commons.ui.context-packs.v1";
  state: "empty" | "ready";
  packs: readonly ContextPackOption[];
  truncated: boolean;
};

export type LaunchOptions = {
  launchEnabled: boolean;
  roles: readonly RoleOption[];
  tasks: readonly TaskOption[];
  contextPacks: readonly ContextPackOption[];
  contextPackOptionsStatus: ContextPackOptionsStatus;
  designPackages: readonly DesignPackageOption[];
  designPackageOptionsStatus: DesignPackageOptionsStatus;
};

export type ProviderAuthState =
  | "ready"
  | "authentication_required"
  | "authenticating"
  | "timed_out"
  | "cancelled"
  | "failed"
  | "unsupported"
  | "credential_store_unavailable";

export type ProviderAuthAction =
  | "authenticate"
  | "cancel_authentication"
  | "check_again"
  | "continue_launch";

export type ProviderAuthStatus = {
  profileId: string;
  provider: "claude" | "codex" | "grok";
  operation: "status" | "login";
  state: ProviderAuthState;
  supported: boolean;
  blocksLaunch: boolean;
  checkedAt: string;
  freshness: "fresh" | "stale";
  freshForSeconds: number;
  actionIds: readonly ProviderAuthAction[];
  postStartRecovery: "new_run_only";
};

export type ProviderAvailabilityRefusalCode =
  | "provider_installation_unavailable"
  | "provider_initialization_failed"
  | "provider_qualification_required"
  | "provider_qualification_failed"
  | "provider_authentication_required"
  | "provider_authentication_unconfirmed";

export type ProviderCapabilityRefusalCode =
  | "provider_resume_unavailable"
  | "provider_skill_projection_unavailable"
  | "provider_monetary_budget_unavailable";

export type ProviderAvailability = {
  profileId: string;
  provider: "claude" | "codex" | "grok";
  model: string | null;
  capabilities: {
    mcp: boolean;
    skills: boolean;
    resume: "unavailable";
    cancellation: string;
    usageReporting: string;
    sandboxBoundary: string;
    budgetUnits: readonly string[];
    contextModes: readonly ["fresh", "accumulated"];
  };
  capabilityRefusals: readonly {
    code: ProviderCapabilityRefusalCode;
    remediation: readonly string[];
  }[];
  installationState: "installed" | "unavailable";
  initializationState: "ready" | "failed" | "passed_unqualified" | "not_checked";
  qualification: {
    state: "qualified" | "required" | "failed";
    freshness: "current" | "missing" | "invalid";
    fingerprint: string | null;
    checkedAt: string | null;
  };
  authentication: {
    state: ProviderAuthState | "not_checked";
    freshness: "fresh" | "stale" | "unknown";
  };
  launchable: boolean;
  refusal: {
    code: ProviderAvailabilityRefusalCode;
    remediation: readonly string[];
  } | null;
};

export type WorkspaceMeta = {
  repo: string;
  writesEnabled: boolean;
};

export type WorkspaceData = {
  meta: WorkspaceMeta;
  setup: SetupStatus;
  guidance: SetupGuidance | null;
  catalog: Catalog | null;
  launch: LaunchOptions | null;
  providerAuth: readonly ProviderAuthStatus[];
  providerAuthErrors: readonly string[];
  providerAvailability: readonly ProviderAvailability[];
};

export type StarterPackSourceKind = "bundled";

export type StarterPackContextMode = "fresh";

export type StarterPackRole = {
  id: string;
  name: string;
  purpose: string;
  profileId: string;
  contextMode: StarterPackContextMode;
  skills: readonly string[];
};

export type StarterPackBlueprint = {
  id: string;
  title: string;
  summary: string;
  roles: readonly StarterPackRole[];
};

export type StarterPack = {
  id: string;
  version: string;
  title: string;
  summary: string;
  sourceKind: StarterPackSourceKind;
  example: true;
  blueprints: readonly StarterPackBlueprint[];
};

export type StarterPackCatalog = {
  packs: readonly StarterPack[];
};

export type AppliedStarterPackRole = {
  sourceRoleId: string;
  agentId: string;
  revision: string;
  name: string;
  profileId: string;
  contextMode: StarterPackContextMode;
  template: true;
  grants: Readonly<Record<string, "deny">>;
  skills: readonly string[];
};

export type StarterPackApplyResult = {
  packId: string;
  blueprintId: string;
  applied: true;
  roles: readonly AppliedStarterPackRole[];
};

export type Failure = {
  code: string;
  title: string;
  nextStep: string;
  canRetry: boolean;
  safeNextActions: readonly string[];
};

export type TrackerSurfaceState =
  | "loading"
  | "empty"
  | "ready"
  | "partial"
  | "stale"
  | "error";

export type TrackerTask = {
  taskId: string;
  title: string;
  taskState: string;
  readiness: string;
  dependencyTaskIds: readonly string[];
  blockingDependencyIds: readonly string[];
  ownerSessionId: string | null;
  roleName: string | null;
  provider: string | null;
  profileId: string | null;
  phase: string | null;
  awaitsHuman: boolean;
  nextAction: string;
  freshness: string;
  evidenceState: string;
  gaps: readonly string[];
};

export type TrackerEdge = {
  prerequisiteTaskId: string;
  dependentTaskId: string;
  prerequisiteMissing: boolean;
};

export type TrackerRun = {
  delegationId: string;
  taskId: string | null;
  agentId: string | null;
  roleName: string | null;
  provider: string | null;
  profileId: string | null;
  phase: string;
  attemptId: string | null;
  attemptNumber: number | null;
  startedAt: string | null;
  updatedAt: string | null;
  finishedAt: string | null;
  durationSeconds: number | null;
  awaitsHuman: boolean;
  nextAction: string;
  freshness: string;
  evidenceState: string;
};

export type TrackerAttention = {
  kind: "run" | "review";
  itemId: string;
  taskId: string | null;
  reasonCode: string;
  nextAction: string;
};

export type TrackerCapacity = {
  state: string;
  active: number | null;
  limit: number | null;
  queued: number | null;
  queueCapacity: number | null;
};

export type TrackerFreshness = {
  generatedAt: string;
  sourceUpdatedAt: string | null;
  state: string;
  resumeGap: boolean;
};

export type TrackerSnapshot = {
  schema: "agent-commons.tracker.v1";
  sequence: number;
  sourceRevision: string | null;
  truncated: boolean;
  state: TrackerSurfaceState;
  tasks: readonly TrackerTask[];
  edges: readonly TrackerEdge[];
  runs: readonly TrackerRun[];
  attention: readonly TrackerAttention[];
  capacity: TrackerCapacity;
  freshness: TrackerFreshness;
  focusTaskIds: readonly string[];
  criticalPathTaskIds: readonly string[];
  criticalPathBasis: "dependency_depth_only";
  criticalPathPredictive: false;
  gaps: readonly string[];
};
