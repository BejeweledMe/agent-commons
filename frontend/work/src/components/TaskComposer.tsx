import type { FormEvent, ReactElement } from "react";
import type { TaskOption } from "../contracts";
import type { MessageKey } from "../i18n";
import { stateLabel } from "../taskPresentation.js";

export type TaskDraft = { title: string; description: string; criteria: string; dependencyIds: readonly string[] };

export function TaskComposer({ draft, onChange, errors, busy, submitBlocked = false, writesEnabled, tasks, dependencyStatus = "current", onSubmit, onClose, onDependency, text }: {
  draft: TaskDraft;
  onChange: (draft: TaskDraft) => void;
  errors: ReadonlySet<string>;
  busy: boolean;
  submitBlocked?: boolean;
  writesEnabled: boolean;
  tasks: readonly TaskOption[];
  dependencyStatus?: "loading" | "unavailable" | "partial" | "current";
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onClose: () => void;
  onDependency: (id: string, selected: boolean) => void;
  text: (key: MessageKey) => string;
}): ReactElement {
  return <section className="task-composer" aria-labelledby="task-composer-title" onKeyDown={(event) => {
    if (event.key === "Escape" && !(event.target instanceof HTMLSelectElement)) { event.stopPropagation(); onClose(); }
  }}>
    <div className="composer-heading"><h2 id="task-composer-title">{text("shell_new_task")}</h2><button className="button button-secondary button-inline" type="button" onClick={onClose}>{text("shell_close")}</button></div>
    <p className="small-copy">{text("shell_composer_intro")}</p>
    <form noValidate onSubmit={onSubmit}>
      <fieldset disabled={!writesEnabled || busy}>
        <label htmlFor="task-title">{text("task_title")}</label>
        <input id="task-title" value={draft.title} aria-invalid={errors.has("title")} onChange={(event) => onChange({ ...draft, title: event.target.value })} placeholder={text("task_title_placeholder")} />
        {errors.has("title") ? <p className="field-error">{text("form_error_task_title")}</p> : null}
        <label htmlFor="task-description">{text("task_description")}</label>
        <textarea id="task-description" rows={4} value={draft.description} aria-invalid={errors.has("description")} onChange={(event) => onChange({ ...draft, description: event.target.value })} placeholder={text("task_description_placeholder")} />
        {errors.has("description") ? <p className="field-error">{text("form_error_task_description")}</p> : null}
        <label htmlFor="task-criteria">{text("acceptance_criteria")}</label>
        <textarea id="task-criteria" rows={3} value={draft.criteria} aria-invalid={errors.has("criteria")} onChange={(event) => onChange({ ...draft, criteria: event.target.value })} placeholder={text("acceptance_criteria_placeholder")} />
        {errors.has("criteria") ? <p className="field-error">{text("form_error_task_criteria")}</p> : null}
        <details className="dependency-picker"><summary>{text("task_dependencies")}</summary>
          <p className="small-copy">{text("task_dependencies_help")}</p>
          {dependencyStatus !== "current" ? <p className="inspector-notice" role="status">{text(`shell_dependencies_${dependencyStatus}`)}</p> : null}
          {dependencyStatus === "current" && tasks.length === 0 ? <p className="small-copy">{text("shell_dependencies_empty")}</p> : null}
          <div className="dependency-option-list">{tasks.map((task) => <label className="dependency-option" key={task.id}>
            <input type="checkbox" checked={draft.dependencyIds.includes(task.id)} onChange={(event) => onDependency(task.id, event.target.checked)} />
            <span>{task.title}</span><span className="dependency-option-state"><code>{task.state}</code> — {text(stateLabel("task", task.state))}</span>
          </label>)}</div>
          {draft.dependencyIds.filter((id) => !tasks.some((task) => task.id === id)).map((id) => <div className="dependency-unconfirmed" key={id}>
            <p className="small-copy">{text("shell_dependency_unconfirmed")} <code>{id}</code></p>
            <button className="button button-secondary" type="button" onClick={() => onDependency(id, false)}>{text("shell_remove_dependency")}</button>
          </div>)}
        </details>
        <button className="button button-primary" disabled={submitBlocked} type="submit">{busy ? text("working") : text("create_task")}</button>
      </fieldset>
    </form>
  </section>;
}
