import { type ReactElement, useEffect, useRef, useState, useSyncExternalStore } from "react";
import type { LibraryApi } from "../libraryApi.js";
import type { Locale, MessageKey } from "../i18n.js";
import type { LibraryItem, SkillOrganization } from "../libraryTypes.js";
import { libraryRefKey } from "../libraryTypes.js";
import { groupedSkills, libraryDraftMemory, libraryMutation } from "../libraryAuthoringState.js";
import { LibraryMutationFeedback } from "./LibraryMutationFeedback.js";

type GroupDraft = { id: string; en: string; ru: string; rename: boolean };
export function SkillGroups({ skills, organization: supplied, search, api, locale, editable, projectId, text, onOpen, onRefresh }: {
  skills: readonly LibraryItem[]; organization?: SkillOrganization; search: string; api: LibraryApi; locale: Locale; editable: boolean; projectId?: string | null;
  text: (key: MessageKey) => string; onOpen: (skill: LibraryItem) => void; onRefresh: () => void;
}): ReactElement {
  const memoryKey = `skill-groups:${projectId ?? "legacy"}`;
  const mutation = libraryMutation(memoryKey);
  const status = useSyncExternalStore(mutation.subscribe, mutation.snapshot, mutation.snapshot);
  const [organization, setOrganization] = useState(supplied);
  const [draft, setDraft] = useState<GroupDraft | null>(() => libraryDraftMemory.get(memoryKey) as GroupDraft | null ?? null);
  const [invalid, setInvalid] = useState(false);
  const opener = useRef<HTMLButtonElement | null>(null);
  const locked = status.status === "saving" || status.status === "uncertain";
  const changeDraft = (value: GroupDraft | null): void => { setDraft(value); libraryDraftMemory.set(memoryKey, value); setInvalid(false); mutation.reset(); if (value === null) window.setTimeout(() => opener.current?.focus(), 0); };
  useEffect(() => { setOrganization(supplied); }, [supplied]);
  useEffect(() => {
    if (status.status === "saved" && status.result && "groups" in status.result) {
      setOrganization(status.result); setDraft(null); libraryDraftMemory.set(memoryKey, null); if (draft) window.setTimeout(() => opener.current?.focus(), 0); onRefresh();
    }
  }, [status]);
  function save(): void {
    if (!draft || !organization || locked || !editable) return;
    const valid = /^[a-z][a-z0-9-]{0,63}$/.test(draft.id) && draft.en.trim() && draft.ru.trim() && [draft.en, draft.ru].every((value) => new TextEncoder().encode(value).length <= 800) && (draft.rename || !organization.groups.some((group) => group.id === draft.id));
    setInvalid(!valid); if (!valid) return;
    void mutation.start({ kind: "group", input: { id: draft.id, name: { en: draft.en.trim(), ru: draft.ru.trim() }, expected_revision: organization.revision } }, api);
  }
  const card = (skill: LibraryItem): ReactElement => <article key={libraryRefKey(skill.ref)} className="library-entry-card"><p className="small-copy">{text(skill.ref.source === "builtin" ? "library_builtin" : "library_custom")}</p><h3>{skill.name}</h3><p>{skill.description}</p><p className="small-copy"><code>{skill.ref.id}</code></p><button className="button button-secondary" type="button" onClick={() => onOpen(skill)}>{text("library_open_detail")}</button>{organization ? <label className="skill-move-label">{text("library_move_to_group")}<select aria-label={`${text("library_move_to_group")}: ${skill.name}`} disabled={!editable || locked} value={organization.assignments.find((item) => item.source === skill.ref.source && item.id === skill.ref.id)?.group_id ?? ""} onChange={(event) => { if (locked || !editable) return; void mutation.start({ kind: "move", input: { skill: { source: skill.ref.source, id: skill.ref.id }, group_id: event.currentTarget.value, expected_revision: organization.revision } }, api); }}>{organization.groups.map((group) => <option key={group.id} value={group.id}>{group.name[locale]}</option>)}</select></label> : null}</article>;
  if (!organization) return <div className="library-catalog-grid">{skills.filter((skill) => `${skill.name} ${skill.description} ${skill.ref.id}`.toLocaleLowerCase(locale).includes(search.trim().toLocaleLowerCase(locale))).map(card)}</div>;
  const groups = groupedSkills(skills, organization, search, locale);
  return <div className="skill-groups"><div className="button-row"><button className="button button-secondary" type="button" disabled={!editable || locked || draft !== null} onClick={(event) => { opener.current = event.currentTarget; changeDraft({ id: "", en: "", ru: "", rename: false }); }}>{text("library_new_group")}</button><p className="small-copy">{text("library_groups_help")}</p></div>
    <LibraryMutationFeedback mutation={mutation} text={text} editable={editable} onRefresh={onRefresh} />
    {draft ? <form className="library-editor" onSubmit={(event) => { event.preventDefault(); save(); }}><h3>{text(draft.rename ? "library_rename_group" : "library_new_group")}</h3><fieldset disabled={!editable || locked}><label>{text("library_id")}<input autoFocus={!draft.rename} maxLength={64} disabled={draft.rename} value={draft.id} onChange={(event) => changeDraft({ ...draft, id: event.currentTarget.value })} /></label><label>{text("library_name_en")}<input autoFocus={draft.rename} maxLength={200} value={draft.en} onChange={(event) => changeDraft({ ...draft, en: event.currentTarget.value })} /></label><label>{text("library_name_ru")}<input maxLength={200} value={draft.ru} onChange={(event) => changeDraft({ ...draft, ru: event.currentTarget.value })} /></label>{invalid ? <p className="field-error" role="alert">{text("library_group_invalid")}</p> : null}<div className="button-row"><button className="button button-primary" type="submit">{text("library_save_group")}</button><button className="button button-secondary" type="button" onClick={() => changeDraft(null)}>{text("shell_close")}</button></div></fieldset></form> : null}
    {groups.map(({ group, skills: members }) => <details className="skill-group" key={`${group.id}:${Boolean(search.trim())}`} open={Boolean(search.trim()) || undefined}><summary><span>{group.name[locale]}</span><span className="library-tab-count">{members.length}</span></summary><div className="skill-group-body">{group.source === "custom" ? <button className="notice-link" type="button" disabled={!editable || locked || draft !== null} onClick={(event) => { opener.current = event.currentTarget; changeDraft({ id: group.id, en: group.name.en, ru: group.name.ru, rename: true }); }}>{text("library_rename_group")}</button> : null}{members.length ? <div className="library-catalog-grid">{members.map(card)}</div> : <p className="empty-guidance">{text("library_group_empty")}</p>}</div></details>)}
    {!groups.length ? <p className="empty-guidance">{text("library_no_results")}</p> : null}
  </div>;
}
