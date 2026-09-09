import { ConversationButton } from "./ConversationPanel.js";
import { OutputsButton } from "./OutputsPanel.js";
import { type ReactElement, type ReactNode, useEffect, useState } from "react";
import type { WorkApi } from "../api";
import type { TaskDetail, TrackerRun, TrackerTask } from "../contracts";
import type { Locale, MessageKey } from "../i18n";
import { attemptLimitText } from "../launchIntentState.js";
import { loadTaskDetailState, type TaskDetailState } from "../trackerState.js";
import { readinessUnconfirmed, stateLabel, type StateDomain } from "../taskPresentation.js";

type Text = (key: MessageKey) => string;
export function TaskState({ domain, value, text, plain = false }: { domain: StateDomain; value: string; text: Text; plain?: boolean }): ReactElement {
  return <span className="task-state" data-domain={domain} data-value={value}>
    {plain ? <span>{text(stateLabel(domain, value))}</span> : <><code>{value}</code> — <span>{text(stateLabel(domain, value))}</span></>}
  </span>;
}

type Props = {
  api: WorkApi;
  task: TrackerTask;
  tasks: readonly TrackerTask[];
  runs: readonly TrackerRun[];
  sourceRevision: string | null;
  locale: Locale;
  text: Text;
  actionsCurrent: boolean;
  writesEnabled: boolean;
  onSelectTask: (id: string | null) => void;
  onLaunchTask: (id: string) => void;
  children?: ReactNode;
};

type DetailPresentation = { current: TaskDetailState; lastReady: TaskDetail | null };

// A tracker refresh invalidates currentness, not the selected record's DOM lifetime.
// Keeping the same task's last details mounted preserves native disclosure/focus state.
export function updateDetailPresentation(previous: DetailPresentation, current: TaskDetailState): DetailPresentation {
  return { current, lastReady: current.kind === "ready" ? current.detail
    : previous.current.taskId === current.taskId ? previous.lastReady : null };
}

export function TaskInspector(props: Props): ReactElement {
  const { api, task, sourceRevision } = props;
  const [presentation, setPresentation] = useState<DetailPresentation>({ current: { kind: "loading", taskId: task.taskId }, lastReady: null });
  const state = presentation.current;
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    void loadTaskDetailState(api, task.taskId, controller.signal, (incoming) => {
      setPresentation((previous) => updateDetailPresentation(previous, incoming));
    });
    return () => controller.abort();
  }, [api, task.taskId, sourceRevision, attempt]);
  // Even before effect cleanup, never render the previous identity's details.
  const selectedState: TaskDetailState = state.taskId === task.taskId
    ? state : { kind: "loading", taskId: task.taskId };
  return <TaskInspectorContent {...props} headerActions={<><OutputsButton scope={{ kind: "task", id: task.taskId }} title={task.title || task.taskId} eager /><ConversationButton scope={{ kind: "task", id: task.taskId }} title={task.title || task.taskId} /></>} detailState={selectedState} cachedDetail={presentation.lastReady?.taskId === task.taskId ? presentation.lastReady : null} onRefresh={() => setAttempt((value) => value + 1)} />;
}

