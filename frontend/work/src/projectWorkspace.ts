import { ApiProblem } from "./api.js";

export type ProjectState = "ready" | "missing" | "identity_conflict";
export type ProjectRecord = Readonly<{
  id: string;
  workspaceId: string;
  name: string;
  revision: string;
  archived: boolean;
  available: boolean;
  state: ProjectState;
}>;
export type ProjectList = Readonly<{
  revision: string;
  defaultProjectId: string | null;
  readOnly: boolean;
  projects: readonly ProjectRecord[];
}>;
export type ProjectInspection = Readonly<{
  inspectionId: string;
  name: string;
  mode: "new" | "existing";
  initializationRequired: boolean;
  expiresAt: string;
}>;

const PROJECT_ID = /^project\.[a-f0-9]{32}$/;
const WORKSPACE_ID = /^workspace\.[0-7][0-9A-HJKMNP-TV-Z]{25}$/;
const REGISTRY_REVISION = /^sha256:[a-f0-9]{64}$/;
const INSPECTION_ID = /^inspection\.[a-f0-9]{32}$/;
const TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/;

function malformed(): never { throw new ApiProblem(502, null); }
function object(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return malformed();
  return value as Record<string, unknown>;
}
function exact(value: Record<string, unknown>, fields: readonly string[]): void {
  const received = Object.keys(value).sort();
  const expected = [...fields].sort();
  if (received.length !== expected.length || received.some((key, index) => key !== expected[index])) malformed();
}
function text(value: unknown, max: number): string {
  if (typeof value !== "string" || !value.trim() || new TextEncoder().encode(value).byteLength > max) malformed();
  return value;
}
// Registry names are bounded in Unicode code points by the host, rather than
// by UTF-8 bytes. Keep the compatible byte ceiling for a closed browser DTO.
function projectName(value: unknown): string {
  if (typeof value !== "string" || !value.trim() || [...value].length > 160
    || new TextEncoder().encode(value).byteLength > 640) malformed();
  return value;
}
export function projectId(value: unknown): string {
  if (typeof value !== "string" || !PROJECT_ID.test(value)) malformed();
  return value;
}

function project(value: unknown): ProjectRecord {
  const item = object(value);
  exact(item, ["id", "workspace_id", "name", "revision", "archived", "available", "state"]);
  if (!WORKSPACE_ID.test(text(item.workspace_id, 64)) || !REGISTRY_REVISION.test(text(item.revision, 80))
    || typeof item.archived !== "boolean" || typeof item.available !== "boolean"
    || (item.state !== "ready" && item.state !== "missing" && item.state !== "identity_conflict")) malformed();
  return { id: projectId(item.id), workspaceId: item.workspace_id as string, name: projectName(item.name),
    revision: item.revision as string, archived: item.archived, available: item.available, state: item.state };
}

export function parseProjectList(value: unknown): ProjectList {
  const payload = object(value);
  exact(payload, ["schema", "revision", "default_project_id", "read_only", "projects"]);
  if (payload.schema !== "agent_commons.project_list.v1" || !REGISTRY_REVISION.test(text(payload.revision, 80))
    || typeof payload.read_only !== "boolean" || !Array.isArray(payload.projects) || payload.projects.length > 64
    || !(payload.default_project_id === null || PROJECT_ID.test(payload.default_project_id as string))) malformed();
  const projects = payload.projects.map(project);
  if (new Set(projects.map((item) => item.id)).size !== projects.length) malformed();
  return { revision: payload.revision as string, defaultProjectId: payload.default_project_id as string | null,
    readOnly: payload.read_only, projects };
}

export function parseProjectInspection(value: unknown): ProjectInspection {
  const payload = object(value);
  exact(payload, ["inspection_id", "name", "mode", "initialization_required", "expires_at"]);
  if (!INSPECTION_ID.test(text(payload.inspection_id, 64)) || (payload.mode !== "new" && payload.mode !== "existing")
    || typeof payload.initialization_required !== "boolean" || !TIMESTAMP.test(text(payload.expires_at, 64))) malformed();
  return { inspectionId: payload.inspection_id as string, name: projectName(payload.name), mode: payload.mode,
    initializationRequired: payload.initialization_required, expiresAt: payload.expires_at as string };
}

export type ProjectTransport = {
  hostRequestData(path: string, options: { method?: "GET" | "POST"; body?: unknown; signal: AbortSignal }): Promise<unknown>;
};

export class ProjectRegistryApi {
  constructor(private readonly transport: ProjectTransport) {}
  list(signal: AbortSignal): Promise<ProjectList> {
    return this.transport.hostRequestData("/projects", { signal }).then(parseProjectList);
  }
  inspect(input: { mode: "new" | "existing"; path: string; name: string }, signal: AbortSignal): Promise<ProjectInspection> {
    return this.transport.hostRequestData("/projects/inspect", { method: "POST", body: input, signal }).then(parseProjectInspection);
  }
  async create(inspectionId: string, idempotencyKey: string, signal: AbortSignal): Promise<{ project: ProjectRecord; revision: string }> {
    const payload = object(await this.transport.hostRequestData("/projects", { method: "POST", body: { inspection_id: inspectionId, idempotency_key: idempotencyKey }, signal }));
    exact(payload, ["project", "revision"]);
    const record = project(payload.project);
    if (!REGISTRY_REVISION.test(text(payload.revision, 80))) malformed();
    return { project: record, revision: payload.revision as string };
  }
  async update(id: string, expectedRevision: string, input: { name: string; archived: boolean }, idempotencyKey: string, signal: AbortSignal): Promise<{ project: ProjectRecord; revision: string }> {
    const payload = object(await this.transport.hostRequestData(`/projects/${encodeURIComponent(projectId(id))}/update`, {
      method: "POST", body: { expected_revision: expectedRevision, name: input.name, archived: input.archived, idempotency_key: idempotencyKey }, signal
    }));
    exact(payload, ["project", "revision"]);
    const record = project(payload.project);
    if (!REGISTRY_REVISION.test(text(payload.revision, 80))) malformed();
    return { project: record, revision: payload.revision as string };
  }
}

/** Owns only browser lifetime. It never writes drafts or opaque ids to storage. */
export class ProjectSelection {
  private generation = 0;
  private controller = new AbortController();
  select(): { generation: number; signal: AbortSignal } {
    this.controller.abort();
    this.controller = new AbortController();
    this.generation += 1;
    return { generation: this.generation, signal: this.controller.signal };
  }
  isCurrent(generation: number): boolean { return this.generation === generation && !this.controller.signal.aborted; }
  currentGeneration(): number { return this.generation; }
  abort(): void { this.controller.abort(); }
}

/** Latest-read ownership is separate from the selected project's epoch. */
export class ProjectReadRequest {
  private generation = 0;
  private controller: AbortController | null = null;
  begin(): { generation: number; signal: AbortSignal } {
    this.controller?.abort();
    this.controller = new AbortController();
    this.generation += 1;
    return { generation: this.generation, signal: this.controller.signal };
  }
  isCurrent(generation: number): boolean {
    return this.generation === generation && this.controller !== null && !this.controller.signal.aborted;
  }
  abort(): void { this.controller?.abort(); }
}

/** Drafts live in RAM under their project identity and are intentionally never persisted. */
export class ProjectDraftStore<T> {
  private readonly drafts = new Map<string, T>();
  get(id: string, fallback: T): T { return this.drafts.get(projectId(id)) ?? fallback; }
  set(id: string, draft: T): void { this.drafts.set(projectId(id), draft); }
}
