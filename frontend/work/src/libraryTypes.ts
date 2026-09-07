export type LibraryKind = "role" | "skill";
export type LibraryRef = Readonly<{
  kind: LibraryKind;
  source: "builtin" | "custom";
  id: string;
  version: string;
}>;
export type SkillRoute = Readonly<{ ref: LibraryRef; when: string }>;
export type LibraryItem = Readonly<{ ref: LibraryRef; name: string; description: string }>;
export type LibraryRole = LibraryItem & Readonly<{
  group: string;
  entry_skill: LibraryRef;
  core_skills: readonly LibraryRef[];
  conditional_skills: readonly SkillRoute[];
}>;
export type ServiceLibrary = Readonly<{
  roles: readonly LibraryRole[];
  skills: readonly LibraryItem[];
  editingEnabled: boolean;
}>;
export type SkillContent = Readonly<{ name: string; description: string; instruction: string }>;
export type RoleContent = Readonly<{
  name: string;
  description: string;
  group: string;
  system_prompt: string;
  entry_skill: LibraryRef;
  core_skills: readonly LibraryRef[];
  conditional_skills: readonly SkillRoute[];
}>;
export type LibraryFile = Readonly<{ path: string; sha256: string; size: number }>;
export type LibraryDetail = Readonly<{
  ref: LibraryRef;
  content: SkillContent | RoleContent;
  files: readonly LibraryFile[];
}>;
export type LibrarySave = Readonly<{
  kind: LibraryKind;
  id: string;
  expected_version: string | null;
  fork_ref?: LibraryRef;
  content: SkillContent | RoleContent;
}>;
export type LibraryState =
  | { kind: "loading" }
  | { kind: "ready"; catalog: ServiceLibrary }
  | { kind: "error"; catalog?: ServiceLibrary };

export function libraryRefKey(ref: LibraryRef): string {
  return `${ref.kind}/${ref.source}/${ref.id}/${ref.version}`;
}

export function sameLibraryRef(a: LibraryRef | null | undefined, b: LibraryRef | null | undefined): boolean {
  return Boolean(a && b && libraryRefKey(a) === libraryRefKey(b));
}

export type BlueprintText = Readonly<{ en: string; ru: string }>;
export type WorkBlueprint = Readonly<{
  id: string; version: string; name: BlueprintText; description: BlueprintText;
  slots: readonly { id: string; name: string; role_ref: LibraryRef }[];
  tasks: readonly { id: string; title: BlueprintText; description: BlueprintText; acceptance_criteria: { en: readonly string[]; ru: readonly string[] }; depends_on: readonly string[]; slot_id: string }[];
}>;
export type BlueprintBinding = Readonly<{ slot_id: string; profile_id: string; model: string | null; name: string }>;
export type BlueprintApplyInput = Readonly<{ expected_version: string; title: string; brief: string; locale: "en" | "ru"; bindings: readonly BlueprintBinding[] }>;
export type BlueprintApplication = Readonly<{
  blueprintId: string; roles: readonly { slotId: string; agentId: string }[];
  tasks: readonly { nodeId: string; taskId: string; agentId: string }[];
}>;
