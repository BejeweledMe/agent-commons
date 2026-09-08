import { type ReactElement, useRef, useState } from "react";
import { ProjectCreateDialog } from "./ProjectCreateDialog.js";
import { ProjectCreation, type FolderPurpose, type FolderSelection } from "../projectCreation.js";

import type { Locale, MessageKey } from "../i18n.js";
import type { ProjectInspection, ProjectList, ProjectRecord } from "../projectWorkspace.js";

type Props = {
  currentProjectId: string | null;
  legacy: boolean;
  locale: Locale;
  projects: ProjectList | null;
  text: (key: MessageKey) => string;
  creation?: ProjectCreation;
  onPickFolder?: (purpose: FolderPurpose, signal: AbortSignal) => Promise<FolderSelection>;
  onInspect: (input: { mode: "new" | "existing"; path: string; name: string }) => Promise<ProjectInspection>;
  onCreate: (inspection: ProjectInspection) => Promise<void>;
  onSelect: (projectId: string) => void;
  onUpdate: (projectId: string, expectedRevision: string, input: { name: string; archived: boolean }) => Promise<void>;
};

export function ProjectSidebar({ currentProjectId, legacy, locale, projects, text, onInspect, onCreate, onSelect, onUpdate, creation: suppliedCreation, onPickFolder }: Props): ReactElement {
  const localCreation = useRef(new ProjectCreation());
  const creation = suppliedCreation ?? localCreation.current;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<"request" | null>(null);
  const [renameId, setRenameId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [search, setSearch] = useState("");
  const newButton = useRef<HTMLButtonElement>(null);
  const close = () => window.setTimeout(() => newButton.current?.focus(), 0);
  async function update(projectId: string, expectedRevision: string, input: { name: string; archived: boolean }): Promise<boolean> {
    setBusy(true); setError(null);
    try { await onUpdate(projectId, expectedRevision, input); return true; }
    catch { setError("request"); return false; }
    finally { setBusy(false); }
  }
  const matching = (projects?.projects ?? []).filter((project) => project.name.toLocaleLowerCase(locale).includes(search.trim().toLocaleLowerCase(locale)));
  const visible = matching.filter((project) => !project.archived);
  const archived = matching.filter((project) => project.archived);
  const row = (project: ProjectRecord): ReactElement => {
    const renameInputId = `project-rename-${project.id}`;
    return <li className="project-row" key={project.id}><button aria-current={project.id === currentProjectId ? "page" : undefined} className="project-list-item" disabled={!project.available || project.state !== "ready"} onClick={() => onSelect(project.id)} type="button"><strong>{project.name}</strong><span>{project.archived ? text("project_archived") : project.state === "ready" ? text("project_ready") : text(`project_${project.state}` as MessageKey)}</span></button>
      <details className="project-actions"><summary><span aria-hidden="true">⋯</span><span className="sr-only">{text("project_actions")}: {project.name}</span></summary><div className="project-actions-menu">{renameId === project.id ? <form onSubmit={(event) => { event.preventDefault(); if (renameValue.trim() && renameValue.trim() !== project.name) void update(project.id, project.revision, { name: renameValue.trim(), archived: project.archived }).then((saved) => { if (saved) setRenameId(null); }); }}><label className="sr-only" htmlFor={renameInputId}>{text("project_rename")}</label><input autoFocus disabled={projects?.readOnly || busy} id={renameInputId} maxLength={160} onChange={(event) => setRenameValue(event.currentTarget.value)} value={renameValue} /><div className="button-row"><button className="button button-primary button-inline" disabled={projects?.readOnly || busy} type="submit">{text("project_rename")}</button><button className="button button-secondary button-inline" onClick={() => setRenameId(null)} type="button">{text("project_back")}</button></div></form> : <button className="notice-link" disabled={projects?.readOnly || busy} onClick={() => { setRenameId(project.id); setRenameValue(project.name); }} type="button">{text("project_rename")}</button>}<button className="notice-link" disabled={projects?.readOnly || busy} onClick={() => void update(project.id, project.revision, { name: project.name, archived: !project.archived })} type="button">{text(project.archived ? "project_restore" : "project_archive")}</button></div></details>
    </li>;
  };
  return <aside className="project-sidebar" aria-label={text("project_sidebar_label")}>
    <p className="app-brand">Agent Commons</p>
    <div className="project-sidebar-heading"><h2>{text("project_sidebar_title")}</h2><button ref={newButton} className="button button-primary button-inline" disabled={legacy || projects === null || projects.readOnly} onClick={() => creation.show()} type="button">{text("project_new")}</button></div>
    {legacy ? <p className="small-copy">{text("project_legacy")}</p> : <><label className="sr-only" htmlFor="project-search">{text("project_search")}</label><input className="project-search" id="project-search" onChange={(event) => setSearch(event.currentTarget.value)} placeholder={text("project_search")} type="search" value={search} /><ul className="project-list">
      {visible.map(row)}
      {visible.length === 0 ? <li className="small-copy">{text("project_none")}</li> : null}
    </ul>{archived.length ? <details className="archived-projects"><summary>{text("project_archived_projects")}</summary><ul className="project-list">{archived.map(row)}</ul></details> : null}{error ? <p className="field-error" role="alert">{text("project_request_failed")}</p> : null}</>}
    <ProjectCreateDialog creation={creation} locale={locale} writable={!legacy && projects !== null && !projects.readOnly} onPickFolder={onPickFolder} onInspect={onInspect} onCreate={onCreate} onClose={close} />
  </aside>;
}
