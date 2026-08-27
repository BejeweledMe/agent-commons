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
