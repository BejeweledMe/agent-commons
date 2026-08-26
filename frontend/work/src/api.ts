import type {
  ApiError,
  Catalog,
  JsonObject,
  LaunchOptions,
  Profile,
  RoleOption,
  SetupStatus,
  TaskOption,
  WorkspaceData
} from "./contracts";

const API_BASE_STORAGE_KEY = "agent_commons.ui.api_base";
const API_BASE_PATTERN = /^\/api\/[A-Za-z0-9_-]{32,128}$/;

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
    this.apiBase = storedApiBase();
    window.history.replaceState(null, "", window.location.pathname);
    if (exchangeCode === null) {
      if (!this.apiBase) {
        throw new ApiProblem(401, { code: "unauthorized", message: "", safeNextActions: [] });
      }
      return;
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

  async load(signal: AbortSignal): Promise<WorkspaceData> {
    const setup = parseSetup(await this.get("/setup", signal));
    if (setup.state !== "setup_configured") {
      return { setup, catalog: null, launch: null };
    }
    const [catalogResult, launchResult] = await Promise.allSettled([
      this.get("/catalog", signal),
      this.get("/launch", signal)
    ]);
    if (catalogResult.status === "rejected" && launchResult.status === "rejected") {
      throw catalogResult.reason;
    }
    return {
      setup,
      catalog: catalogResult.status === "fulfilled" ? parseCatalog(catalogResult.value) : null,
      launch: launchResult.status === "fulfilled" ? parseLaunch(launchResult.value) : null
    };
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
