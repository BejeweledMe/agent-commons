import { type ReactElement, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import type { LibraryApi } from "../libraryApi.js";
import { LibraryDetailSession, type LibraryDetailView } from "../libraryDetailSession.js";
import { Modal } from "./Modal.js";
import { SkillGroups } from "./SkillGroups.js";
import type { Locale, MessageKey } from "../i18n.js";
import { editLibraryDraft, newLibraryDraft, type LibraryDraft } from "../libraryEditorState.js";
import { libraryRefKey, type LibraryItem, type LibraryRole, type LibraryState } from "../libraryTypes.js";
import { roleGroupName } from "./SpecializationPicker.js";
import { ServiceLibraryEditor } from "./ServiceLibraryEditor.js";

export function ServiceLibrarySection({ api, state, onRefresh, onChooseRole, text, writesEnabled, kind, locale = "en", projectId }: {
  api: LibraryApi; state: LibraryState; onRefresh: () => void; onChooseRole: (role: LibraryRole) => void;
  text: (key: MessageKey) => string; writesEnabled: boolean; kind?: "role" | "skill"; locale?: Locale; projectId?: string | null;
}): ReactElement {
  const [localTab, setTab] = useState<"role" | "skill">("role");
  const tab = kind ?? localTab;
  const [search, setSearch] = useState("");
  const [group, setGroup] = useState("");
  const reader = useMemo(() => new LibraryDetailSession(api), [api]);
  const view = useSyncExternalStore(reader.subscribe, reader.snapshot, reader.snapshot);
  const detail = view.detail;
  useEffect(() => () => reader.close(), [reader]);
  const [editor, setEditor] = useState<{ draft: LibraryDraft; key: number } | null>(null);
  const [notice, setNotice] = useState(false);
  const editorVersion = useRef(0);
  const container = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!editor || container.current?.closest("[hidden]")) return;
    const element = container.current?.querySelector<HTMLElement>(".library-editor");
    element?.scrollIntoView({ block: "start" });
    element?.querySelector<HTMLInputElement>("input:not(:disabled)")?.focus({ preventScroll: true });
  }, [editor?.key]);
  const catalog = state.kind === "loading" ? null : state.catalog;
  const editable = Boolean(writesEnabled && catalog?.editingEnabled);
  const rows = tab === "role" ? catalog?.roles ?? [] : catalog?.skills ?? [];
  const filtered = rows.filter((row) => (tab !== "role" || !group || (row as LibraryRole).group === group)
    && `${row.name} ${row.description} ${row.ref.id}`.toLowerCase().includes(search.toLowerCase()));
  return <section ref={container} className="service-library" aria-label={text(tab === "role" ? "library_roles" : "library_skills")}>
    <div className="composer-heading"><div><h2>{text(tab === "role" ? "library_roles" : "library_skills")}</h2><p className="lead-copy">{text(tab === "role" ? "library_catalog_intro" : "library_skills_intro")}</p></div><button type="button" className="button button-secondary" onClick={onRefresh}>{text("library_refresh")}</button></div>
    {!kind ? <div className="library-kind-buttons"><button type="button" className="button button-secondary" aria-pressed={tab === "role"} onClick={() => setTab("role")}>{text("library_roles")} {catalog ? `(${catalog.roles.length})` : ""}</button><button type="button" className="button button-secondary" aria-pressed={tab === "skill"} onClick={() => setTab("skill")}>{text("library_skills")} {catalog ? `(${catalog.skills.length})` : ""}</button></div> : null}
    <div className="library-filter-row"><label>{text("library_search")}<input type="search" value={search} onChange={(event) => setSearch(event.currentTarget.value)} /></label>{tab === "role" ? <label>{text("library_group")}<select value={group} onChange={(event) => setGroup(event.currentTarget.value)}><option value="">{text("library_all_groups")}</option>{[...new Set(catalog?.roles.map((role) => role.group) ?? [])].map((value) => <option key={value} value={value}>{roleGroupName(value, text)}</option>)}</select></label> : null}
      <button type="button" className="button button-primary" disabled={!editable || editor !== null} onClick={() => { setEditor({ draft: newLibraryDraft(tab), key: ++editorVersion.current }); setNotice(false); }}>{text(tab === "role" ? "library_new_role" : "library_new_skill")}</button></div>
    {state.kind === "loading" ? <p role="status">{text("library_loading")}</p> : state.kind === "error" ? <p className="field-error" role="alert">{text("library_unavailable")}</p> : null}
    {catalog && !editable ? <p className="small-copy">{text("library_readonly")}</p> : null}
    {notice ? <p className="notice" role="status">{text(state.kind === "error" ? "library_saved_refresh_failed" : "library_saved")}</p> : null}
    {editor && catalog ? <ServiceLibraryEditor key={editor.key} idPrefix={`library-${tab}-edit`} initialDraft={editor.draft} catalog={catalog} api={api} text={text} writesEnabled={writesEnabled} onClose={() => setEditor(null)} onSaved={() => { setEditor(null); setNotice(true); onRefresh(); }} /> : null}
    {tab === "skill" && catalog ? <SkillGroups skills={catalog.skills} organization={catalog.skillOrganization} search={search} api={api} locale={locale} editable={editable} projectId={projectId} text={text} onOpen={(skill) => void reader.open(skill)} onRefresh={onRefresh} /> : <><div className="library-catalog-grid">{filtered.map((item) => <article key={libraryRefKey(item.ref)} className="library-entry-card"><p className="small-copy">{text(item.ref.source === "builtin" ? "library_builtin" : "library_custom")}{item.ref.kind === "role" ? ` · ${roleGroupName((item as LibraryRole).group, text)}` : ""}</p><h3>{item.name}</h3><p>{item.description}</p><p className="small-copy"><code>{item.ref.id}</code></p><div className="button-row"><button type="button" className="button button-secondary" onClick={() => void reader.open(item)}>{text("library_open_detail")}</button>{item.ref.kind === "role" ? <button type="button" className="button button-primary" onClick={() => onChooseRole(item as LibraryRole)}>{text("library_choose_team")}</button> : null}</div></article>)}</div></>}

    {catalog && tab !== "skill" && !filtered.length ? <p className="empty-guidance">{text("library_no_results")}</p> : null}
    {view.item ? <Modal title={view.item.name} size="medium" closeLabel={text("shell_close")} onClose={reader.close} className="library-detail-modal">
      <LibraryDetailContent view={view} text={text} onRetry={() => void reader.retry()} onReadFile={(file) => void reader.readFile(file)}
        editable={editable && editor === null} onEdit={() => { if (!detail) return; const draft = editLibraryDraft(detail); reader.close(); setEditor({ draft, key: ++editorVersion.current }); }} />
    </Modal> : null}
  </section>;
}

