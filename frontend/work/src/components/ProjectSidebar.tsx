import { type FormEvent, type ReactElement, useEffect, useRef, useState } from "react";

import { ApiProblem } from "../api.js";
import type { Locale, MessageKey } from "../i18n.js";
import type { ProjectInspection, ProjectList, ProjectRecord } from "../projectWorkspace.js";

type Props = {
  currentProjectId: string | null;
  legacy: boolean;
  locale: Locale;
  projects: ProjectList | null;
  text: (key: MessageKey) => string;
  onGallery: () => void;
  onInspect: (input: { mode: "new" | "existing"; path: string; name: string }) => Promise<ProjectInspection>;
  onCreate: (inspection: ProjectInspection) => Promise<void>;
  onSelect: (projectId: string) => void;
  onUpdate: (projectId: string, expectedRevision: string, input: { name: string; archived: boolean }) => Promise<void>;
};

export function ProjectSidebar({ currentProjectId, legacy, locale, projects, text, onGallery, onInspect, onCreate, onSelect, onUpdate }: Props): ReactElement {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<"new" | "existing">("new");
  const [name, setName] = useState("");
  const [path, setPath] = useState("");
  const [inspection, setInspection] = useState<ProjectInspection | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<"request" | "initialization_ambiguous" | null>(null);
  const [renameId, setRenameId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [search, setSearch] = useState("");
  const dialog = useRef<HTMLDialogElement>(null);
  const newButton = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (open && dialog.current && !dialog.current.open) dialog.current.showModal();
    if (!open && dialog.current?.open) dialog.current.close();
  }, [open]);
  function close(): void {
    setOpen(false); setInspection(null); setError(null);
    window.setTimeout(() => newButton.current?.focus(), 0);
  }
  async function inspect(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!name.trim() || !path.trim()) { setError("request"); return; }
    setBusy(true); setError(null);
    try { setInspection(await onInspect({ mode, name: name.trim(), path: path.trim() })); }
    catch { setError("request"); }
    finally { setBusy(false); }
  }
  async function create(): Promise<void> {
    if (inspection === null) return;
    setBusy(true); setError(null);
    try { await onCreate(inspection); close(); setName(""); setPath(""); }
    catch (failure: unknown) {
      if (failure instanceof ApiProblem && failure.apiError?.code === "project_initialization_ambiguous") {
        setInspection(null); setMode("existing"); setError("initialization_ambiguous");
      } else setError("request");
    }
    finally { setBusy(false); }
  }
  async function update(projectId: string, expectedRevision: string, input: { name: string; archived: boolean }): Promise<boolean> {
    setBusy(true); setError(null);
    try { await onUpdate(projectId, expectedRevision, input); return true; }
    catch { setError("request"); return false; }
    finally { setBusy(false); }
  }
  const matching = (projects?.projects ?? []).filter((project) => project.name.toLocaleLowerCase(locale).includes(search.trim().toLocaleLowerCase(locale)));
  const visible = matching.filter((project) => !project.archived);
  const archived = matching.filter((project) => project.archived);
  const row = (project: ProjectRecord): ReactElement => <li key={project.id}><button aria-current={project.id === currentProjectId ? "page" : undefined} className="project-list-item" disabled={!project.available || project.state !== "ready"} onClick={() => onSelect(project.id)} type="button"><strong>{project.name}</strong><span>{project.archived ? text("project_archived") : project.state === "ready" ? text("project_ready") : text(`project_${project.state}` as MessageKey)}</span></button>
    {project.id === currentProjectId ? <details className="project-actions"><summary>{text("project_actions")}</summary>{renameId === project.id ? <form onSubmit={(event) => { event.preventDefault(); if (renameValue.trim() && renameValue.trim() !== project.name) void update(project.id, project.revision, { name: renameValue.trim(), archived: project.archived }).then((saved) => { if (saved) setRenameId(null); }); }}><label className="sr-only" htmlFor="project-rename">{text("project_rename")}</label><input autoFocus disabled={projects?.readOnly || busy} id="project-rename" maxLength={160} onChange={(event) => setRenameValue(event.currentTarget.value)} value={renameValue} /><div className="button-row"><button className="button button-primary button-inline" disabled={projects?.readOnly || busy} type="submit">{text("project_rename")}</button><button className="button button-secondary button-inline" onClick={() => setRenameId(null)} type="button">{text("project_back")}</button></div></form> : <button className="notice-link" disabled={projects?.readOnly || busy} onClick={() => { setRenameId(project.id); setRenameValue(project.name); }} type="button">{text("project_rename")}</button>}<button className="notice-link" disabled={projects?.readOnly || busy} onClick={() => void update(project.id, project.revision, { name: project.name, archived: !project.archived })} type="button">{text(project.archived ? "project_restore" : "project_archive")}</button></details> : null}
  </li>;
  return <aside className="project-sidebar" aria-label={text("project_sidebar_label")}>
    <p className="app-brand">Agent Commons</p>
    <div className="project-sidebar-heading"><h2>{text("project_sidebar_title")}</h2><button ref={newButton} className="button button-primary button-inline" disabled={legacy || projects?.readOnly} onClick={() => setOpen(true)} type="button">{text("project_new")}</button></div>
    {legacy ? <p className="small-copy">{text("project_legacy")}</p> : <><label className="sr-only" htmlFor="project-search">{text("project_search")}</label><input className="project-search" id="project-search" onChange={(event) => setSearch(event.currentTarget.value)} placeholder={text("project_search")} type="search" value={search} /><ul className="project-list">
      {visible.map(row)}
      {visible.length === 0 ? <li className="small-copy">{text("project_none")}</li> : null}
    </ul>{archived.length ? <details className="archived-projects"><summary>{text("project_archived_projects")}</summary><ul className="project-list">{archived.map(row)}</ul></details> : null}{error && !open ? <p className="field-error" role="alert">{text(error === "initialization_ambiguous" ? "project_initialization_ambiguous" : "project_request_failed")}</p> : null}</>}
    {!legacy ? <button className="gallery-link" onClick={onGallery} type="button">{text("project_open_gallery")}</button> : null}
    <dialog aria-labelledby="new-project-title" className="project-dialog" onCancel={close} ref={dialog}>
      <form noValidate onSubmit={inspect}><div className="project-dialog-header"><h2 id="new-project-title">{text("project_new")}</h2><button aria-label={text("shell_close")} className="icon-close" onClick={close} type="button">×</button></div>
        {inspection === null ? <><fieldset disabled={busy}><label><input checked={mode === "new"} name="project-mode" onChange={() => setMode("new")} type="radio" /> {text("project_mode_new")}</label><label><input checked={mode === "existing"} name="project-mode" onChange={() => setMode("existing")} type="radio" /> {text("project_mode_existing")}</label><label htmlFor="project-name">{text("project_name")}</label><input id="project-name" maxLength={160} onChange={(event) => setName(event.target.value)} value={name} /><label htmlFor="project-path">{text("project_path")}</label><input id="project-path" onChange={(event) => setPath(event.target.value)} value={path} /></fieldset><p className="small-copy">{text("project_path_help")}</p><button className="button button-primary" type="submit">{busy ? text("working") : text("project_inspect")}</button></> : <><p>{text("project_inspection_ready")}: <strong>{inspection.name}</strong></p><p className="small-copy">{inspection.initializationRequired ? text("project_initialization_required") : text("project_connect_ready")}</p><p className="small-copy">{text("project_create_no_provider")}</p><div className="button-row"><button className="button button-secondary button-inline" disabled={busy} onClick={() => setInspection(null)} type="button">{text("project_back")}</button><button className="button button-primary" disabled={busy} onClick={() => void create()} type="button">{busy ? text("working") : text("project_create")}</button></div></>}
        {error ? <><p className="field-error" role="alert">{text(error === "initialization_ambiguous" ? "project_initialization_ambiguous" : "project_request_failed")}</p>{error === "initialization_ambiguous" ? <button className="button button-secondary" onClick={() => { setInspection(null); setMode("existing"); setError(null); }} type="button">{text("project_connect_existing")}</button> : null}</> : null}
      </form>
    </dialog>
  </aside>;
}
