import { type KeyboardEvent, type ReactElement, useEffect, useRef, useState } from "react";
import { ApiProblem, requestOutcome, type WorkApi } from "../api";
import { recordInstrumentation } from "../instrumentation.js";
import type { TasksView } from "../appRouteState.js";
import type { TrackerTask } from "../contracts";
import type { Locale, MessageKey } from "../i18n";
import { bucketTasksForNow, filterTrackerTasks, TASK_FILTERS, taskFilterLabel, taskObservationCurrent, trackerCapacityOnlyGap, type NowBuckets, type TaskFilter } from "../taskPresentation.js";
import { trackerLoadFailed, trackerLoadSucceeded, trackerStreamSucceeded, type TrackerViewState } from "../trackerState.js";
import { TaskInspector, TaskState, type RenderedTask } from "./TaskInspector.js";
import { TaskViews } from "./TaskViews.js";
import { TaskEditor, type TaskEditorEntries } from "./TaskEditor.js";
import "../taskGraph.css";

type Props = {
  api: WorkApi;
  writesEnabled?: boolean;
  onObservation?: (state: TrackerViewState) => void;
  locale: Locale;
  text: (key: MessageKey) => string;
  selectedTaskId: string | null;
  onSelectTask: (id: string | null) => void;
  onLaunchTask: (id: string) => void;
  search: string;
  filter: TaskFilter;
  onSearchChange: (value: string) => void;
  onFilterChange: (value: TaskFilter) => void;
  /** Which of Now / Map / All tasks is open; presentation route state only. */
  tasksView: TasksView;
  onTasksViewChange: (value: TasksView) => void;
  /** Narrow the tracker to one role's tasks (navigation state from the board). */
  agentId?: string | null;
  onClearAgent?: () => void;
};
type TrackerTaskActionName = "request_review" | "accept_task" | "reopen_task";
/** `expectedRevision` is the revision the inspector rendered when the intent was formed; accept and return post exactly it. */
type TaskActionIntent = Readonly<{ action: TrackerTaskActionName; taskId: string; value: string | readonly string[]; key: string; expectedRevision: string | null }>;
type TrackerActionState =
  | { kind: "idle" }
  | { kind: "submitting"; action: TrackerTaskActionName }
  | { kind: "success"; message: MessageKey }
  | { kind: "error"; code: string; safeNextActions: readonly string[]; uncertain: boolean; intent: TaskActionIntent };
type TaskActionDraft = { reviewCriteria: string; acceptSummary: string; reopenReason: string; reopenConfirmed: boolean };
const PRIMARY_FILTERS: readonly TaskFilter[] = ["all", "attention", "active"];
const MORE_FILTERS = TASK_FILTERS.filter((value) => !PRIMARY_FILTERS.includes(value));
const emptyDraft: TaskActionDraft = { reviewCriteria: "", acceptSummary: "", reopenReason: "", reopenConfirmed: false };
const emptyBuckets: NowBuckets = { needs_you: [], in_progress: [], next: [], settled: [] };
function criteriaLines(value: string): readonly string[] {
  return value.split("\n").map((item) => item.trim()).filter((item) => item.length > 0);
}

function trackerActionFailure(error: unknown): { code: string; safeNextActions: readonly string[]; uncertain: boolean } {
  if (error instanceof ApiProblem) {
    return {
      code: error.apiError?.code ?? (error.status === 401 ? "unauthorized" : "request_unavailable"),
      safeNextActions: error.apiError?.safeNextActions ?? [],
      uncertain: !(error.status >= 400 && error.status < 500)
    };
  }
  return {
    code: "request_unavailable",
    safeNextActions: [],
    uncertain: true
  };
}

