import { ApiProblem } from "./api.js";
import type { LibraryApi } from "./libraryApi.js";
import type { BlueprintDefinition, BlueprintSave, LibraryItem, LibraryRef, ServiceLibrary, SkillOrganization, WorkBlueprint } from "./libraryTypes.js";
import { libraryRefKey } from "./libraryTypes.js";

export type LibraryChange =
  | { kind: "group"; input: { id: string; name: { en: string; ru: string }; expected_revision: string } }
  | { kind: "move"; input: { skill: { source: "builtin" | "custom"; id: string }; group_id: string; expected_revision: string } }
  | { kind: "blueprint"; input: BlueprintSave }
  | { kind: "archive"; id: string; input: { expected_version: string; archived: boolean } }
  | { kind: "group_archive"; id: string; input: { expected_revision: string; archived: boolean; move_to_group_id?: string } };
export type LibraryChangeResult = SkillOrganization | WorkBlueprint;
export type MutationSnapshot = Readonly<{ status: "idle" | "saving" | "uncertain" | "refused" | "saved"; operation: Readonly<{ key: string; change: LibraryChange }> | null; result: LibraryChangeResult | null; code: string }>;
function freeze<T>(value: T): T {
  if (value && typeof value === "object") { Object.freeze(value); for (const item of Object.values(value)) freeze(item); }
  return value;
}
export class LibraryMutation {
  private value: MutationSnapshot = { status: "idle", operation: null, result: null, code: "" };
  private listeners = new Set<() => void>();
  private api: LibraryApi | null = null;
  subscribe = (listener: () => void): (() => void) => { this.listeners.add(listener); return () => this.listeners.delete(listener); };
  snapshot = (): MutationSnapshot => this.value;
  private publish(value: MutationSnapshot): void { this.value = value; for (const listener of this.listeners) listener(); }
  reset(): void { if (this.value.status !== "saving" && this.value.status !== "uncertain") this.publish({ status: "idle", operation: null, result: null, code: "" }); }
  async start(change: LibraryChange, api: LibraryApi, key = crypto.randomUUID()): Promise<void> {
    if (this.value.status === "saving" || this.value.status === "uncertain") return;
    this.api = api;
    this.publish({ status: "idle", operation: freeze({ key, change: structuredClone(change) }), result: null, code: "" });
    await this.retry();
  }
  async retry(): Promise<void> {
    if (this.value.status === "saving" || !this.value.operation || !this.api) return;
    const operation = this.value.operation, { change, key } = operation, api = this.api;
    this.publish({ status: "saving", operation, result: null, code: "" });
    try {
      const signal = new AbortController().signal;
      const result = change.kind === "group" ? await api.saveSkillGroup(change.input, key, signal)
        : change.kind === "move" ? await api.moveSkill(change.input, key, signal)
          : change.kind === "group_archive" ? await api.archiveSkillGroup(change.id, change.input, key, signal)
            : change.kind === "blueprint" ? await api.saveBlueprint(change.input, key, signal)
              : await api.archiveBlueprint(change.id, change.input, key, signal);
      // A later catalog refresh is a read, never a reason to replay this write.
      this.publish({ status: "saved", operation: null, result, code: "" });
    } catch (error) {
      const refused = error instanceof ApiProblem && error.status >= 400 && error.status < 500;
      this.publish({ status: refused ? "refused" : "uncertain", operation, result: null, code: error instanceof ApiProblem ? error.apiError?.code ?? "request_unavailable" : "request_unavailable" });
    }
  }
}
const mutations = new Map<string, LibraryMutation>();
export function libraryMutation(key: string): LibraryMutation {
  let value = mutations.get(key); if (!value) { value = new LibraryMutation(); mutations.set(key, value); } return value;
}
export const libraryDraftMemory = new Map<string, unknown>(); // RAM only; never URL or browser storage.