export function LibraryDetailContent({ view, text, onRetry, onReadFile, editable, onEdit }: {
  view: LibraryDetailView; text: (key: MessageKey) => string; onRetry: () => void;
  onReadFile: (file: import("../libraryTypes.js").LibraryFile) => void; editable: boolean; onEdit: () => void;
}): ReactElement {
  const detail = view.detail;
  const fileView = view.file;
  if (view.status === "loading") return <p role="status">{text("library_loading")}</p>;
  if (view.status === "error") return <div role="alert"><p className="field-error">{text("library_unavailable")}</p><button type="button" className="button button-secondary" onClick={onRetry}>{text("library_detail_retry")}</button></div>;
  if (!detail) return <></>;
  return <section className="library-detail" aria-label={detail.content.name}>
    <p>{detail.content.description}</p><button type="button" className="button button-secondary" disabled={!editable} onClick={onEdit}>{text(detail.ref.source === "builtin" ? "library_fork" : "library_edit_custom")}</button>
    <p className="small-copy">{text("library_version")}: <code>{detail.ref.version}</code></p>
    {"system_prompt" in detail.content ? <><p>{text("library_primary_skill")}: <code>{detail.content.entry_skill.id}</code></p><p className="small-copy">{text("library_route_help")}</p><ul>{detail.content.conditional_skills.map((route) => <li key={libraryRefKey(route.ref)}><code>{route.ref.id}</code> — {route.when}</li>)}</ul></> : null}
    <details open><summary>{text(detail.ref.kind === "role" ? "library_role_instructions" : "library_skill_instructions")}</summary><pre className="library-source-text">{"system_prompt" in detail.content ? detail.content.system_prompt : detail.content.instruction}</pre></details>
    {detail.files.length ? <details><summary>{text("library_files")} ({detail.files.length})</summary><ul className="library-files">{detail.files.map((file) => <li key={file.path}><code>{file.path}</code><button type="button" className="notice-link" onClick={() => onReadFile(file)}>{text("library_read_file")}</button></li>)}</ul></details> : null}
    {fileView ? <div><h4>{fileView.path}</h4>{fileView.error ? <p role="alert" className="field-error">{text("library_file_unavailable")}</p> : fileView.text === null ? <p role="status">{text("library_loading")}</p> : <pre className="library-source-text">{fileView.text}</pre>}</div> : null}
  </section>;
}
