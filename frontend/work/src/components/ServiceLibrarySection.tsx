import { type ReactElement, useEffect, useRef, useState } from "react";
import type { LibraryApi } from "../libraryApi.js";
import { SkillGroups } from "./SkillGroups.js";
import type { Locale, MessageKey } from "../i18n.js";
import { editLibraryDraft, newLibraryDraft, type LibraryDraft } from "../libraryEditorState.js";
import { libraryRefKey, type LibraryDetail, type LibraryFile, type LibraryItem, type LibraryRole, type LibraryState } from "../libraryTypes.js";
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
  const [detail, setDetail] = useState<LibraryDetail | null>(null);
  const [detailState, setDetailState] = useState<"idle" | "loading" | "error">("idle");
  const [editor, setEditor] = useState<{ draft: LibraryDraft; key: number } | null>(null);
  const [notice, setNotice] = useState(false);
  const [fileView, setFileView] = useState<{ path: string; text: string | null; error: boolean } | null>(null);
  const readVersion = useRef(0);
  const fileVersion = useRef(0);
  const editorVersion = useRef(0);
  const container = useRef<HTMLElement>(null);
  const detailElement = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!editor || container.current?.closest("[hidden]")) return;
    const element = container.current?.querySelector<HTMLElement>(".library-editor");
    element?.scrollIntoView({ block: "start" });
    element?.querySelector<HTMLInputElement>("input:not(:disabled)")?.focus({ preventScroll: true });
  }, [editor?.key]);
  useEffect(() => {
    if (!detail || editor || container.current?.closest("[hidden]")) return;
    detailElement.current?.scrollIntoView({ block: "start" });
    detailElement.current?.focus({ preventScroll: true });
  }, [detail]);
  const catalog = state.kind === "loading" ? null : state.catalog;
  const editable = Boolean(writesEnabled && catalog?.editingEnabled);
  const rows = tab === "role" ? catalog?.roles ?? [] : catalog?.skills ?? [];
  const filtered = rows.filter((row) => (tab !== "role" || !group || (row as LibraryRole).group === group)
    && `${row.name} ${row.description} ${row.ref.id}`.toLowerCase().includes(search.toLowerCase()));
  async function open(item: LibraryItem): Promise<void> {
    const version = ++readVersion.current; ++fileVersion.current;
    setDetailState("loading"); setDetail(null); setFileView(null); setNotice(false);
    try { const next = await api.detail(item.ref, new AbortController().signal); if (readVersion.current === version) { setDetail(next); setDetailState("idle"); } }
    catch { if (readVersion.current === version) setDetailState("error"); }
  }
  async function readFile(file: LibraryFile): Promise<void> {
    if (!detail) return;
    const version = ++fileVersion.current;
    setFileView({ path: file.path, text: null, error: false });
    try { const contents = await api.readFile(detail.ref, file, new AbortController().signal); if (fileVersion.current === version) setFileView({ path: file.path, text: contents, error: false }); }
    catch { if (fileVersion.current === version) setFileView({ path: file.path, text: null, error: true }); }
  }
  return <section ref={container} className="service-library" aria-label={text(tab === "role" ? "library_roles" : "library_skills")}>
    <div className="composer-heading"><div><h2>{text(tab === "role" ? "library_roles" : "library_skills")}</h2><p className="lead-copy">{text(tab === "role" ? "library_catalog_intro" : "library_skills_intro")}</p></div><button type="button" className="button button-secondary" onClick={onRefresh}>{text("library_refresh")}</button></div>
    {!kind ? <div className="library-kind-buttons"><button type="button" className="button button-secondary" aria-pressed={tab === "role"} onClick={() => setTab("role")}>{text("library_roles")} {catalog ? `(${catalog.roles.length})` : ""}</button><button type="button" className="button button-secondary" aria-pressed={tab === "skill"} onClick={() => setTab("skill")}>{text("library_skills")} {catalog ? `(${catalog.skills.length})` : ""}</button></div> : null}
    <div className="library-filter-row"><label>{text("library_search")}<input type="search" value={search} onChange={(event) => setSearch(event.currentTarget.value)} /></label>{tab === "role" ? <label>{text("library_group")}<select value={group} onChange={(event) => setGroup(event.currentTarget.value)}><option value="">{text("library_all_groups")}</option>{[...new Set(catalog?.roles.map((role) => role.group) ?? [])].map((value) => <option key={value} value={value}>{roleGroupName(value, text)}</option>)}</select></label> : null}
      <button type="button" className="button button-primary" disabled={!editable || editor !== null} onClick={() => { setEditor({ draft: newLibraryDraft(tab), key: ++editorVersion.current }); setNotice(false); }}>{text(tab === "role" ? "library_new_role" : "library_new_skill")}</button></div>
    {state.kind === "loading" ? <p role="status">{text("library_loading")}</p> : state.kind === "error" ? <p className="field-error" role="alert">{text("library_unavailable")}</p> : null}
    {catalog && !editable ? <p className="small-copy">{text("library_readonly")}</p> : null}
    {notice ? <p className="notice" role="status">{text("library_saved")}</p> : null}
    {editor && catalog ? <ServiceLibraryEditor key={editor.key} idPrefix={`library-${tab}-edit`} initialDraft={editor.draft} catalog={catalog} api={api} text={text} writesEnabled={writesEnabled} onClose={() => setEditor(null)} onSaved={(saved) => { setDetail(saved); setEditor(null); setNotice(true); onRefresh(); }} /> : null}
    {tab === "skill" && catalog ? <SkillGroups skills={catalog.skills} organization={catalog.skillOrganization} search={search} api={api} locale={locale} editable={editable} projectId={projectId} text={text} onOpen={(skill) => void open(skill)} onRefresh={onRefresh} /> : <><div className="library-catalog-grid">{filtered.map((item) => <article key={libraryRefKey(item.ref)} className="library-entry-card"><p className="small-copy">{text(item.ref.source === "builtin" ? "library_builtin" : "library_custom")}{item.ref.kind === "role" ? ` · ${roleGroupName((item as LibraryRole).group, text)}` : ""}</p><h3>{item.name}</h3><p>{item.description}</p><p className="small-copy"><code>{item.ref.id}</code></p><div className="button-row"><button type="button" className="button button-secondary" onClick={() => void open(item)}>{text("library_open_detail")}</button>{item.ref.kind === "role" ? <button type="button" className="button button-primary" onClick={() => onChooseRole(item as LibraryRole)}>{text("library_choose_team")}</button> : null}</div></article>)}</div></>}

    {catalog && tab !== "skill" && !filtered.length ? <p className="empty-guidance">{text("library_no_results")}</p> : null}
    {detailState === "loading" ? <p role="status">{text("library_loading")}</p> : detailState === "error" ? <p className="field-error" role="alert">{text("library_unavailable")}</p> : null}
    {detail ? <section ref={detailElement} tabIndex={-1} className="library-detail" aria-label={detail.content.name}><div className="composer-heading"><h3>{detail.content.name}</h3><button type="button" className="button button-secondary" disabled={!editable || editor !== null} onClick={() => setEditor({ draft: editLibraryDraft(detail), key: ++editorVersion.current })}>{text(detail.ref.source === "builtin" ? "library_fork" : "library_edit_custom")}</button></div><p>{detail.content.description}</p><p className="small-copy">{text("library_version")}: <code>{detail.ref.version}</code></p>
      {"system_prompt" in detail.content ? <><p>{text("library_primary_skill")}: <code>{detail.content.entry_skill.id}</code></p><p className="small-copy">{text("library_route_help")}</p><ul>{detail.content.conditional_skills.map((route) => <li key={libraryRefKey(route.ref)}><code>{route.ref.id}</code> — {route.when}</li>)}</ul></> : null}
      <details><summary>{text(detail.ref.kind === "role" ? "library_role_instructions" : "library_skill_instructions")}</summary><pre className="library-source-text">{"system_prompt" in detail.content ? detail.content.system_prompt : detail.content.instruction}</pre></details>
      {detail.files.length ? <details><summary>{text("library_files")} ({detail.files.length})</summary><ul className="library-files">{detail.files.map((file) => <li key={file.path}><code>{file.path}</code><button type="button" className="notice-link" onClick={() => void readFile(file)}>{text("library_read_file")}</button></li>)}</ul></details> : null}
      {fileView ? <div><h4>{fileView.path}</h4>{fileView.error ? <p role="alert" className="field-error">{text("library_file_unavailable")}</p> : fileView.text === null ? <p role="status">{text("library_loading")}</p> : <pre className="library-source-text">{fileView.text}</pre>}</div> : null}
    </section> : null}
  </section>;
}
