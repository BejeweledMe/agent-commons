import type { ReactElement, ReactNode } from "react";
import type { TaskDetail, TrackerRun, TrackerTask } from "../contracts";
import type { Locale, MessageKey } from "../i18n";
import { readinessUnconfirmed, stateLabel, type StateDomain } from "../taskPresentation.js";

type Text = (key: MessageKey) => string;

/** The raw canonical pair: the exact stored value beside its glossary reading. */
export function TaskState({ domain, value, text, plain = false }: { domain: StateDomain; value: string; text: Text; plain?: boolean }): ReactElement {
  return <span className="task-state" data-domain={domain} data-value={value}>
    {plain ? <span>{text(stateLabel(domain, value))}</span> : <><code>{value}</code> — <span>{text(stateLabel(domain, value))}</span></>}
  </span>;
}

/**
 * The one control the card offers for the observed next action. `prepare_run`
 * and `answer_worker` are rendered by the card itself; the three canonical
 * write actions are rendered by the action slot the tracker passes in, so
 * exactly one primary control is present for every next action.
 */
export type DecisionControl = "prepare_run" | "accept" | "return_for_revision" | "answer_worker" | "request_review" | null;
const CONTROLS: Readonly<Record<string, Exclude<DecisionControl, null>>> = {
  start_ready_work: "prepare_run", retry_new_run: "prepare_run", accept_task: "accept",
  revise_work: "return_for_revision", answer_operator_request: "answer_worker", request_review: "request_review"
};
export function decisionControl(nextAction: string): DecisionControl {
  return CONTROLS[nextAction] ?? null;
}
/** Controls the card owns; the rest belong to the tracker's action panel. */
const CARD_CONTROLS: readonly DecisionControl[] = ["prepare_run", "answer_worker"];
export function cardOwnsControl(nextAction: string): boolean {
  return CARD_CONTROLS.includes(decisionControl(nextAction));
}

/** Run phases that have not finished: such a run still has a worker on it. */
const LIVE_RUN_PHASES: readonly string[] = ["requested", "reserved", "launching", "running",
  "cancellation_requested", "input_needed"];
export function activeRun(runs: readonly TrackerRun[]): TrackerRun | null {
  return runs.find((run) => LIVE_RUN_PHASES.includes(run.phase)) ?? null;
}

/**
 * "What is happening" in one sentence. The task state carries the sentence;
 * readiness is added only when it says something the state does not, so a
 * plainly ready task is not told twice that it can be started.
 */
export function happeningText(task: TrackerTask, text: Text): string {
  const state = text(stateLabel("task", task.taskState));
  return task.taskState === "ready" && task.readiness === "ready" ? state
    : `${state} — ${text(stateLabel("readiness", task.readiness))}`;
}

/** "Who is responsible": the worker of a live run first, then the task's role. */
export function responsibleText(task: TrackerTask, runs: readonly TrackerRun[], text: Text): string {
  const live = activeRun(runs);
  if (live !== null && live.roleName !== null) return `${live.roleName} — ${text("decision_responsible_running")}`;
  if (task.roleName !== null) return `${task.roleName} — ${text("decision_responsible_owner")}`;
  return text("inspector_unassigned");
}

type Props = {
  task: TrackerTask;
  tasks: readonly TrackerTask[];
  runs: readonly TrackerRun[];
  detail: TaskDetail | null;
  locale: Locale;
  text: Text;
  actionsCurrent: boolean;
  writesEnabled: boolean;
  onSelectTask: (id: string | null) => void;
  onLaunchTask: (id: string) => void;
  /** The conversation entry used when the next action is answering a worker. */
  answerControl?: ReactNode;
  /** The tracker's canonical accept / return / request review panel. */
  children?: ReactNode;
};