// An archived group is hidden everywhere a skill can land: the active list and every
// move target. Only the archived section below the list may name it, to restore it.
export function moveTargets(organization: SkillOrganization, exclude?: string) {
  return organization.groups.filter((group) => !group.archived && group.id !== exclude);
}
export function archivedGroups(organization: SkillOrganization) {
  return organization.groups.filter((group) => group.archived);
}
export function groupMemberCount(organization: SkillOrganization, groupId: string): number {
  return organization.assignments.filter((assignment) => assignment.group_id === groupId).length;
}
export function groupedSkills(skills: readonly LibraryItem[], organization: SkillOrganization, search: string, locale: "en" | "ru") {
  const needle = search.trim().toLocaleLowerCase(locale);
  return moveTargets(organization).map((group) => ({ group, skills: skills.filter((skill) => organization.assignments.some((assignment) => assignment.id === skill.ref.id && assignment.source === skill.ref.source && assignment.group_id === group.id)).filter((skill) => !needle || `${group.name[locale]} ${skill.name} ${skill.description} ${skill.ref.id}`.toLocaleLowerCase(locale).includes(needle)) })).filter((row) => !needle || row.skills.length || row.group.name[locale].toLocaleLowerCase(locale).includes(needle));
}
export type BlueprintDraft = {
  id: string; expectedVersion: string | null; forkRef?: { source: "builtin" | "custom"; id: string; version: string };
  name: { en: string; ru: string }; description: { en: string; ru: string };
  slots: { id: string; name: string; roleRef: LibraryRef | null }[];
  tasks: BlueprintDefinition["tasks"][number][]; retainedRoles: LibraryRef[];
};
// User-authored text is entered once and never translated. The persisted shape keeps
// both locale slots, so a single value is written into both; a record whose slots
// already differ was authored under the old two-field editor and needs an explicit choice.
export function sameText(value: string): { en: string; ru: string } {
  return { en: value, ru: value };
}
export function sameLines(values: readonly string[]): { en: string[]; ru: string[] } {
  return { en: [...values], ru: [...values] };
}
export function textMismatch(value: { en: string; ru: string }): boolean {
  return value.en !== value.ru;
}
export function linesMismatch(value: { en: readonly string[]; ru: readonly string[] }): boolean {
  return value.en.join("\n") !== value.ru.join("\n");
}
export function blueprintLegacyMismatch(draft: BlueprintDraft): boolean {
  return textMismatch(draft.name) || textMismatch(draft.description)
    || draft.tasks.some((task) => textMismatch(task.title) || textMismatch(task.description) || linesMismatch(task.acceptance_criteria));
}
export function nextBlueprintId(prefix: string, values: readonly { id: string }[]): string {
  let index = 1; while (values.some((value) => value.id === `${prefix}-${index}`)) index++; return `${prefix}-${index}`;
}
export function newBlueprintTask(id = "task-1", slotId = "role-1"): BlueprintDraft["tasks"][number] {
  return { id, slot_id: slotId, title: { en: "", ru: "" }, description: { en: "", ru: "" }, acceptance_criteria: { en: [], ru: [] }, depends_on: [] };
}
export function blueprintDraft(plan?: WorkBlueprint, copy = false): BlueprintDraft {
  if (!plan) return { id: "", expectedVersion: null, name: { en: "", ru: "" }, description: { en: "", ru: "" }, slots: [{ id: "role-1", name: "", roleRef: null }], tasks: [newBlueprintTask()], retainedRoles: [] };
  if (plan.contentAvailable === false) throw new Error("Blueprint content is unavailable");
  const fork = copy || plan.source !== "custom";
  return structuredClone({ id: fork ? `${plan.id.slice(0, 59)}-copy` : plan.id, expectedVersion: fork ? null : plan.version, ...(fork ? { forkRef: { source: plan.source ?? "builtin", id: plan.id, version: plan.version } } : {}), name: plan.name, description: plan.description, slots: plan.slots.map((slot) => ({ id: slot.id, name: slot.name, roleRef: slot.role_ref })), tasks: [...plan.tasks], retainedRoles: plan.slots.map((slot) => slot.role_ref) });
}
const slug = /^[a-z][a-z0-9-]{0,63}$/;
const digest = /^[a-f0-9]{64}$/;
const bytes = (value: string) => new TextEncoder().encode(value).byteLength;
const validText = (value: string, max: number) => Boolean(value.trim()) && bytes(value) <= max && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(value);
export type BlueprintDraftError = "identity" | "text" | "legacy" | "slots" | "roles" | "tasks" | "dependencies" | "cycle";
export function blueprintDraftErrors(draft: BlueprintDraft, catalog: ServiceLibrary, plans: readonly WorkBlueprint[] = []): BlueprintDraftError[] {
  const errors = new Set<BlueprintDraftError>();
  if (!slug.test(draft.id) || (draft.expectedVersion === null && plans.some((plan) => plan.id === draft.id))) errors.add("identity");
  // One entered value fills both slots, so the single text is validated once; slots that
  // still disagree are a legacy record awaiting a choice, never a translation to complete.
  if (blueprintLegacyMismatch(draft)) errors.add("legacy");
  if (!validText(draft.name.en, 800) || !validText(draft.description.en, 16000)) errors.add("text");
  const slots = new Set(draft.slots.map((slot) => slot.id));
  if (!slots.size || slots.size > 32 || slots.size !== draft.slots.length || draft.slots.some((slot) => !slug.test(slot.id) || !validText(slot.name, 800))) errors.add("slots");
  for (const slot of draft.slots) {
    const ref = slot.roleRef;
    if (!ref || ref.kind !== "role" || !slug.test(ref.id) || !digest.test(ref.version) || ![...catalog.roles.map((role) => role.ref), ...draft.retainedRoles].some((item) => libraryRefKey(item) === libraryRefKey(ref))) errors.add("roles");
  }
  const nodes = new Map(draft.tasks.map((task) => [task.id, task]));
  if (!nodes.size || nodes.size > 64 || nodes.size !== draft.tasks.length) errors.add("tasks");
  for (const task of draft.tasks) {
    if (!slug.test(task.id) || !slots.has(task.slot_id)) errors.add("tasks");
    if (!validText(task.title.en, 800) || !validText(task.description.en, 16000) || !task.acceptance_criteria.en.length || task.acceptance_criteria.en.length > 32 || task.acceptance_criteria.en.some((criterion) => !validText(criterion, 16000))) errors.add("text");
    if (task.depends_on.some((id) => !nodes.has(id) || id === task.id) || new Set(task.depends_on).size !== task.depends_on.length) errors.add("dependencies");
  }
  const complete = new Set<string>(), active = new Set<string>();
  const visit = (id: string): void => { if (active.has(id)) { errors.add("cycle"); return; } if (complete.has(id) || !nodes.has(id)) return; active.add(id); for (const dep of nodes.get(id)!.depends_on) visit(dep); active.delete(id); complete.add(id); };
  for (const id of nodes.keys()) visit(id);
  return [...errors];
}
export function blueprintSaveInput(draft: BlueprintDraft): BlueprintSave {
  return structuredClone({ id: draft.id, expected_version: draft.expectedVersion, ...(draft.forkRef ? { fork_ref: draft.forkRef } : {}), definition: { name: draft.name, description: draft.description, slots: draft.slots.map((slot) => ({ id: slot.id, name: slot.name, role_ref: slot.roleRef! })), tasks: draft.tasks } });
}
