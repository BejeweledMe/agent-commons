import { type ReactElement, useEffect, useRef, useState, useSyncExternalStore } from "react";
import type { LibraryApi } from "../libraryApi.js";
import type { Locale, MessageKey } from "../i18n.js";
import type { LibraryItem, SkillOrganization } from "../libraryTypes.js";
import { libraryRefKey } from "../libraryTypes.js";
import { archivedGroups, groupMemberCount, groupedSkills, libraryDraftMemory, libraryMutation, moveTargets, sameText } from "../libraryAuthoringState.js";
import { LegacyTextChoice } from "./BlueprintDefinitionEditor.js";
import { LibraryMutationFeedback } from "./LibraryMutationFeedback.js";

// `name` is the one entered value; `legacy` holds the two stored texts of a group
// saved under the old two-field editor until the author picks the one to keep.
type GroupDraft = { id: string; name: string; legacy: { en: string; ru: string } | null; rename: boolean };
// The open archive panel of one group; `target` is the group its skills move to, chosen
// by the author. An empty group needs no target and only confirms.
type ArchiveDraft = { id: string; target: string };
export function SkillGroups({ skills, organization: supplied, search, api, locale, editable, projectId, text, onOpen, onRefresh }: {
  skills: readonly LibraryItem[]; organization?: SkillOrganization; search: string; api: LibraryApi; locale: Locale; editable: boolean; projectId?: string | null;
  text: (key: MessageKey) => string; onOpen: (skill: LibraryItem) => void; onRefresh: () => void;
}): ReactElement {
  const memoryKey = `skill-groups:${projectId ?? "legacy"}`;
  const archiveKey = `skill-groups-archive:${projectId ?? "legacy"}`;
  const mutation = libraryMutation(memoryKey);
  const status = useSyncExternalStore(mutation.subscribe, mutation.snapshot, mutation.snapshot);
  const [organization, setOrganization] = useState(supplied);
  const [draft, setDraft] = useState<GroupDraft | null>(() => libraryDraftMemory.get(memoryKey) as GroupDraft | null ?? null);
  const [archiving, setArchiving] = useState<ArchiveDraft | null>(() => libraryDraftMemory.get(archiveKey) as ArchiveDraft | null ?? null);
  const [invalid, setInvalid] = useState(false);
  const opener = useRef<HTMLButtonElement | null>(null);
  // Restore removes its own button, so focus returns to the archived section instead of
  // the opener; when the last archived group leaves, the section is gone and focus stays.
  const archivedSummary = useRef<HTMLElement | null>(null);
  // Persistent focus target for the restore of the last archived group: its Restore button
  // and the archived summary both unmount with the returned catalog.
  const root = useRef<HTMLDivElement | null>(null);
  const restoring = useRef(false);
  const locked = status.status === "saving" || status.status === "uncertain";
  const changeDraft = (value: GroupDraft | null): void => { setDraft(value); libraryDraftMemory.set(memoryKey, value); setInvalid(false); mutation.reset(); if (value === null) window.setTimeout(() => opener.current?.focus(), 0); };
  const changeArchive = (value: ArchiveDraft | null): void => { setArchiving(value); libraryDraftMemory.set(archiveKey, value); mutation.reset(); if (value === null) window.setTimeout(() => opener.current?.focus(), 0); };
  useEffect(() => { setOrganization(supplied); }, [supplied]);
  useEffect(() => {
    if (status.status === "saved" && status.result && "groups" in status.result) {
      // The archived group leaves the active list here, on the returned catalog, never earlier.
      setOrganization(status.result); setDraft(null); libraryDraftMemory.set(memoryKey, null); setArchiving(null); libraryDraftMemory.set(archiveKey, null);
      if (draft || archiving) window.setTimeout(() => opener.current?.focus(), 0);
      else if (restoring.current) window.setTimeout(() => (archivedSummary.current ?? root.current)?.focus(), 0);
      restoring.current = false; onRefresh();
    }
  }, [status]);
  function archiveGroup(id: string, archived: boolean, target?: string): void {
    if (!organization || locked || !editable) return;
    void mutation.start({ kind: "group_archive", id, input: { expected_revision: organization.revision, archived, ...(target ? { move_to_group_id: target } : {}) } }, api);
  }
  function archivePanel(id: string): ReactElement | null {
    if (!organization || !archiving) return null;
    const members = groupMemberCount(organization, id), targets = moveTargets(organization, id);
    return <div className="skill-group-archive">
      {members ? <p>{text("library_group_archive_members")} <span className="library-group-count">{members}</span>. {text("library_group_archive_move_help")}</p> : <p>{text("library_group_archive_confirm")}</p>}
      {!members ? null : targets.length
        ? <label>{text("library_group_archive_target")}<select autoFocus disabled={!editable || locked} value={archiving.target} onChange={(event) => changeArchive({ id, target: event.currentTarget.value })}><option value="">{text("library_group_archive_choose")}</option>{targets.map((target) => <option key={target.id} value={target.id}>{target.name[locale]}</option>)}</select></label>
        : <p className="field-error" role="alert">{text("library_group_archive_unavailable")}</p>}
      <div className="button-row">
        <button className="button button-primary" type="button" autoFocus={!members} disabled={!editable || locked || (members > 0 && !archiving.target)} onClick={() => archiveGroup(id, true, members ? archiving.target : undefined)}>{text(members ? "library_group_archive_move" : "library_archive_group")}</button>
        <button className="button button-secondary" type="button" onClick={() => changeArchive(null)}>{text("shell_close")}</button>
      </div>
    </div>;
  }
  function save(): void {
    if (!draft || !organization || locked || !editable) return;
    const valid = /^[a-z][a-z0-9-]{0,63}$/.test(draft.id) && !draft.legacy && draft.name.trim() && new TextEncoder().encode(draft.name).length <= 800 && (draft.rename || !organization.groups.some((group) => group.id === draft.id));
    setInvalid(!valid); if (!valid) return;
    // One entered value fills both persisted locale slots; it is never translated.
    void mutation.start({ kind: "group", input: { id: draft.id, name: sameText(draft.name.trim()), expected_revision: organization.revision } }, api);
  }
  const card = (skill: LibraryItem): ReactElement => <article key={libraryRefKey(skill.ref)} className="library-entry-card"><p className="small-copy">{text(skill.ref.source === "builtin" ? "library_builtin" : "library_custom")}</p><h3>{skill.name}</h3><p>{skill.description}</p><p className="small-copy"><code>{skill.ref.id}</code></p><button className="button button-secondary" type="button" onClick={() => onOpen(skill)}>{text("library_open_detail")}</button>{organization ? <label className="skill-move-label">{text("library_move_to_group")}<select aria-label={`${text("library_move_to_group")}: ${skill.name}`} disabled={!editable || locked} value={organization.assignments.find((item) => item.source === skill.ref.source && item.id === skill.ref.id)?.group_id ?? ""} onChange={(event) => { if (locked || !editable) return; void mutation.start({ kind: "move", input: { skill: { source: skill.ref.source, id: skill.ref.id }, group_id: event.currentTarget.value, expected_revision: organization.revision } }, api); }}>{moveTargets(organization).map((group) => <option key={group.id} value={group.id}>{group.name[locale]}</option>)}</select></label> : null}</article>;
  if (!organization) return <div className="library-catalog-grid">{skills.filter((skill) => `${skill.name} ${skill.description} ${skill.ref.id}`.toLocaleLowerCase(locale).includes(search.trim().toLocaleLowerCase(locale))).map(card)}</div>;
  const groups = groupedSkills(skills, organization, search, locale), hidden = archivedGroups(organization);
  return <div className="skill-groups" ref={root} tabIndex={-1}><div className="button-row"><button className="button button-secondary" type="button" disabled={!editable || locked || draft !== null || archiving !== null} onClick={(event) => { opener.current = event.currentTarget; changeDraft({ id: "", name: "", legacy: null, rename: false }); }}>{text("library_new_group")}</button><p className="small-copy">{text("library_groups_help")}</p></div>
    <LibraryMutationFeedback mutation={mutation} text={text} editable={editable} onRefresh={onRefresh} />
    {draft ? <form className="library-editor" onSubmit={(event) => { event.preventDefault(); save(); }}><h3>{text(draft.rename ? "library_rename_group" : "library_new_group")}</h3><fieldset disabled={!editable || locked}><label>{text("library_id")}<input autoFocus={!draft.rename} maxLength={64} disabled={draft.rename} value={draft.id} onChange={(event) => changeDraft({ ...draft, id: event.currentTarget.value })} /></label><p className="small-copy">{text("library_text_single_help")}</p><label>{text("library_name")}<input autoFocus={draft.rename} maxLength={200} disabled={draft.legacy !== null} value={draft.name} onChange={(event) => changeDraft({ ...draft, name: event.currentTarget.value })} /></label>{draft.legacy ? <LegacyTextChoice en={draft.legacy.en} ru={draft.legacy.ru} text={text} onChoose={(value) => changeDraft({ ...draft, name: value, legacy: null })} /> : null}{invalid ? <p className="field-error" role="alert">{text(draft.legacy ? "library_legacy_required" : "library_group_invalid")}</p> : null}<div className="button-row"><button className="button button-primary" type="submit">{text("library_save_group")}</button><button className="button button-secondary" type="button" onClick={() => changeDraft(null)}>{text("shell_close")}</button></div></fieldset></form> : null}
    {groups.map(({ group, skills: members }) => <details className="skill-group" key={`${group.id}:${Boolean(search.trim())}`} open={Boolean(search.trim()) || undefined}><summary><span>{group.name[locale]}</span><span className="library-tab-count">{members.length}</span></summary><div className="skill-group-body">{group.source === "custom" ? <><button className="notice-link" type="button" disabled={!editable || locked || draft !== null || archiving !== null} onClick={(event) => { opener.current = event.currentTarget; changeDraft(group.name.en === group.name.ru ? { id: group.id, name: group.name.en, legacy: null, rename: true } : { id: group.id, name: "", legacy: { en: group.name.en, ru: group.name.ru }, rename: true }); }}>{text("library_rename_group")}</button><button className="notice-link" type="button" disabled={!editable || locked || draft !== null || archiving !== null} onClick={(event) => { opener.current = event.currentTarget; changeArchive({ id: group.id, target: "" }); }}>{text("library_archive_group")}</button>{archiving && archiving.id === group.id ? archivePanel(group.id) : null}</> : null}{members.length ? <div className="library-catalog-grid">{members.map(card)}</div> : <p className="empty-guidance">{text("library_group_empty")}</p>}</div></details>)}
    {hidden.length ? <details className="skill-groups-archived"><summary ref={archivedSummary}><span>{text("library_archived_groups")}</span><span className="library-tab-count">{hidden.length}</span></summary><div className="skill-groups-archived-body"><p className="small-copy">{text("library_archived_groups_help")}</p>{hidden.map((group) => <div className="skill-groups-archived-row" key={group.id}><span>{group.name[locale]}</span><button className="button button-secondary" type="button" disabled={!editable || locked} onClick={() => { restoring.current = true; archiveGroup(group.id, false); }}>{text("library_restore_group")}</button></div>)}</div></details> : null}
    {!groups.length ? <p className="empty-guidance">{text("library_no_results")}</p> : null}
  </div>;
}
