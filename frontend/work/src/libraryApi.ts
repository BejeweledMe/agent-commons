import { ApiProblem } from "./api.js";
import type { LibraryDetail, LibraryFile, LibraryItem, LibraryKind, LibraryRef, LibraryRole, LibrarySave, RoleContent, ServiceLibrary, SkillContent, SkillRoute } from "./libraryTypes.js";
import { libraryRefKey } from "./libraryTypes.js";
import type { BlueprintApplication, BlueprintApplyInput, BlueprintText, WorkBlueprint } from "./libraryTypes.js";

export type AuthenticatedRequest = {
  requestData(path: string, options: { method?: "GET" | "POST"; body?: unknown; signal: AbortSignal }): Promise<unknown>;
};
const digest = /^[a-f0-9]{64}$/;
const slug = /^[a-z][a-z0-9-]{0,63}$/;
const encoder = new TextEncoder();
const fail = (): never => { throw new ApiProblem(502, null); };
export function blueprintBriefIsValid(value: unknown): value is string {
  return typeof value === "string" && Boolean(value.trim()) && [...value.trim()].length <= 4000;
}
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return fail();
  return value as Record<string, unknown>;
}
function text(value: unknown, max: number, nonempty = false): string {
  if (typeof value !== "string" || encoder.encode(value).byteLength > max || (nonempty && !value.trim())) return fail();
  return value;
}
function list(value: unknown, max: number): unknown[] {
  if (!Array.isArray(value) || value.length > max) return fail();
  return value;
}
export function parseLibraryRef(value: unknown, kind?: LibraryKind): LibraryRef {
  const ref = object(value);
  if ((ref.kind !== "role" && ref.kind !== "skill") || (kind && ref.kind !== kind)
    || (ref.source !== "builtin" && ref.source !== "custom")
    || typeof ref.id !== "string" || !slug.test(ref.id)
    || typeof ref.version !== "string" || !digest.test(ref.version)) return fail();
  return { kind: ref.kind, source: ref.source, id: ref.id, version: ref.version };
}
function skillRefs(value: unknown, max: number): LibraryRef[] {
  const refs = list(value, max).map((item) => parseLibraryRef(item, "skill"));
  if (new Set(refs.map(libraryRefKey)).size !== refs.length) return fail();
  return refs;
}
function routes(value: unknown): SkillRoute[] {
  const result = list(value, 32).map((item) => {
    const route = object(item);
    return { ref: parseLibraryRef(route.ref, "skill"), when: text(route.when, 16_000, true) };
  });
  if (new Set(result.map((route) => libraryRefKey(route.ref))).size !== result.length) return fail();
  return result;
}
function item(value: unknown, kind: LibraryKind): LibraryItem {
  const row = object(value);
  return { ref: parseLibraryRef(row.ref, kind), name: text(row.name, 800, true), description: text(row.description, 16_000) };
}
export function parseServiceLibrary(value: unknown): ServiceLibrary {
  const data = object(value);
  if (data.schema !== "agent_commons.ui.library.v1" || typeof data.editing_enabled !== "boolean") return fail();
  const roles = list(data.roles, 600).map((row): LibraryRole => {
    const raw = object(row);
    return { ...item(row, "role"), group: text(raw.group, 320), entry_skill: parseLibraryRef(raw.entry_skill, "skill"), core_skills: skillRefs(raw.core_skills, 8), conditional_skills: routes(raw.conditional_skills) };
  });
  const skills = list(data.skills, 600).map((row) => item(row, "skill"));
  if (new Set([...roles, ...skills].map((row) => libraryRefKey(row.ref))).size !== roles.length + skills.length) return fail();
  return { roles, skills, editingEnabled: data.editing_enabled };
}
function content(value: unknown, kind: LibraryKind): SkillContent | RoleContent {
  const raw = object(value);
  const base = { name: text(raw.name, 800, true), description: text(raw.description, 16_000) };
  if (kind === "skill") return { ...base, instruction: text(raw.instruction, 65_536, true) };
  return { ...base, group: text(raw.group ?? "custom", 320), system_prompt: text(raw.system_prompt, 65_536, true), entry_skill: parseLibraryRef(raw.entry_skill, "skill"), core_skills: skillRefs(raw.core_skills ?? [raw.entry_skill], 8), conditional_skills: routes(raw.conditional_skills ?? []) };
}
function file(value: unknown): LibraryFile {
  const raw = object(value);
  const path = text(raw.path, 1024, true);
  if (path.startsWith("/") || path.split("/").some((part) => !part || part === "." || part === "..")
    || /[\\\x00-\x1f]/.test(path) || typeof raw.sha256 !== "string" || !digest.test(raw.sha256)
    || !Number.isSafeInteger(raw.size) || Number(raw.size) < 0) return fail();
  return { path, sha256: raw.sha256, size: Number(raw.size) };
}
export function parseLibraryDetail(value: unknown, expected?: LibraryRef): LibraryDetail {
  const raw = object(value);
  const ref = parseLibraryRef(raw.ref);
  if (expected && libraryRefKey(ref) !== libraryRefKey(expected)) return fail();
  return { ref, content: content(raw.content, ref.kind), files: list(raw.files ?? [], 512).map(file) };
}

