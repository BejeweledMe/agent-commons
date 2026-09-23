import { ConversationButton } from "./ConversationPanel.js";
import { DecisionCard, TaskState } from "./DecisionCard.js";
import { OutputsButton } from "./OutputsPanel.js";
import { StopReasonNotice } from "./StopReasonNotice.js";
import { type ReactElement, type ReactNode, useEffect, useState } from "react";
import type { WorkApi } from "../api";
import type { TaskDetail, TrackerRun, TrackerTask } from "../contracts";
import type { Locale, MessageKey } from "../i18n";
import { attemptLimitText } from "../launchIntentState.js";
import { loadTaskDetailState, type TaskDetailState } from "../trackerState.js";

type Text = (key: MessageKey) => string;
// The state pair lives with the card that owns the decision; the inspector and
// the tracker keep importing it from here.
export { TaskState };

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
  /** Either static content or a function of what the inspector rendered: the
   *  detail revision the decision card shows, or null while it is unknown. */
  children?: ReactNode | ((rendered: RenderedTask) => ReactNode);
};
export type RenderedTask = Readonly<{ revision: string | null }>;

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
  return <TaskInspectorContent {...props}
    headerActions={<><OutputsButton scope={{ kind: "task", id: task.taskId }} title={task.title || task.taskId} eager /><ConversationButton scope={{ kind: "task", id: task.taskId }} title={task.title || task.taskId} /></>}
    answerControl={<ConversationButton scope={{ kind: "task", id: task.taskId }} title={task.title || task.taskId} variant="primary" label={props.text("decision_answer_worker")} />}
    detailState={selectedState} cachedDetail={presentation.lastReady?.taskId === task.taskId ? presentation.lastReady : null} onRefresh={() => setAttempt((value) => value + 1)} />;
}

export function TaskInspectorContent({ task, tasks, runs, locale, text, actionsCurrent, writesEnabled,
  onSelectTask, onLaunchTask, children, detailState, cachedDetail = null, headerActions = null, answerControl = null, onRefresh }: Omit<Props, "api"> & {
    detailState: TaskDetailState;
    cachedDetail?: TaskDetail | null;
    headerActions?: ReactNode;
    answerControl?: ReactNode;
    onRefresh: () => void;
  }): ReactElement {
  const detailsCurrent = detailState.kind === "ready" && detailState.taskId === task.taskId;
  const detail = detailsCurrent ? detailState.detail : cachedDetail?.taskId === task.taskId ? cachedDetail : null;
  const taskRuns = runs.filter((run) => run.taskId === task.taskId);
  // The action panel is given the very revision this render shows, so an
  // accept or return can only ever target what the person looked at.
  const rendered: RenderedTask = { revision: detail?.revision ?? null };
  const content = typeof children === "function" ? children(rendered) : children;
  const date = (value: string | null): string => value === null ? "—"
    : new Intl.DateTimeFormat(locale === "ru" ? "ru-RU" : "en-US", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
  return <section className="task-inspector" tabIndex={-1} aria-labelledby="inspector-title">
    <header className="inspector-header">
      <h2 id="inspector-title">{detail?.title || task.title || task.taskId}</h2>
      {headerActions}
      <button className="button button-secondary button-inline" type="button" onClick={() => onSelectTask(null)}>{text("inspector_close")}</button>
    </header>
    <DecisionCard task={task} tasks={tasks} runs={taskRuns} detail={detail} locale={locale} text={text}
      actionsCurrent={actionsCurrent} writesEnabled={writesEnabled} onSelectTask={onSelectTask}
      onLaunchTask={onLaunchTask} answerControl={answerControl}
      refreshControl={<button className="button button-secondary" type="button" onClick={onRefresh}>{text("inspector_detail_retry")}</button>}>{content}</DecisionCard>

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
          {/* Why this run stopped, in the server's closed terms. The history
              states the action; the decision card above carries the control. */}
          {run.stopReason ? <StopReasonNotice reason={run.stopReason} text={text} /> : null}
          <details key={`${task.taskId}:${run.delegationId}`} className="inspector-technical"><summary>{text("inspector_run_details")}</summary>
            <dl><div><dt>{text("tracker_attempt_label")}</dt><dd><code>{run.attemptId ?? "—"}</code></dd></div>
              <div><dt>{text("tracker_profile_label")}</dt><dd><code>{run.profileId ?? "—"}</code></dd></div>
              <div><dt>{text("tracker_started_at")}</dt><dd>{date(run.startedAt)}</dd></div>
              <div><dt>{text("tracker_finished_at")}</dt><dd>{date(run.finishedAt)}</dd></div>
              <div><dt>{text("tracker_evidence_label")}</dt><dd><TaskState domain="evidence" value={run.evidenceState} text={text} /></dd></div>
              <div><dt>{text("tracker_freshness")}</dt><dd><TaskState domain="freshness" value={run.freshness} text={text} /></dd></div>
            </dl>
            {/* The bounded provider diagnostic, when the server sent one: a
                closed disclosure inside the technical details, never the first layer. */}
            {run.providerDiagnostic ? <details className="run-diagnostic"><summary>{text("inspector_run_diagnostic")}</summary>
              <p className="small-copy">{text("inspector_run_diagnostic_help")}</p>
              <pre className="run-diagnostic-text">{run.providerDiagnostic}</pre>
            </details> : null}
          </details>
        </li>)}
      </ol>}
      <p className="small-copy">{text("inspector_launch_attachments")}</p>
    </details>
  </section>;
}
