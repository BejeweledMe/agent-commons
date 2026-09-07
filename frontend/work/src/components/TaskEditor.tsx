import { type Dispatch, type ReactElement, type SetStateAction, useEffect, useMemo, useRef } from "react";
import { ApiProblem, type WorkApi } from "../api.js";
import type { TrackerTask } from "../contracts";
import type { Locale } from "../i18n";
import { TaskGraphApi, type TaskEditDetail, type TaskEditInput } from "../taskGraphApi.js";
import { wouldCreateTaskCycle } from "../taskGraph.js";
import { taskGraphText, type TaskGraphMessage } from "../taskGraphStrings.js";
import { editorInput, observeTaskEditorDraft, taskEditorDraft, taskEditorInputError, type TaskEditorDraft } from "../taskEditorState.js";

type Mode = "closed" | "edit" | "dependent" | "cancel";
type Intent = { kind: "edit"; taskId: string; revision: string; input: TaskEditInput; key: string }
  | { kind: "dependent"; taskId: string; revision: string; input: TaskEditInput; key: string }
  | { kind: "cancel"; taskId: string; revision: string; reason: string; key: string };
type Action = { kind: "idle" } | { kind: "pending" } | { kind: "success" }
  | { kind: "failure"; code: string; uncertain: boolean; intent: Intent };
type Entry = { mode: Mode; detail: TaskEditDetail | null; draft: TaskEditorDraft | null;
  dependent: TaskEditorDraft | null; reason: string; loading: boolean; replacing: boolean; loadFailed: boolean; action: Action };
export type TaskEditorEntries = Readonly<Record<string, Entry>>;
const emptyEntry: Entry = { mode: "closed", detail: null, draft: null, dependent: null, reason: "", loading: false, replacing: false, loadFailed: false, action: { kind: "idle" } };
function failure(error: unknown): { code: string; uncertain: boolean } {
  return error instanceof ApiProblem ? { code: error.apiError?.code ?? "request_unavailable", uncertain: error.status < 400 || error.status >= 500 }
    : { code: "request_unavailable", uncertain: true };
}
function refusalMessage(code: string): TaskGraphMessage {
  return ({ task_live_work: "live", task_edit_stale: "stale", task_dependency_cycle: "cycle",
    task_dependency_missing: "missing", task_edit_state: "unavailable" } as Record<string, TaskGraphMessage>)[code] ?? "failed";
}

