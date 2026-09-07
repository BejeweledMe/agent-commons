import type { LibraryDetail, LibraryKind, LibraryRef, LibrarySave, RoleContent, ServiceLibrary, SkillContent, SkillRoute } from "./libraryTypes.js";
import { libraryRefKey } from "./libraryTypes.js";

export type LibraryDraft = {
  kind: LibraryKind;
  id: string;
  name: string;
  description: string;
  instruction: string;
  group: string;
  entrySkill: string;
  coreSkills: string[];
  conditionalSkills: { key: string; when: string }[];
  expectedVersion: string | null;
  forkRef?: LibraryRef;
  retainedSkills: LibraryRef[];
};
export function newLibraryDraft(kind: LibraryKind): LibraryDraft {
  return { kind, id: "", name: "", description: "", instruction: "", group: "custom", entrySkill: "", coreSkills: [], conditionalSkills: [], expectedVersion: null, retainedSkills: [] };
}
export function editLibraryDraft(detail: LibraryDetail, fork = detail.ref.source === "builtin"): LibraryDraft {
  const value = detail.content;
  const role = "system_prompt" in value ? value : null;
  return { kind: detail.ref.kind, id: fork ? `${detail.ref.id.slice(0, 57)}-custom` : detail.ref.id,
    name: value.name, description: value.description, instruction: role ? role.system_prompt : (value as SkillContent).instruction,
    group: role?.group ?? "custom", entrySkill: role ? libraryRefKey(role.entry_skill) : "",
    coreSkills: role?.core_skills.map(libraryRefKey) ?? [],
    conditionalSkills: role?.conditional_skills.map((route) => ({ key: libraryRefKey(route.ref), when: route.when })) ?? [],
    expectedVersion: fork ? null : detail.ref.version, ...(fork ? { forkRef: detail.ref } : {}),
    retainedSkills: role ? [role.entry_skill, ...role.core_skills, ...role.conditional_skills.map((route) => route.ref)] : [] };
}
export function libraryDraftErrors(draft: LibraryDraft, catalog: ServiceLibrary): string[] {
  const errors: string[] = [];
  if (!/^[a-z][a-z0-9-]{0,63}$/.test(draft.id)) errors.push("id");
  if (!draft.name.trim() || draft.name.length > 200) errors.push("name");
  if (draft.description.length > 4000) errors.push("description");
  if (!draft.instruction.trim() || new TextEncoder().encode(draft.instruction).byteLength > 65_536) errors.push("instruction");
  if (draft.group.length > 80) errors.push("group");
  if (draft.kind === "role") {
    const known = new Set([...catalog.skills.map((item) => item.ref), ...draft.retainedSkills].map(libraryRefKey));
    if (!known.has(draft.entrySkill)) errors.push("entrySkill");
    if (draft.coreSkills.length > 8 || draft.coreSkills.some((key) => !known.has(key))) errors.push("coreSkills");
    if (draft.conditionalSkills.length > 32 || new Set(draft.conditionalSkills.map((row) => row.key)).size !== draft.conditionalSkills.length
      || draft.conditionalSkills.some((row) => !known.has(row.key) || !row.when.trim() || row.when.length > 4000)) errors.push("conditionalSkills");
  }
  return errors;
}
export function librarySaveInput(draft: LibraryDraft, catalog: ServiceLibrary): LibrarySave {
  if (libraryDraftErrors(draft, catalog).length) throw new Error("library draft is invalid");
  const skill = (key: string): LibraryRef => {
    const ref = catalog.skills.find((item) => libraryRefKey(item.ref) === key)?.ref ?? draft.retainedSkills.find((ref) => libraryRefKey(ref) === key);
    if (!ref) throw new Error("selected skill revision is unavailable");
    return ref;
  };
  const base = { name: draft.name.trim(), description: draft.description.trim() };
  const content: SkillContent | RoleContent = draft.kind === "skill" ? { ...base, instruction: draft.instruction } : {
    ...base, group: draft.group.trim() || "custom", system_prompt: draft.instruction, entry_skill: skill(draft.entrySkill),
    core_skills: (draft.coreSkills.length ? draft.coreSkills : [draft.entrySkill]).map(skill),
    conditional_skills: draft.conditionalSkills.map((row): SkillRoute => ({ ref: skill(row.key), when: row.when.trim() }))
  };
  return { kind: draft.kind, id: draft.id, expected_version: draft.expectedVersion, content, ...(draft.forkRef ? { fork_ref: draft.forkRef } : {}) };
}

export type LibrarySaveIntent = Readonly<{ input: LibrarySave; key: string }>;
export function freezeLibrarySave(input: LibrarySave, key: string): LibrarySaveIntent {
  return { input: structuredClone(input), key };
}

export type BlueprintRuntimeChoice = { profile_id: string; model: string | null };
export function selectBlueprintProfile<T extends BlueprintRuntimeChoice>(selection: T, profileId: string): T {
  return { ...selection, profile_id: profileId, model: null };
}
