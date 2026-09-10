import type { ReactElement } from "react";

import type { RolePreset } from "../contracts";
import type { MessageKey } from "../i18n";

export function RolePresetPicker({ presets, value, onChange, text }: {
  presets: readonly RolePreset[];
  value: string;
  onChange: (id: string) => void;
  text: (key: MessageKey) => string;
}): ReactElement {
  const selected = presets.find((preset) => preset.id === value);
  return (
    <div className="role-preset-picker">
      <label htmlFor="role-preset">{text("role_preset")}</label>
      <select aria-describedby="role-preset-help" id="role-preset" value={value}
        onChange={(event) => onChange(event.currentTarget.value)}>
        <option value="">{text("role_preset_none")}</option>
        {presets.map((preset) => (
          <option key={preset.id} value={preset.id}>{preset.name} — {preset.profileId}</option>
        ))}
      </select>
      <p className="small-copy" id="role-preset-help">{text("role_preset_help")}</p>
      {selected ? <p className="small-copy" role="status">
        {text("library_skills")}: {selected.skills.map((skill) => (
          <code key={skill} className="skill-chip">{skill}</code>
        ))}
      </p> : null}
    </div>
  );
}