export function DecisionCard({ task, tasks, runs, detail, locale, text, actionsCurrent, writesEnabled,
  onSelectTask, onLaunchTask, answerControl = null, children }: Props): ReactElement {
  const control = decisionControl(task.nextAction);
  const live = activeRun(runs);
  const findings = detail?.findings ?? null;
  const taskName = (id: string): string => tasks.find((item) => item.taskId === id)?.title || id;
  const date = (value: string | null): string => value === null ? "—"
    : new Intl.DateTimeFormat(locale === "ru" ? "ru-RU" : "en-US", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
  // Green marks accepted work and nothing else; every other state is neutral
  // text with a decorative icon, and warnings keep the existing amber tokens.
  const accepted = task.taskState === "accepted";
  const attention = task.taskState === "blocked" || task.awaitsHuman || readinessUnconfirmed(task);
  return <section className="inspector-section decision-card" data-accepted={accepted ? "true" : "false"}
    data-attention={attention ? "true" : "false"} aria-labelledby="decision-card-title">
    <h3 id="decision-card-title" className="decision-line decision-happening">
      <span className="decision-icon" aria-hidden="true">{accepted ? "✓" : attention ? "!" : "•"}</span>
      <span className="decision-label">{text("decision_happening")}: </span>
      <span className="decision-sentence">{happeningText(task, text)}</span>
    </h3>
    {readinessUnconfirmed(task) ? <p className="inspector-notice" role="status">{text("inspector_readiness_unknown")}</p> : null}
    {task.blockingDependencyIds.length > 0 ? <div className="inspector-notice">
      <h4>{text("inspector_blockers")}</h4>
      <ul>{task.blockingDependencyIds.map((id) => <li key={id}><button type="button" className="task-reference" onClick={() => onSelectTask(id)}>{taskName(id)}</button></li>)}</ul>
    </div> : null}
    {task.freshness !== "fresh" ? <p className="inspector-notice" role="status"><span className="task-domain-label">{text("tracker_freshness")}: </span><TaskState domain="freshness" value={task.freshness} text={text} plain /></p> : null}
    <p className="decision-line decision-responsible">
      <span className="decision-label">{text("decision_responsible")}: </span>
      <span className="decision-sentence">{responsibleText(task, runs, text)}</span>
    </p>
    <div className="decision-line decision-next">
      <p><span className="decision-label">{text("decision_next")}: </span>
        <span className="decision-sentence">{text(stateLabel("action", task.nextAction))}</span></p>
      {!actionsCurrent ? <p className="inspector-notice" role="status">{text(writesEnabled ? "inspector_actions_stale" : "inspector_actions_readonly")}</p> : null}
      {control === "prepare_run" ? <button className="button button-primary" type="button" disabled={!actionsCurrent || task.freshness !== "fresh" || readinessUnconfirmed(task)} onClick={() => onLaunchTask(task.taskId)}>{text("inspector_prepare_run")}</button> : null}
      {control === "answer_worker" ? <div className="decision-answer">{answerControl}</div> : null}
      {children}
    </div>
    {/* Findings are a verdict about this work: they stay in the open, and the
        count is stated even when it is zero. When the server sent no findings
        key at all the card says so instead of claiming there are none. */}
    <div className="decision-findings">
      <h4>{findings === null ? text("decision_findings") : `${text("decision_findings")} (${findings.length})`}</h4>
      {findings === null ? <p className="small-copy">{text("decision_findings_unavailable")}</p>
        : findings.length === 0 ? <p className="small-copy">{text("decision_no_findings")}</p>
          : <ul>{findings.map((finding) => <li key={finding.id}>{finding.title ?? finding.id} <code>{finding.id}</code></li>)}</ul>}
    </div>
    <details key={`${task.taskId}:decision-details`} className="decision-details inspector-technical"><summary>{text("decision_details")}</summary>
      <dl><div><dt>{text("tracker_task_state_label")}</dt><dd><TaskState domain="task" value={task.taskState} text={text} /></dd></div>
        <div><dt>{text("tracker_readiness_label")}</dt><dd><TaskState domain="readiness" value={task.readiness} text={text} /></dd></div>
        <div><dt>{text("inspector_next")}</dt><dd><TaskState domain="action" value={task.nextAction} text={text} /></dd></div>
        <div><dt>{text("inspector_responsible")}</dt><dd>{task.roleName ?? text("inspector_unassigned")}</dd></div>
        <div><dt>{text("tracker_task_label")}</dt><dd><code>{task.taskId}</code></dd></div>
        <div><dt>{text("inspector_revision")}</dt><dd><code>{detail?.revision ?? "—"}</code></dd></div>
        <div><dt>{text("decision_run_id")}</dt><dd><code>{live?.delegationId ?? "—"}</code></dd></div>
        <div><dt>{text("tracker_started_at")}</dt><dd>{date(live?.startedAt ?? null)}</dd></div>
        <div><dt>{text("tracker_updated_at")}</dt><dd>{date(live?.updatedAt ?? null)}</dd></div>
        <div><dt>{text("decision_objective")}</dt><dd><code>{task.objectiveId ?? "—"}</code></dd></div>
        <div><dt>{text("decision_application")}</dt><dd><code>{task.applicationId ?? "—"}</code></dd></div>
        <div><dt>{text("tracker_provider_label")}</dt><dd><code>{task.provider ?? "—"}</code></dd></div>
        <div><dt>{text("tracker_profile_label")}</dt><dd><code>{task.profileId ?? "—"}</code></dd></div>
        <div><dt>{text("tracker_freshness")}</dt><dd><TaskState domain="freshness" value={task.freshness} text={text} /></dd></div>
        <div><dt>{text("inspector_dependencies")}</dt><dd>{task.dependencyTaskIds.length === 0 ? text("tracker_no_dependencies") : <ul>{task.dependencyTaskIds.map((id) => <li key={id}><button type="button" className="task-reference" onClick={() => onSelectTask(id)}>{taskName(id)}</button> <code>{id}</code></li>)}</ul>}</dd></div>
        <div><dt>{text("tracker_gaps_label")}</dt><dd>{task.gaps.length === 0 ? "—" : task.gaps.map((gap) => <code key={gap}>{gap} </code>)}</dd></div>
      </dl><p className="small-copy">{text("inspector_observed")}</p>
    </details>
  </section>;
}
