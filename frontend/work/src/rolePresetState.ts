import type { RolePreset } from "./contracts";

type RoleRuntimeDraft = { provider: string; profileId: string; model: string; modelMode: "profile" | "explicit" };
export function chooseRoleProvider<T extends RoleRuntimeDraft>(draft: T, provider: string): T {
  return { ...draft, provider, profileId: "", model: "", modelMode: "profile" };
}
export function chooseRoleProfile<T extends RoleRuntimeDraft>(draft: T, profileId: string): T {
  return { ...draft, profileId, model: "", modelMode: "profile" };
}

export function chooseRolePreset<T extends {
  name: string; profileId: string; contextMode: string; fromPresetId: string;
}>(draft: T, id: string, presets: readonly RolePreset[]): T {
  if (id === "") {
    return { ...draft, fromPresetId: "" };
  }
  const preset = presets.find((candidate) => candidate.id === id);
  if (!preset) {
    return draft;
  }
  return {
    ...draft,
    fromPresetId: preset.id,
    name: draft.name || preset.name,
    profileId: preset.profileId,
    contextMode: preset.contextMode
  };
}
