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
};

export type TaskOption = {
  id: string;
  title: string;
  state: string;
};

export type LaunchOptions = {
  launchEnabled: boolean;
  roles: readonly RoleOption[];
  tasks: readonly TaskOption[];
};

export type WorkspaceMeta = {
  repo: string;
};

export type WorkspaceData = {
  meta: WorkspaceMeta;
  setup: SetupStatus;
  guidance: SetupGuidance | null;
  catalog: Catalog | null;
  launch: LaunchOptions | null;
};

export type Failure = {
  code: string;
  title: string;
  nextStep: string;
  canRetry: boolean;
  safeNextActions: readonly string[];
};