export function TaskInspectorContent({ task, tasks, runs, locale, text, actionsCurrent, writesEnabled,
  onSelectTask, onLaunchTask, children, detailState, cachedDetail = null, headerActions = null, onRefresh }: Omit<Props, "api"> & {
    detailState: TaskDetailState;
    cachedDetail?: TaskDetail | null;
    headerActions?: ReactNode;
    onRefresh: () => void;
  }): ReactElement {
  const detailsCurrent = detailState.kind === "ready" && detailState.taskId === task.taskId;
  const detail = detailsCurrent ? detailState.detail : cachedDetail?.taskId === task.taskId ? cachedDetail : null;
  const taskRuns = runs.filter((run) => run.taskId === task.taskId);
  const canPrepare = task.nextAction === "start_ready_work" || task.nextAction === "retry_new_run";
  const taskName = (id: string): string => tasks.find((item) => item.taskId === id)?.title || id;
  const date = (value: string | null): string => value === null ? "—"
    : new Intl.DateTimeFormat(locale === "ru" ? "ru-RU" : "en-US", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
  return <section className="task-inspector" tabIndex={-1} aria-labelledby="inspector-title">
    <header className="inspector-header">
      <h2 id="inspector-title">{detail?.title || task.title || task.taskId}</h2>
      {headerActions}
      <button className="button button-secondary button-inline" type="button" onClick={() => onSelectTask(null)}>{text("inspector_close")}</button>
    </header>
    <div className="inspector-section inspector-decision">
      <p className="task-primary-state"><span className="task-domain-label">{text("tracker_task_state_label")}: </span><TaskState domain="task" value={task.taskState} text={text} plain /></p>
      <dl className="inspector-facts">
        {task.taskState !== "ready" || task.readiness !== "ready" ? <div><dt>{text("tracker_readiness_label")}</dt><dd><TaskState domain="readiness" value={task.readiness} text={text} plain /></dd></div> : null}
        <div><dt>{text("inspector_responsible")}</dt><dd>{task.roleName ?? text("inspector_unassigned")}</dd></div>
      </dl>
      {readinessUnconfirmed(task) ? <p className="inspector-notice" role="status">{text("inspector_readiness_unknown")}</p> : null}
      {task.blockingDependencyIds.length > 0 ? <div className="inspector-notice">
        <h3>{text("inspector_blockers")}</h3>
        <ul>{task.blockingDependencyIds.map((id) => <li key={id}><button type="button" className="task-reference" onClick={() => onSelectTask(id)}>{taskName(id)}</button></li>)}</ul>
      </div> : null}
      {task.freshness !== "fresh" ? <p className="inspector-notice" role="status"><span className="task-domain-label">{text("tracker_freshness")}: </span><TaskState domain="freshness" value={task.freshness} text={text} plain /></p> : null}
      <div className="inspector-next-action">
        <h3>{text("inspector_next")}</h3>
        <TaskState domain="action" value={task.nextAction} text={text} plain />
        {!actionsCurrent ? <p className="inspector-notice" role="status">{text(writesEnabled ? "inspector_actions_stale" : "inspector_actions_readonly")}</p> : null}
        {canPrepare ? <button className="button button-primary" type="button" disabled={!actionsCurrent || task.freshness !== "fresh" || readinessUnconfirmed(task)} onClick={() => onLaunchTask(task.taskId)}>{text("inspector_prepare_run")}</button> : null}
        {children}
      </div>
    </div>

    {detailState.kind === "loading" ? <p className="inspector-section" role="status" aria-live="polite">{text("inspector_detail_loading")}</p>
      : !detailsCurrent ? <div className="inspector-section inspector-notice" role="alert">
        <p>{text("inspector_detail_unavailable")}</p>
        <button className="button button-secondary" type="button" onClick={onRefresh}>{text("inspector_detail_retry")}</button>
      </div> : null}
    {!detailsCurrent && detail !== null ? <p className="inspector-notice" role="status">{text("inspector_detail_cached")}</p> : null}
    {detail === null ? null : <>
        {detail.truncated ? <p className="inspector-section inspector-notice" role="status">{text("inspector_partial")}</p> : null}
        <details key={`${task.taskId}:goal`} className="inspector-section inspector-disclosure" open><summary><h3>{text("inspector_goal")}</h3></summary><p className="inspector-prose">{detail.description}</p>
          <h3>{text("inspector_criteria")}</h3><ul className="inspector-criteria">{detail.acceptanceCriteria.map((item, index) => <li key={index}>{item}</li>)}</ul>
        </details>
        {detail.summary !== null ? <details key={`${task.taskId}:summary`} className="inspector-section inspector-disclosure" open={detail.summary !== null}><summary><h3>{text("inspector_summary")}</h3></summary><p className="inspector-prose">{detail.summary}</p></details> : null}
      </>}

    <section className="inspector-section inspector-evidence-state"><h3>{text("inspector_evidence")}</h3>
      <TaskState domain="evidence" value={task.evidenceState} text={text} plain />
      <p className="small-copy">{text("inspector_evidence_help")}</p>
      {detail === null ? null : detail.evidenceRefs.length === 0 ? <p className="small-copy">{text("inspector_no_evidence")}</p>
        : <ul>{detail.evidenceRefs.map((ref) => <li key={`${ref.id}@${ref.revision}`}><code>{ref.id} @ {ref.revision}</code></li>)}</ul>}
    </section>
    <details key={`${task.taskId}:runs`} className="inspector-section inspector-disclosure" open={taskRuns.length > 0}><summary><h3>{text("inspector_runs")}</h3></summary>
      {taskRuns.length === 0 ? <p>{text("inspector_no_runs")}</p> : <ol className="inspector-run-list">
        {taskRuns.map((run) => <li key={run.delegationId}>
          <strong>{run.roleName ?? text("inspector_unassigned")}</strong>
          <TaskState domain="run" value={run.phase} text={text} plain />
          <p className="small-copy">{run.provider ?? "—"} · {date(run.finishedAt ?? run.updatedAt ?? run.startedAt)}</p>
          {/* The recorded limit only. A run whose delegation carries none says so. */}
          <p className="small-copy inspector-run-limit">{`${text("run_limit_label")}: ${attemptLimitText(run.wallTimeSeconds, text)}`}</p>
          <details key={`${task.taskId}:${run.delegationId}`} className="inspector-technical"><summary>{text("inspector_run_details")}</summary>
            <dl><div><dt>{text("tracker_attempt_label")}</dt><dd><code>{run.attemptId ?? "—"}</code></dd></div>
              <div><dt>{text("tracker_profile_label")}</dt><dd><code>{run.profileId ?? "—"}</code></dd></div>
              <div><dt>{text("tracker_started_at")}</dt><dd>{date(run.startedAt)}</dd></div>
              <div><dt>{text("tracker_finished_at")}</dt><dd>{date(run.finishedAt)}</dd></div>
              <div><dt>{text("tracker_evidence_label")}</dt><dd><TaskState domain="evidence" value={run.evidenceState} text={text} /></dd></div>
              <div><dt>{text("tracker_freshness")}</dt><dd><TaskState domain="freshness" value={run.freshness} text={text} /></dd></div>
            </dl>
          </details>
        </li>)}
      </ol>}
      <p className="small-copy">{text("inspector_launch_attachments")}</p>
    </details>
    <details key={`${task.taskId}:technical`} className="inspector-section inspector-technical"><summary>{text("inspector_technical")}</summary>
      <dl><div><dt>{text("tracker_task_state_label")}</dt><dd><TaskState domain="task" value={task.taskState} text={text} /></dd></div>
        <div><dt>{text("tracker_readiness_label")}</dt><dd><TaskState domain="readiness" value={task.readiness} text={text} /></dd></div>
        <div><dt>{text("inspector_next")}</dt><dd><TaskState domain="action" value={task.nextAction} text={text} /></dd></div>
        <div><dt>{text("tracker_task_label")}</dt><dd><code>{task.taskId}</code></dd></div>
        <div><dt>{text("inspector_revision")}</dt><dd><code>{detail?.revision ?? "—"}</code></dd></div>
        <div><dt>{text("tracker_provider_label")}</dt><dd><code>{task.provider ?? "—"}</code></dd></div>
        <div><dt>{text("tracker_profile_label")}</dt><dd><code>{task.profileId ?? "—"}</code></dd></div>
        <div><dt>{text("tracker_freshness")}</dt><dd><TaskState domain="freshness" value={task.freshness} text={text} /></dd></div>
        <div><dt>{text("inspector_dependencies")}</dt><dd>{task.dependencyTaskIds.length === 0 ? text("tracker_no_dependencies") : <ul>{task.dependencyTaskIds.map((id) => <li key={id}><button type="button" className="task-reference" onClick={() => onSelectTask(id)}>{taskName(id)}</button> <code>{id}</code></li>)}</ul>}</dd></div>
        <div><dt>{text("tracker_gaps_label")}</dt><dd>{task.gaps.length === 0 ? "—" : task.gaps.map((gap) => <code key={gap}>{gap} </code>)}</dd></div>
      </dl><p className="small-copy">{text("inspector_observed")}</p>
    </details>
  </section>;
}
