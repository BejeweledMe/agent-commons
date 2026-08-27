import type {
  ApiError,
  Catalog,
  JsonObject,
  LaunchOptions,
  Profile,
  RoleOption,
  SetupGuidance,
  SetupGuidanceBlockerCode,
  SetupGuidanceNextActionKey,
  SetupGuidanceTool,
  SetupStatus,
  TaskOption,
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
  return { repo };
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
  return { id, name: stringAt(value, "name", id), profileId: stringAt(value, "profile_id") };
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

function parseLaunch(value: unknown): LaunchOptions {
  if (!isObject(value)) {
    throw new ApiProblem(502, null);
  }
  const rawRoles = Array.isArray(value.roles) ? value.roles : [];
  const rawTasks = Array.isArray(value.tasks) ? value.tasks : [];
  return {
    launchEnabled: booleanAt(value, "launch_enabled"),
    roles: rawRoles.map(parseRole).filter((item): item is RoleOption => item !== null),
    tasks: rawTasks.map(parseTask).filter((item): item is TaskOption => item !== null)
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
      return { meta, setup, guidance, catalog: null, launch: null };
    }
    const [catalogResult, launchResult] = await Promise.allSettled([
      this.get("/catalog", signal),
      this.get("/launch", signal)
    ]);
    if (catalogResult.status === "rejected" && launchResult.status === "rejected") {
      throw catalogResult.reason;
    }
    return {
      meta,
      setup,
      guidance,
      catalog: catalogResult.status === "fulfilled" ? parseCatalog(catalogResult.value) : null,
      launch: launchResult.status === "fulfilled" ? parseLaunch(launchResult.value) : null
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

  async startRun(input: { agentId: string; taskId: string }, signal: AbortSignal): Promise<void> {
    await this.post(
      "/delegations",
      {
        agent_id: input.agentId,
        task_id: input.taskId,
        idempotency_key: crypto.randomUUID()
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
