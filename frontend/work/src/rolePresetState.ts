import type { RolePreset } from "./contracts";

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