export function TaskEditor({ api, task, tasks, sourceRevision, locale, writesEnabled, actionsCurrent, onChanged, onSelectTask, entries, setEntries }: {
  api: WorkApi; task: TrackerTask; tasks: readonly TrackerTask[]; sourceRevision: string | null; locale: Locale;
  writesEnabled: boolean; actionsCurrent: boolean; onChanged: () => void; onSelectTask: (id: string) => void;
  entries: TaskEditorEntries; setEntries: Dispatch<SetStateAction<TaskEditorEntries>>;
}): ReactElement {
  const client = useMemo(() => new TaskGraphApi(api), [api]);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const entry = entries[task.taskId] ?? emptyEntry;
  const t = (key: TaskGraphMessage): string => taskGraphText(locale, key);
  function update(id: string, changes: Partial<Entry>): void {
    setEntries((current) => ({ ...current, [id]: { ...(current[id] ?? emptyEntry), ...changes } }));
  }
  async function load(id: string, signal: AbortSignal, replace = false): Promise<void> {
    update(id, { loading: true, loadFailed: false, ...(replace ? { replacing: true } : {}) });
    try {
      const detail = await client.detail(id, signal);
      if (signal.aborted || (!mounted.current && !replace)) return;
      setEntries((current) => { const old = current[id] ?? emptyEntry;
        return { ...current, [id]: { ...old, detail, loading: false, loadFailed: false, ...(replace ? { replacing: false } : {}),
          draft: replace ? taskEditorDraft(detail) : observeTaskEditorDraft(old.draft, detail) } }; });
    } catch { if (!signal.aborted) update(id, { loading: false, loadFailed: true, ...(replace ? { replacing: false } : {}) }); }
  }
  useEffect(() => {
    if (entry.mode === "closed") return;
    const controller = new AbortController();
    void load(task.taskId, controller.signal);
    return () => controller.abort();
  }, [client, task.taskId, sourceRevision, entry.mode]);
  const pending = entry.action.kind === "pending";
  const uncertain = entry.action.kind === "failure" && entry.action.uncertain;
  const dependent = entry.mode === "dependent";
  const draft = dependent ? entry.dependent : entry.draft;
  const input = draft === null ? null : editorInput(draft);
  const inputError = input === null ? null : taskEditorInputError(input);
  const cycle = !dependent && input !== null && wouldCreateTaskCycle(tasks, task.taskId, input.dependencyIds);
  const missing = input !== null && input.dependencyIds.some((id) => !tasks.some((candidate) => candidate.taskId === id));
  const permitted = actionsCurrent && (dependent || (entry.mode === "cancel" ? entry.detail?.cancellable : entry.detail?.editable));
  const stale = !dependent && entry.draft?.stale === true;
  const disabled = pending || uncertain || !permitted || entry.loading || entry.loadFailed || stale;

  function mode(next: Mode): void {
    const additions = next === "dependent" && entry.dependent === null ? {
      dependent: { taskId: task.taskId, revision: "", title: "", description: "", criteria: "", dependencyIds: [task.taskId], stale: false }
    } : {};
    update(task.taskId, { mode: next, ...additions });
  }
  function changeDraft(changes: Partial<TaskEditorDraft>): void {
    if (draft !== null) update(task.taskId, dependent ? { dependent: { ...draft, ...changes } } : { draft: { ...draft, ...changes } });
  }
  async function perform(intent: Intent): Promise<void> {
    update(intent.taskId, { action: { kind: "pending" } });
    const signal = new AbortController().signal;
    try {
      if (intent.kind === "dependent") {
        const result = await api.createTask(intent.input, signal, intent.key);
        update(intent.taskId, { action: { kind: "success" }, dependent: null, mode: "closed" });
        api.forgetTaskWrite(intent.key); onChanged(); onSelectTask(result.taskId);
      } else {
        if (intent.kind === "edit") await client.save(intent.taskId, intent.revision, intent.input, intent.key, signal);
        else await client.cancel(intent.taskId, intent.revision, intent.reason, intent.key, signal);
        update(intent.taskId, { action: { kind: "success" } });
        client.forget(intent.key); onChanged(); await load(intent.taskId, signal, true);
      }
    } catch (error) { update(intent.taskId, { action: { kind: "failure", intent, ...failure(error) } }); }
  }
  function submit(): void {
    if (disabled) return;
    const key = `task-editor-${crypto.randomUUID()}`;
    if (entry.mode === "cancel" && entry.detail && entry.reason.trim()) {
      void perform({ kind: "cancel", taskId: task.taskId, revision: entry.detail.revision, reason: entry.reason.trim(), key });
    } else if (draft && input && !inputError && !cycle && !missing) {
      void perform({ kind: dependent ? "dependent" : "edit", taskId: task.taskId, revision: draft.revision, input, key });
    }
  }
  const controlsLocked = pending || uncertain || entry.replacing || !writesEnabled;
  return <section className="task-editor" aria-label={t("edit")} onKeyDown={(event) => {
    if (event.key === "Escape" && entry.mode !== "closed") { event.stopPropagation(); event.preventDefault(); mode("closed"); }
  }}>
    <div className="task-editor-toolbar">
      <button type="button" className="button button-secondary" disabled={pending || !writesEnabled} aria-expanded={entry.mode === "edit"} onClick={() => mode("edit")}>{t("edit")}</button>
      <button type="button" className="button button-secondary" disabled={pending || !writesEnabled} aria-expanded={dependent} onClick={() => mode("dependent")}>{t("addDependent")}</button>
      <button type="button" className="button button-secondary" disabled={pending || !writesEnabled} aria-expanded={entry.mode === "cancel"} onClick={() => mode("cancel")}>{t("cancel")}</button>
    </div>
    {entry.mode === "closed" ? null : <div>
      <div className="task-editor-buttons"><strong>{t(dependent ? "addDependent" : entry.mode === "cancel" ? "cancel" : "edit")}</strong>
        <button type="button" className="button button-secondary button-inline" onClick={() => mode("closed")}>{t("close")}</button></div>
      <p className="small-copy">{t("draftKept")}</p>
      {entry.loading && entry.detail === null ? <p role="status">{t("loading")}</p> : null}
      {entry.loadFailed ? <p className="field-error" role="alert">{t("loadFailed")}</p> : null}
      {stale ? <p className="inspector-notice" role="status">{t("stale")}</p> : null}
      {!actionsCurrent ? <p className="inspector-notice">{t("readonly")}</p> : null}
      {!dependent && entry.detail?.refusalCode ? <p className="inspector-notice">{t(refusalMessage(entry.detail.refusalCode))}</p> : null}
      {entry.mode === "cancel" ? <div className="task-editor-cancel"><p>{t("cancelHelp")}</p>
        <label htmlFor="task-editor-reason">{t("reason")}</label><textarea id="task-editor-reason" value={entry.reason} maxLength={4096} disabled={controlsLocked} onChange={(event) => update(task.taskId, { reason: event.target.value })} />
        <button type="button" className="button button-secondary" disabled={disabled || !entry.reason.trim()} onClick={submit}>{pending ? t("working") : t("cancelConfirm")}</button>
      </div> : draft === null ? null : <form className="task-editor-form" onSubmit={(event) => { event.preventDefault(); submit(); }}>
        <label htmlFor="task-editor-title">{t("title")}</label><input id="task-editor-title" value={draft.title} maxLength={512} disabled={controlsLocked} onChange={(event) => changeDraft({ title: event.target.value })} />
        <label htmlFor="task-editor-description">{t("description")}</label><textarea id="task-editor-description" rows={5} value={draft.description} maxLength={16000} disabled={controlsLocked} onChange={(event) => changeDraft({ description: event.target.value })} />
        <label htmlFor="task-editor-criteria">{t("criteria")}</label><textarea id="task-editor-criteria" rows={4} value={draft.criteria} disabled={controlsLocked} onChange={(event) => changeDraft({ criteria: event.target.value })} />
        <details><summary>{t("dependencies")} ({draft.dependencyIds.length})</summary>
          <div className="task-editor-dependencies">{tasks.filter((candidate) => dependent || candidate.taskId !== task.taskId).map((candidate) => <label className="task-editor-dependency" key={candidate.taskId}>
            <input type="checkbox" disabled={controlsLocked} checked={draft.dependencyIds.includes(candidate.taskId)} onChange={(event) => changeDraft({ dependencyIds: event.target.checked
              ? [...draft.dependencyIds, candidate.taskId] : draft.dependencyIds.filter((id) => id !== candidate.taskId) })} />
            <span>{candidate.title}{candidate.taskState === "cancelled" ? ` — ${t("cancelledDependency")}` : ""}</span>
          </label>)}</div>
          {draft.dependencyIds.filter((id) => !tasks.some((candidate) => candidate.taskId === id)).map((id) => <p key={id}><code>{id}</code> <button type="button" className="button button-secondary" disabled={controlsLocked} onClick={() => changeDraft({ dependencyIds: draft.dependencyIds.filter((value) => value !== id) })}>{t("removeDependency")}</button></p>)}
        </details>
        {inputError || cycle || missing ? <p className="field-error" role="status">{t(inputError ?? (cycle ? "cycle" : "missing"))}</p> : null}
        <button type="submit" className="button button-primary" disabled={disabled || inputError !== null || cycle || missing}>{pending ? t("working") : t(dependent ? "add" : "save")}</button>
      </form>}
      {entry.action.kind === "success" ? <p role="status">{t("success")}</p> : null}
      {entry.action.kind === "failure" ? <div className="task-editor-error" role="alert">
        <p>{t(entry.action.uncertain ? "uncertain" : refusalMessage(entry.action.code))}</p><code>{entry.action.code}</code>
        <div className="task-editor-buttons"><button type="button" className="button button-secondary" disabled={!writesEnabled} onClick={() => { if (entry.action.kind === "failure") void perform(entry.action.intent); }}>{t("retry")}</button>
          {!entry.action.uncertain ? <button type="button" className="button button-secondary" onClick={() => {
            if (entry.action.kind === "failure") { client.forget(entry.action.intent.key); api.forgetTaskWrite(entry.action.intent.key); }
            update(task.taskId, { action: { kind: "idle" } }); void load(task.taskId, new AbortController().signal, true);
          }}>{t("newAttempt")}</button> : null}</div>
      </div> : null}
      {entry.loadFailed || stale ? <button type="button" className="button button-secondary" disabled={pending || uncertain} onClick={() => void load(task.taskId, new AbortController().signal, true)}>{t("refresh")}</button> : null}
    </div>}
  </section>;
}