export function TrackerSection({ api, writesEnabled = false, onObservation, locale, text, selectedTaskId, onSelectTask, onLaunchTask, search, filter, onSearchChange, onFilterChange, tasksView, onTasksViewChange, agentId = null, onClearAgent }: Props): ReactElement {
  const [state, setState] = useState<TrackerViewState>({ kind: "loading" });
  const [editorEntries, setEditorEntries] = useState<TaskEditorEntries>({});
  const selectedTaskRef = useRef(selectedTaskId);
  selectedTaskRef.current = selectedTaskId;
  const [drafts, setDrafts] = useState<Readonly<Record<string, TaskActionDraft>>>({});
  const [actionStates, setActionStates] = useState<Readonly<Record<string, TrackerActionState>>>({});
  const { reviewCriteria, acceptSummary, reopenReason, reopenConfirmed } = drafts[selectedTaskId ?? ""] ?? emptyDraft;
  const actionState = actionStates[selectedTaskId ?? ""] ?? { kind: "idle" };
  function updateDraft(changes: Partial<TaskActionDraft>): void {
    if (selectedTaskId === null) return;
    setDrafts((current) => ({ ...current, [selectedTaskId]: { ...(current[selectedTaskId] ?? emptyDraft), ...changes } }));
  }
  const setReviewCriteria = (value: string): void => updateDraft({ reviewCriteria: value });
  const setAcceptSummary = (value: string): void => updateDraft({ acceptSummary: value });
  const setReopenReason = (value: string): void => updateDraft({ reopenReason: value });
  const setReopenConfirmed = (value: boolean): void => updateDraft({ reopenConfirmed: value });
  function setTaskActionState(taskId: string, next: TrackerActionState): void {
    setActionStates((current) => ({ ...current, [taskId]: next }));
  }
  const taskButtons = useRef(new Map<string, HTMLButtonElement>());
  const searchField = useRef<HTMLInputElement>(null);
  const actionKeys = useRef(new Map<string, string>());
  async function load(signal: AbortSignal): Promise<void> {
    try {
      const snapshot = await api.loadTracker(signal);
      if (!signal.aborted) setState((current) => trackerLoadSucceeded(current, snapshot));
    } catch {
      if (!signal.aborted) setState(trackerLoadFailed);
    }
  }
  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    const close = api.openTrackerStream(
      (snapshot) => { if (!controller.signal.aborted) setState((current) => trackerStreamSucceeded(current, snapshot)); },
      (connection) => { if (!controller.signal.aborted) setState((current) => current.kind === "ready" ? { ...current, connection } : current); },
      () => { if (!controller.signal.aborted) setState(trackerLoadFailed); }
    );
    return () => { controller.abort(); close(); };
  }, [api]);
  useEffect(() => { onObservation?.(state); }, [state, onObservation]);
  const tasks = state.kind === "ready" ? state.snapshot.tasks : [];
  const visibleTasks = state.kind === "ready" ? filterTrackerTasks(state.snapshot, filter, search, agentId) : [];
  const buckets = state.kind === "ready" ? bucketTasksForNow(state.snapshot, agentId) : emptyBuckets;
  // Keyboard order follows what the open view actually shows, column by column.
  const navigableTasks: readonly TrackerTask[] = tasksView === "now"
    ? [...buckets.needs_you, ...buckets.in_progress, ...buckets.next]
    : visibleTasks;
  const selectedTask = tasks.find((task) => task.taskId === selectedTaskId) ?? null;
  const actionsCurrent = writesEnabled && state.kind === "ready" && selectedTask !== null
    && taskObservationCurrent(state.snapshot, selectedTask)
    && state.connection !== "disconnected";
  function selectTask(taskId: string | null): void {
    onSelectTask(taskId);
    if (taskId === null) {
      const previousId = selectedTaskId;
      requestAnimationFrame(() => {
        const button = previousId === null ? undefined : taskButtons.current.get(previousId);
        (button ?? searchField.current)?.focus();
      });
    }
  }
  function moveTaskFocus(event: KeyboardEvent<HTMLButtonElement>, taskId: string): void {
    if (!["ArrowDown", "ArrowRight", "ArrowUp", "ArrowLeft", "Home", "End"].includes(event.key) || navigableTasks.length === 0) return;
    event.preventDefault();
    const current = navigableTasks.findIndex((task) => task.taskId === taskId);
    const next = event.key === "Home" ? 0 : event.key === "End" ? navigableTasks.length - 1
      : event.key === "ArrowDown" || event.key === "ArrowRight" ? (current + 1) % navigableTasks.length
        : (current - 1 + navigableTasks.length) % navigableTasks.length;
    const nextId = navigableTasks[next].taskId;
    selectTask(nextId);
    taskButtons.current.get(nextId)?.focus();
  }
  function taskActionIntent(action: TrackerTaskActionName, task: TrackerTask, expectedRevision: string | null): TaskActionIntent {
    const value = action === "request_review" ? criteriaLines(reviewCriteria)
      : action === "accept_task" ? acceptSummary.trim() : reopenReason.trim();
    const signature = JSON.stringify([action, task.taskId, value]);
    let key = actionKeys.current.get(signature);
    if (!key) {
      key = `tracker-${action}-${crypto.randomUUID()}`;
      actionKeys.current.set(signature, key);
    }
    return { action, taskId: task.taskId, value, key, expectedRevision };
  }

  async function performTaskAction(action: TrackerTaskActionName, task: TrackerTask, expectedRevision: string | null): Promise<void> {
    await performTaskIntent(taskActionIntent(action, task, expectedRevision));
  }

  async function performTaskIntent(intent: TaskActionIntent): Promise<void> {
    const signal = new AbortController().signal;
    const { action, taskId, value, key, expectedRevision } = intent;
    setTaskActionState(taskId, { kind: "submitting", action });
    const started = performance.now();
    try {
      if (action === "request_review") {
        await api.requestTaskReview(taskId, value as readonly string[], key, signal);
      } else if (expectedRevision === null) {
        // Nothing was rendered to accept or return: refuse here rather than
        // fetch a revision the person has not seen.
        throw new ApiProblem(409, {
          code: "tracker_task_revision_unavailable",
          message: "the task detail has not been rendered yet",
          safeNextActions: ["Wait for the task detail to load, then retry."]
        });
      } else if (action === "accept_task") {
        await api.acceptTask(taskId, value as string, key, signal, expectedRevision);
      } else {
        await api.reopenTask(taskId, value as string, key, signal, expectedRevision);
      }
      const snapshot = await api.loadTracker(signal);
      setState((current) => trackerLoadSucceeded(current, snapshot));
      actionKeys.current.delete(JSON.stringify([action, taskId, value]));
      api.forgetTaskWrite(key);
      setTaskActionState(taskId, { kind: "success", message: "tracker_action_success" });
      // Which action it was is not recorded: only that a task action finished.
      recordInstrumentation("task_action", "confirmed", performance.now() - started);
    } catch (error: unknown) {
      recordInstrumentation("task_action", requestOutcome(error), performance.now() - started);
      if (!(error instanceof DOMException && error.name === "AbortError")) {
        setTaskActionState(taskId, { kind: "error", intent, ...trackerActionFailure(error) });
      }
    }
  }

  function trackerTaskActions(task: TrackerTask, renderedRevision: string | null): ReactElement | null {
    const hasCanonicalAction = task.nextAction === "request_review"
      || task.nextAction === "accept_task"
      || task.nextAction === "revise_work";
    if (!hasCanonicalAction && actionState.kind === "idle") {
      return null;
    }
    const busy = actionState.kind === "submitting" || !actionsCurrent
      || (actionState.kind === "error" && actionState.uncertain);
    const submitting = (action: TrackerTaskActionName): boolean => (
      actionState.kind === "submitting" && actionState.action === action
    );
    return (
      <div className="tracker-actions" aria-labelledby="tracker-actions-title">
        <h4 id="tracker-actions-title">{text("tracker_actions_title")}</h4>
        {task.nextAction === "request_review" ? (
          <div className="tracker-action-panel">
            <label htmlFor="tracker-review-criteria">{text("tracker_review_criteria_label")}</label>
            <textarea
              disabled={busy}
              id="tracker-review-criteria"
              onChange={(event) => setReviewCriteria(event.target.value)}
              value={reviewCriteria}
            />
            <p className="small-copy">{text("tracker_review_criteria_help")}</p>
            <button
              className="button button-primary"
              disabled={busy}
              onClick={() => void performTaskAction("request_review", task, renderedRevision)}
              type="button"
            >
              {submitting("request_review") ? text("working") : text("tracker_request_review_action")}
            </button>
          </div>
        ) : null}
        {task.nextAction === "accept_task" ? (
          <div className="tracker-action-panel">
            <label htmlFor="tracker-accept-summary">{text("tracker_accept_summary_label")}</label>
            <textarea
              disabled={busy}
              aria-invalid={acceptSummary.trim().length === 0 ? "true" : undefined}
              id="tracker-accept-summary"
              onChange={(event) => setAcceptSummary(event.target.value)}
              value={acceptSummary}
            />
            {acceptSummary.trim().length === 0 ? (
              <p className="field-error">{text("tracker_action_required")}</p>
            ) : null}
            <button
              className="button button-primary"
              disabled={busy || acceptSummary.trim().length === 0}
              onClick={() => void performTaskAction("accept_task", task, renderedRevision)}
              type="button"
            >
              {submitting("accept_task") ? text("working") : text("tracker_accept_action")}
            </button>
          </div>
        ) : null}
        {task.nextAction === "revise_work" ? (
          <div className="tracker-action-panel">
            <label htmlFor="tracker-reopen-reason">{text("tracker_reopen_reason_label")}</label>
            <textarea
              disabled={busy}
              aria-invalid={reopenReason.trim().length === 0 ? "true" : undefined}
              id="tracker-reopen-reason"
              onChange={(event) => setReopenReason(event.target.value)}
              value={reopenReason}
            />
            {reopenReason.trim().length === 0 ? (
              <p className="field-error">{text("tracker_action_required")}</p>
            ) : null}
            <label className="tracker-confirm">
              <input
                disabled={busy}
                checked={reopenConfirmed}
                onChange={(event) => setReopenConfirmed(event.target.checked)}
                type="checkbox"
              />
              <span>{text("tracker_reopen_confirm_label")}</span>
            </label>
            <button
              className="button button-primary"
              disabled={busy || reopenReason.trim().length === 0 || !reopenConfirmed}
              onClick={() => void performTaskAction("reopen_task", task, renderedRevision)}
              type="button"
            >
              {submitting("reopen_task") ? text("working") : text("tracker_reopen_action")}
            </button>
          </div>
        ) : null}
        {actionState.kind === "success" ? (
          <p className="tracker-action-status" role="status">{text(actionState.message)}</p>
        ) : null}
        {actionState.kind === "error" && actionState.intent.taskId === task.taskId ? (
          <div className="tracker-state tracker-state-error" role="alert">
            <h4>{text("tracker_action_failed")}</h4>
            <p>{text(actionState.uncertain ? "tracker_retry_previous_help" : "inspector_known_refusal")}</p>
            <button className="button button-secondary" type="button"
              onClick={() => void performTaskIntent(actionState.intent)}>
              {text("tracker_retry_previous_action")}
            </button>
            {!actionState.uncertain ? <button className="button button-secondary" type="button" disabled={!actionsCurrent} onClick={() => {
              const { action, taskId, value, key } = actionState.intent;
              actionKeys.current.delete(JSON.stringify([action, taskId, value]));
              api.forgetTaskWrite(key);
              setTaskActionState(taskId, { kind: "idle" });
            }}>{text("inspector_new_attempt")}</button> : null}
            <p><code>{actionState.code}</code></p>
            {actionState.safeNextActions.length > 0 ? (
              <>
                <p>{text("tracker_action_safe_next")}</p>
                <ul>
                  {actionState.safeNextActions.map((next) => <li key={next}>{next}</li>)}
                </ul>
              </>
            ) : null}
          </div>
        ) : null}
      </div>
    );
  }

  if (state.kind === "loading") return <section className="tracker-section work-tracker" aria-label={text("tracker_title")}><p role="status" aria-live="polite">{text("tracker_loading")}</p></section>;
  if (state.kind === "failure") return <section className="tracker-section work-tracker" aria-label={text("tracker_title")}><div className="tracker-state tracker-state-error" role="alert"><h2>{text("tracker_error_title")}</h2><p>{text("tracker_error_next")}</p><button type="button" className="button button-secondary" onClick={() => void load(new AbortController().signal)}>{text("tracker_retry")}</button></div></section>;
  const { snapshot } = state;
  const stale = snapshot.state === "stale" || snapshot.freshness.state === "stale" || snapshot.freshness.resumeGap || state.connection === "disconnected";
  return <section className="tracker-section work-tracker" aria-label={text("tracker_title")}>
    <div className="task-view-toolbar">
      {/* Search and the status filters belong to Map and All tasks; the Now
          columns are the filter, so no inert control is offered there. */}
      {tasksView === "now" ? null : <div className="task-search-field"><label htmlFor="task-view-search">{text("task_view_search")}</label>
      <input id="task-view-search" ref={searchField} type="search" value={search} maxLength={200} onChange={(event) => onSearchChange(event.target.value)} /></div>}
      <div className="tracker-connection" role="status">{text(state.connection === "connected" ? "tracker_updates_connected" : state.connection === "connecting" ? "tracker_updates_connecting" : "tracker_updates_disconnected")}</div>
      <button type="button" className="button button-secondary button-inline" onClick={() => void load(new AbortController().signal)}>{text("task_view_refresh")}</button>
    </div>
    {agentId !== null ? <p className="notice tracker-agent-filter" role="status">{text("tracker_agent_filter")}: <strong>{(state.kind === "ready" ? state.snapshot.tasks.find((task) => task.suggestedAgentId === agentId)?.suggestedRoleName ?? state.snapshot.runs.find((run) => run.agentId === agentId)?.roleName : null) ?? agentId}</strong>{onClearAgent ? <button type="button" className="notice-link" onClick={onClearAgent}>{text("tracker_agent_filter_clear")}</button> : null}</p> : null}
    {tasksView === "now" ? null : <div className="task-view-filters" role="group" aria-label={text("task_view_filter")}>
      {PRIMARY_FILTERS.map((value) => <button key={value} type="button" aria-pressed={filter === value} onClick={() => onFilterChange(value)}>{text(taskFilterLabel[value])}</button>)}
      <label className="task-more-filter" htmlFor="task-more-filter">
        <span className="visually-hidden">{text("task_view_more_status")}</span>
        <select id="task-more-filter" value={MORE_FILTERS.includes(filter) ? filter : ""}
          onChange={(event) => { const next = event.target.value; if (MORE_FILTERS.some((value) => value === next)) onFilterChange(next as TaskFilter); }}>
          <option value="" disabled>{text("task_view_more_status")}</option>
          {MORE_FILTERS.map((value) => <option key={value} value={value}>{text(taskFilterLabel[value])}</option>)}
        </select>
      </label>
    </div>}
    {stale ? <div className="tracker-state tracker-state-warning" role="status"><strong>{text("tracker_stale_title")}</strong><p>{text(snapshot.freshness.resumeGap ? "tracker_resume_gap" : "tracker_stale_next")}</p></div> : null}
    {(snapshot.state === "partial" && !trackerCapacityOnlyGap(snapshot)) || snapshot.truncated ? <div className="tracker-state tracker-state-warning" role="status"><strong>{text("tracker_partial_title")}</strong><p>{text("tracker_partial_next")}</p></div> : null}
    {snapshot.state === "error" ? <div className="tracker-state tracker-state-error" role="alert"><strong>{text("tracker_projection_error_title")}</strong><p>{text("tracker_projection_error_next")}</p></div> : null}
    {snapshot.state === "loading" ? <p role="status">{text("tracker_loading")}</p> : null}
    <div className={`task-workspace${selectedTaskId !== null ? " task-workspace-selected" : ""}`}>
      <section className="task-list-pane" aria-labelledby="task-list-title">
        <h2 id="task-list-title">{text("tracker_title")} <span className="task-list-count" aria-label={`${text("task_view_task_count")}: ${navigableTasks.length}/${tasks.length}`}>{navigableTasks.length}/{tasks.length}</span></h2>
        <TaskViews view={tasksView} onViewChange={onTasksViewChange} snapshot={snapshot} visibleTasks={visibleTasks} buckets={buckets}
          selectedTaskId={selectedTaskId} locale={locale} text={text} onSelectTask={selectTask} onTaskKeyDown={moveTaskFocus}
          registerTaskButton={(taskId, element) => { if (element) taskButtons.current.set(taskId, element); else taskButtons.current.delete(taskId); }}
          onClearFilters={() => { onSearchChange(""); onFilterChange("all"); }} />
      </section>
      {selectedTaskId === null ? <div className="task-inspector task-inspector-empty"><p>{text("task_view_no_selection")}</p></div>
        : <div onKeyDown={(event) => {
          if (event.key === "Escape" && !(event.target instanceof HTMLSelectElement)) { event.preventDefault(); selectTask(null); }
        }}>
          {selectedTask === null ? <section className="task-inspector" tabIndex={-1} aria-labelledby="inspector-missing-title"><header className="inspector-header"><h2 id="inspector-missing-title">{text("inspector_missing")}</h2><button type="button" className="button button-secondary" onClick={() => selectTask(null)}>{text("inspector_close")}</button></header><p>{text("inspector_missing_help")}</p><details className="inspector-technical"><summary>{text("inspector_technical")}</summary><code>{selectedTaskId}</code></details></section>
            : <TaskInspector api={api} task={selectedTask} tasks={tasks} runs={snapshot.runs} sourceRevision={snapshot.sourceRevision} locale={locale} text={text} actionsCurrent={actionsCurrent} writesEnabled={writesEnabled} onSelectTask={selectTask} onLaunchTask={onLaunchTask}>
              {({ revision }: RenderedTask) => <>{trackerTaskActions(selectedTask, revision)}
              <TaskEditor api={api} task={selectedTask} tasks={tasks} sourceRevision={snapshot.sourceRevision} locale={locale}
                entries={editorEntries} setEntries={setEditorEntries}
                writesEnabled={writesEnabled} actionsCurrent={actionsCurrent} onSelectTask={(id) => {
                  if (selectedTaskRef.current === selectedTask.taskId) selectTask(id);
                }}
                onChanged={() => void load(new AbortController().signal)} />
            </>}
            </TaskInspector>}
        </div>}
    </div>
    <details className="tracker-diagnostics inspector-technical"><summary>{text("task_view_diagnostics")}</summary>
      <dl className="tracker-summary"><div><dt>{text("tracker_source_revision")}</dt><dd><code>{snapshot.sourceRevision ?? "—"}</code></dd></div>
        <div><dt>{text("tracker_truncated")}</dt><dd>{text(snapshot.truncated ? "tracker_truncated_yes" : "tracker_truncated_no")}</dd></div>
        <div><dt>{text("tracker_freshness")}</dt><dd><TaskState domain="freshness" value={snapshot.freshness.state} text={text} /></dd></div>
        <div><dt>{text("tracker_capacity")}</dt><dd><TaskState domain="capacity" value={snapshot.capacity.state} text={text} /></dd></div>
        <div><dt>{text("tracker_gaps_label")}</dt><dd>{snapshot.gaps.length > 0 ? snapshot.gaps.map((gap) => <code key={gap}>{gap} </code>) : "—"}</dd></div>
      </dl>
      <p className="small-copy">{text("tracker_critical_path_note")}</p><p><code>{snapshot.criticalPathTaskIds.join(" → ") || "—"}</code></p>
    </details>
  </section>;
}
