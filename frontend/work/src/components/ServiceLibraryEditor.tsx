import { type FormEvent, type ReactElement, useRef, useState } from "react";
import { ApiProblem } from "../api.js";
import type { LibraryApi } from "../libraryApi.js";
import type { MessageKey } from "../i18n.js";
import { freezeLibrarySave, libraryDraftErrors, librarySaveInput, type LibraryDraft, type LibrarySaveIntent } from "../libraryEditorState.js";
import { libraryRefKey, type LibraryDetail, type ServiceLibrary } from "../libraryTypes.js";

export function ServiceLibraryEditor({ initialDraft, catalog, api, onClose, onSaved, text, writesEnabled, idPrefix = "library-edit" }: {
  initialDraft: LibraryDraft; catalog: ServiceLibrary; api: LibraryApi; onClose: () => void;
  onSaved: (detail: LibraryDetail) => void; text: (key: MessageKey) => string; writesEnabled: boolean; idPrefix?: string;
}): ReactElement {
  const [draft, setDraft] = useState(initialDraft);
  const [errors, setErrors] = useState<string[]>([]);
  const [intent, setIntent] = useState<LibrarySaveIntent | null>(null);
  const [status, setStatus] = useState<"idle" | "saving" | "refused" | "uncertain">("idle");
  const [code, setCode] = useState("");
  const executing = useRef(false);
  const locked = status === "saving" || status === "uncertain";
  const update = <K extends keyof LibraryDraft>(key: K, value: LibraryDraft[K]): void => { setDraft((current) => ({ ...current, [key]: value })); setStatus("idle"); setIntent(null); };
  async function execute(operation: LibrarySaveIntent): Promise<void> {
    if (executing.current) return;
    executing.current = true;
    setStatus("saving"); setCode("");
    try { const detail = await api.save(operation.input, operation.key, new AbortController().signal); setStatus("idle"); setIntent(null); onSaved(detail); }
    catch (error: unknown) {
      setIntent(operation);
      const problem = error instanceof ApiProblem ? error : null;
      setStatus(problem && problem.status >= 400 && problem.status < 500 ? "refused" : "uncertain");
      setCode(problem?.apiError?.code ?? "request_unavailable");
    }
    finally { executing.current = false; }
  }
  function submit(event: FormEvent): void {
    event.preventDefault();
    if (locked || executing.current || !writesEnabled || !catalog.editingEnabled) return;
    const invalid = libraryDraftErrors(draft, catalog); setErrors(invalid);
    if (invalid.length) return;
    const operation = freezeLibrarySave(librarySaveInput(draft, catalog), crypto.randomUUID());
    setIntent(operation); void execute(operation);
  }
  const errorFor = (key: string): ReactElement | null => errors.includes(key) ? <p className="field-error" id={`${idPrefix}-${key}-error`}>{text("library_invalid_field")}</p> : null;
  const inputProps = (key: string) => ({ "aria-invalid": errors.includes(key), "aria-describedby": errors.includes(key) ? `${idPrefix}-${key}-error` : undefined });
  const retained = draft.retainedSkills.filter((ref, index, all) => !catalog.skills.some((skill) => libraryRefKey(skill.ref) === libraryRefKey(ref)) && all.findIndex((item) => libraryRefKey(item) === libraryRefKey(ref)) === index);
  const skillOptions = [...catalog.skills, ...retained.map((ref) => ({ ref, name: ref.id }))].map((skill) => <option key={libraryRefKey(skill.ref)} value={libraryRefKey(skill.ref)}>{skill.name} · {skill.ref.source} · {skill.ref.version.slice(0, 8)}</option>);
  return <section className="library-editor" aria-label={text("library_editor_title")}>
    <div className="composer-heading"><h3>{text(draft.expectedVersion ? "library_edit_custom" : draft.forkRef ? "library_fork" : "library_create")}</h3><button className="button button-secondary button-inline" type="button" disabled={locked} onClick={onClose}>{text("shell_close")}</button></div>
    {draft.forkRef ? <p className="small-copy">{text("library_fork_help")} <code>{draft.forkRef.id}</code></p> : null}
    <form onSubmit={submit} noValidate>
      <fieldset disabled={!writesEnabled || !catalog.editingEnabled || locked}>
        <label htmlFor={`${idPrefix}-id`}>{text("library_id")}</label>
        <input id={`${idPrefix}-id`} {...inputProps("id")} value={draft.id} disabled={draft.expectedVersion !== null} maxLength={64} onChange={(event) => update("id", event.currentTarget.value)} />{errorFor("id")}
        <p className="small-copy">{text("library_id_help")}</p>
        <label htmlFor={`${idPrefix}-name`}>{text("library_name")}</label>
        <input id={`${idPrefix}-name`} {...inputProps("name")} value={draft.name} maxLength={200} onChange={(event) => update("name", event.currentTarget.value)} />{errorFor("name")}
        <label htmlFor={`${idPrefix}-description`}>{text("library_description")}</label>
        <textarea id={`${idPrefix}-description`} {...inputProps("description")} value={draft.description} maxLength={4000} rows={3} onChange={(event) => update("description", event.currentTarget.value)} />{errorFor("description")}
        {draft.kind === "role" ? <>
          <label htmlFor={`${idPrefix}-group`}>{text("library_group")}</label><input id={`${idPrefix}-group`} {...inputProps("group")} maxLength={80} value={draft.group} onChange={(event) => update("group", event.currentTarget.value)} />{errorFor("group")}
          <label htmlFor={`${idPrefix}-primary`}>{text("library_primary_skill")}</label>
          <select id={`${idPrefix}-primary`} {...inputProps("entrySkill")} value={draft.entrySkill} onChange={(event) => update("entrySkill", event.currentTarget.value)}><option value="">{text("library_choose_skill")}</option>{skillOptions}</select>{errorFor("entrySkill")}
          <label htmlFor={`${idPrefix}-core`}>{text("library_core_skills")}</label><select id={`${idPrefix}-core`} {...inputProps("coreSkills")} multiple size={5} value={draft.coreSkills} onChange={(event) => update("coreSkills", [...event.currentTarget.selectedOptions].map((option) => option.value))}>{skillOptions}</select>{errorFor("coreSkills")}
          <p className="small-copy">{text("library_core_help")}</p>
          <h4>{text("library_conditional_skills")}</h4>{draft.conditionalSkills.map((route, index) => <div className="library-route-editor" key={index}>
            <label>{text("library_skill")}<select value={route.key} onChange={(event) => update("conditionalSkills", draft.conditionalSkills.map((row, i) => i === index ? { ...row, key: event.currentTarget.value } : row))}><option value="">{text("library_choose_skill")}</option>{skillOptions}</select></label>
            <label>{text("library_trigger")}<textarea rows={2} maxLength={4000} value={route.when} onChange={(event) => update("conditionalSkills", draft.conditionalSkills.map((row, i) => i === index ? { ...row, when: event.currentTarget.value } : row))} /></label>
            <button type="button" className="button button-secondary button-inline" onClick={() => update("conditionalSkills", draft.conditionalSkills.filter((_, i) => i !== index))}>{text("library_remove_route")}</button>
          </div>)}{errorFor("conditionalSkills")}
          <button type="button" className="button button-secondary" disabled={draft.conditionalSkills.length >= 32} onClick={() => update("conditionalSkills", [...draft.conditionalSkills, { key: "", when: "" }])}>{text("library_add_route")}</button>
        </> : null}
        <label htmlFor={`${idPrefix}-instruction`}>{text(draft.kind === "role" ? "library_role_instructions" : "library_skill_instructions")}</label>
        <textarea id={`${idPrefix}-instruction`} {...inputProps("instruction")} className="library-instruction-input" value={draft.instruction} rows={14} onChange={(event) => update("instruction", event.currentTarget.value)} />{errorFor("instruction")}
        <p className="small-copy">{text("library_instruction_help")}</p>
        <button className="button button-primary" type="submit">{text(status === "saving" ? "working" : "library_save_version")}</button>
      </fieldset>
    </form>
    {status === "uncertain" || status === "refused" ? <div className="library-save-error" role="alert"><p>{text(status === "uncertain" ? "library_save_uncertain" : "library_save_refused")}</p><p className="small-copy"><code>{code}</code></p>{intent ? <button className="button button-secondary" type="button" onClick={() => void execute(intent)} disabled={!writesEnabled || !catalog.editingEnabled}>{text("library_retry_same")}</button> : null}</div> : null}
  </section>;
}
