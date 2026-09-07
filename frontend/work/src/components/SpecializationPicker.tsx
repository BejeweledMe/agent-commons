import { type ReactElement, useMemo, useState } from "react";
import type { MessageKey } from "../i18n.js";
import type { LibraryRef, LibraryRole, LibraryState } from "../libraryTypes.js";
import { libraryRefKey, sameLibraryRef } from "../libraryTypes.js";

export const ROLE_GROUP_KEYS: Readonly<Record<string, MessageKey>> = {
  business_product: "library_group_business", communication: "library_group_communication",
  engineering_leadership: "library_group_leadership", application: "library_group_application",
  data: "library_group_data", ai: "library_group_ai", platform: "library_group_platform",
  security: "library_group_security", custom: "library_group_custom"
};
export function roleGroupName(group: string, text: (key: MessageKey) => string): string {
  return ROLE_GROUP_KEYS[group] ? text(ROLE_GROUP_KEYS[group]) : group;
}
export function SpecializationPicker({ library, value, selectedName, onChange, onRefresh, text }: {
  library: LibraryState; value: LibraryRef | null; selectedName: string;
  onChange: (role: LibraryRole | null) => void; onRefresh: () => void; text: (key: MessageKey) => string;
}): ReactElement {
  const [search, setSearch] = useState("");
  const catalog = library.kind === "loading" ? null : library.catalog;
  const roles = catalog?.roles ?? [];
  const selected = roles.find((role) => sameLibraryRef(role.ref, value));
  const matching = useMemo(() => roles.filter((role) => `${role.name} ${role.description} ${role.ref.id}`.toLowerCase().includes(search.toLowerCase())), [roles, search]);
  const groups = [...new Set(matching.map((role) => role.group))];
  return <div className="specialization-picker">
    <label htmlFor="specialization-search">{text("library_find_role")}</label>
    <input id="specialization-search" type="search" value={search} onChange={(event) => setSearch(event.currentTarget.value)} />
    <label htmlFor="role-specialization">{text("library_specialization")}</label>
    <select id="role-specialization" value={value ? libraryRefKey(value) : ""} aria-describedby="role-specialization-help"
      onChange={(event) => onChange(roles.find((role) => libraryRefKey(role.ref) === event.currentTarget.value) ?? null)}>
      <option value="">{text("library_no_specialization")}</option>
      {value && !matching.some((role) => sameLibraryRef(role.ref, value)) ? <option value={libraryRefKey(value)}>{selectedName || value.id}{!selected ? ` — ${text("library_selection_unavailable")}` : ""}</option> : null}
      {groups.map((group) => <optgroup key={group} label={roleGroupName(group, text)}>
        {matching.filter((role) => role.group === group).map((role) => <option key={libraryRefKey(role.ref)} value={libraryRefKey(role.ref)}>{role.name}{role.ref.source === "custom" ? ` · ${text("library_custom")}` : ""}</option>)}
      </optgroup>)}
    </select>
    <p className="small-copy" id="role-specialization-help">{text("library_specialization_help")}</p>
    {selected ? <div className="specialization-summary"><p>{selected.description}</p><p className="small-copy">{text("library_primary_skill")}: <code>{selected.entry_skill.id}</code> · {text("library_version")}: <code>{selected.ref.version.slice(0, 12)}</code></p></div> : null}
    {library.kind !== "ready" || (value && !selected) ? <p className="field-error" role="status">{text(library.kind === "loading" ? "library_loading" : "library_selection_unavailable")} <button type="button" className="notice-link" onClick={onRefresh}>{text("check_again")}</button></p> : null}
  </div>;
}