function translated(value: unknown): BlueprintText {
  const raw = object(value); return { en: text(raw.en, 16_000, true), ru: text(raw.ru, 16_000, true) };
}
function identifier(value: unknown): string {
  if (typeof value !== "string" || !slug.test(value)) return fail(); return value;
}
export function parseWorkBlueprints(value: unknown): WorkBlueprint[] {
  const raw = object(value);
  if (raw.schema !== "agent_commons.library-blueprints.v1") return fail();
  const blueprints = list(raw.blueprints, 20).map((value): WorkBlueprint => {
    const item = object(value);
    if (typeof item.version !== "string" || !digest.test(item.version)) return fail();
    const slots = list(item.slots, 32).map((value) => { const slot = object(value); return { id: identifier(slot.id), name: text(slot.name, 800, true), role_ref: parseLibraryRef(slot.role_ref, "role") }; });
    const tasks = list(item.tasks, 64).map((value) => {
      const task = object(value); const criteria = object(task.acceptance_criteria);
      return { id: identifier(task.id), title: translated(task.title), description: translated(task.description),
        acceptance_criteria: { en: list(criteria.en, 32).map((value) => text(value, 16_000, true)), ru: list(criteria.ru, 32).map((value) => text(value, 16_000, true)) },
        depends_on: list(task.depends_on, 64).map(identifier), slot_id: identifier(task.slot_id) };
    });
    const ids = new Set(tasks.map((task) => task.id));
    if (!slots.length || !tasks.length || new Set(slots.map((slot) => slot.id)).size !== slots.length || ids.size !== tasks.length
      || tasks.some((task) => !slots.some((slot) => slot.id === task.slot_id) || task.depends_on.some((id) => !ids.has(id) || id === task.id))) return fail();
    const visited = new Set<string>();
    const visit = (id: string, ancestors: Set<string>): void => { if (ancestors.has(id)) return fail(); if (visited.has(id)) return; const next = new Set(ancestors).add(id); for (const dep of tasks.find((task) => task.id === id)!.depends_on) visit(dep, next); visited.add(id); };
    for (const task of tasks) visit(task.id, new Set());
    return { id: identifier(item.id), version: item.version, name: translated(item.name), description: translated(item.description), slots, tasks };
  });
  if (new Set(blueprints.map((item) => item.id)).size !== blueprints.length) return fail();
  return blueprints;
}
export function parseBlueprintApplication(value: unknown, expectedId: string): BlueprintApplication {
  const raw = object(value);
  const entity = (value: unknown, kind: "agent" | "task"): string => { if (typeof value !== "string" || !new RegExp(`^${kind}\\.[0-9A-HJKMNP-TV-Z]{26}$`).test(value)) return fail(); return value; };
  if (raw.schema !== "agent_commons.blueprint-application.v1" || raw.state !== "created" || raw.blueprint_id !== expectedId) return fail();
  const roles = list(raw.roles, 32).map((value) => { const role = object(value); return { slotId: identifier(role.slot_id), agentId: entity(role.agent_id, "agent") }; });
  const tasks = list(raw.tasks, 64).map((value) => { const task = object(value); return { nodeId: identifier(task.node_id), taskId: entity(task.task_id, "task"), agentId: entity(task.agent_id, "agent") }; });
  if (!tasks.length || !roles.length || tasks.some((task) => !roles.some((role) => role.agentId === task.agentId))) return fail();
  return { blueprintId: expectedId, roles, tasks };
}

export class LibraryApi {
  constructor(private readonly api: AuthenticatedRequest) {}
  async load(signal: AbortSignal): Promise<ServiceLibrary> {
    return parseServiceLibrary(await this.api.requestData("/library", { signal }));
  }
  async detail(ref: LibraryRef, signal: AbortSignal): Promise<LibraryDetail> {
    parseLibraryRef(ref);
    return parseLibraryDetail(await this.api.requestData(`/library/${libraryRefKey(ref)}`, { signal }), ref);
  }
  async save(input: LibrarySave, key: string, signal: AbortSignal): Promise<LibraryDetail> {
    if (!slug.test(input.id)) throw new ApiProblem(400, { code: "library_invalid_id", message: "", safeNextActions: [] });
    const result = parseLibraryDetail(await this.api.requestData(`/library/${input.kind}`, {
      method: "POST", signal,
      body: { id: input.id, expected_version: input.expected_version, idempotency_key: key, content: input.content, ...(input.fork_ref ? { fork_ref: input.fork_ref } : {}) }
    }));
    if (result.ref.kind !== input.kind || result.ref.source !== "custom" || result.ref.id !== input.id) return fail();
    return result;
  }
  async readFile(ref: LibraryRef, selected: LibraryFile, signal: AbortSignal): Promise<string> {
    parseLibraryRef(ref, "skill");
    file(selected);
    const path = selected.path.split("/").map(encodeURIComponent).join("/");
    const raw = object(await this.api.requestData(`/library/${libraryRefKey(ref)}/files/${path}`, { signal }));
    if (raw.path !== selected.path || raw.sha256 !== selected.sha256) return fail();
    return text(raw.text, 262_144);
  }
  async blueprints(signal: AbortSignal): Promise<WorkBlueprint[]> {
    return parseWorkBlueprints(await this.api.requestData("/library/blueprints", { signal }));
  }
  async applyBlueprint(id: string, input: BlueprintApplyInput, key: string, signal: AbortSignal): Promise<BlueprintApplication> {
    identifier(id);
    if (!blueprintBriefIsValid(input.brief)) throw new ApiProblem(400, { code: "blueprint_invalid", message: "", safeNextActions: [] });
    return parseBlueprintApplication(await this.api.requestData(`/library/blueprints/${id}/apply`, { method: "POST", signal, body: {
      expected_version: input.expected_version, title: input.title, brief: input.brief, locale: input.locale, bindings: input.bindings, idempotency_key: key
    } }), id);
  }
}
