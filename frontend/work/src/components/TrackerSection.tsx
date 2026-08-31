import {
  type KeyboardEvent,
  type ReactElement,
  useEffect,
  useMemo,
  useRef,
  useState
} from "react";

import { type WorkApi } from "../api";
import { type TrackerSnapshot, type TrackerTask } from "../contracts";
import { type Locale, type MessageKey } from "../i18n";
import {
  trackerLoadFailed,
  trackerLoadSucceeded,
  trackerStreamSucceeded,
  type TrackerViewState
} from "../trackerState";

type Props = {
  api: WorkApi;
  locale: Locale;
  text: (key: MessageKey) => string;
};

const stateGloss: Readonly<Record<string, MessageKey>> = {
  ready: "tracker_gloss_ready",
  blocked: "tracker_gloss_blocked",
  terminal_dependency_failure: "tracker_gloss_dependency_failure",
  policy_unknown: "tracker_gloss_policy_unknown",
  in_progress: "tracker_gloss_in_progress",
  human_attention: "tracker_gloss_human_attention",
  complete: "tracker_gloss_complete",
  completed: "tracker_gloss_complete",
  cancelled: "tracker_gloss_cancelled",
  assigned: "tracker_gloss_assigned",
  active: "tracker_gloss_active",
  review: "tracker_gloss_review",
  accepted: "tracker_gloss_accepted",
  unknown: "tracker_gloss_unknown",
  requested: "tracker_gloss_requested",
  reserved: "tracker_gloss_reserved",
  launching: "tracker_gloss_launching",
  running: "tracker_gloss_running",
  cancellation_requested: "tracker_gloss_cancellation_requested",
  input_needed: "tracker_gloss_input_needed",
  succeeded: "tracker_gloss_succeeded",
  failed: "tracker_gloss_failed",
  timed_out: "tracker_gloss_timed_out",
  needs_operator: "tracker_gloss_needs_operator",
  available: "tracker_gloss_available",
  saturated: "tracker_gloss_saturated",
  backpressure: "tracker_gloss_backpressure",
  fresh: "tracker_gloss_fresh",
  stale: "tracker_gloss_stale",
  partial: "tracker_gloss_partial",
  missing: "tracker_gloss_missing",
  wait_for_run: "tracker_gloss_wait_for_run",
  start_ready_work: "tracker_gloss_start_ready_work",
  resolve_dependencies: "tracker_gloss_resolve_dependencies",
  answer_operator_request: "tracker_gloss_answer_operator_request",
  inspect_failure: "tracker_gloss_inspect_failure",
  retry_new_run: "tracker_gloss_retry_new_run",
  request_review: "tracker_gloss_request_review",
  wait_for_review: "tracker_gloss_wait_for_review",
  revise_work: "tracker_gloss_revise_work",
  accept_task: "tracker_gloss_accept_task",
  inspect_missing_evidence: "tracker_gloss_inspect_missing_evidence",
  none: "tracker_gloss_none"
};

const gapGloss: Readonly<Record<string, MessageKey>> = {
  missing_review: "tracker_gap_missing_review",
  stale_review: "tracker_gap_stale_review",
  target_revision_mismatch: "tracker_gap_target_revision_mismatch",
  non_independent_review: "tracker_gap_non_independent_review",
  changes_requested: "tracker_gap_changes_requested",
  review_evidence_missing: "tracker_gap_review_evidence_missing",
  tracker_snapshot_too_large: "tracker_gap_stream",
  tracker_sequence_regressed: "tracker_gap_stream",
  tracker_sequence_reused: "tracker_gap_stream"
};

function CanonicalState({
  text,
  value
}: {
  text: (key: MessageKey) => string;
  value: string;
}): ReactElement {
  return (
    <span className="tracker-canonical">
      <code>{value}</code> — {text(gapGloss[value] ?? stateGloss[value] ?? "tracker_gap_incomplete")}
    </span>
  );
}

function CanonicalList({
  text,
  values
}: {
  text: (key: MessageKey) => string;
  values: readonly string[];
}): ReactElement {
  if (values.length === 0) {
    return <span>—</span>;
  }
  return (
    <ul className="tracker-code-list">
      {values.map((value) => <li key={value}><CanonicalState text={text} value={value} /></li>)}
    </ul>
  );
}

function formatTimestamp(value: string | null, locale: string): string {
  if (value === null) {
    return "—";
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString(locale);
}

function taskPosition(tasks: readonly TrackerTask[], taskId: string): number {
  return Math.max(0, tasks.findIndex((task) => task.taskId === taskId));
}

export function TrackerSection({ api, locale, text }: Props): ReactElement {
  const [state, setState] = useState<TrackerViewState>({ kind: "loading" });
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const taskButtons = useRef(new Map<string, HTMLButtonElement>());

  async function load(signal: AbortSignal): Promise<void> {
    setState({ kind: "loading" });
    try {
      const snapshot = await api.loadTracker(signal);
      setState((current) => trackerLoadSucceeded(current, snapshot));
    } catch (error: unknown) {
      if (!(error instanceof DOMException && error.name === "AbortError")) {
        setState(trackerLoadFailed);
      }
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    const close = api.openTrackerStream(
      (snapshot) => setState((current) => trackerStreamSucceeded(current, snapshot)),
      (connection) => setState((current) => (
        current.kind === "ready" ? { ...current, connection } : current
      )),
      () => setState((current) => (
        current.kind === "ready"
          ? { ...current, connection: "disconnected" }
          : { kind: "failure" }
      ))
    );
    return () => {
      controller.abort();
      close();
    };
  }, [api]);

  const tasks = state.kind === "ready" ? state.snapshot.tasks : [];
  const effectiveSelectedTaskId = useMemo(() => {
    if (selectedTaskId !== null && tasks.some((task) => task.taskId === selectedTaskId)) {
      return selectedTaskId;
    }
    if (state.kind !== "ready") {
      return null;
    }
    return state.snapshot.focusTaskIds.at(-1)
      ?? state.snapshot.criticalPathTaskIds.at(-1)
      ?? tasks[0]?.taskId
      ?? null;
  }, [selectedTaskId, state, tasks]);
  const selectedTask = tasks.find((task) => task.taskId === effectiveSelectedTaskId) ?? null;

  function moveTaskFocus(event: KeyboardEvent<HTMLButtonElement>, taskId: string): void {
    const keys = ["ArrowDown", "ArrowRight", "ArrowUp", "ArrowLeft", "Home", "End"];
    if (!keys.includes(event.key) || tasks.length === 0) {
      return;
    }
    event.preventDefault();
    const current = taskPosition(tasks, taskId);
    const next = event.key === "Home"
      ? 0
      : event.key === "End"
        ? tasks.length - 1
        : event.key === "ArrowDown" || event.key === "ArrowRight"
          ? (current + 1) % tasks.length
          : (current - 1 + tasks.length) % tasks.length;
    const nextId = tasks[next].taskId;
    setSelectedTaskId(nextId);
    taskButtons.current.get(nextId)?.focus();
  }

  if (state.kind === "loading") {
    return (
      <section aria-labelledby="tracker-title" className="tracker-section">
        <h2 id="tracker-title">{text("tracker_title")}</h2>
        <p aria-live="polite" role="status">{text("tracker_loading")}</p>
      </section>
    );
  }

  if (state.kind === "failure") {
    return (
      <section aria-labelledby="tracker-title" className="tracker-section">
        <h2 id="tracker-title">{text("tracker_title")}</h2>
        <div className="tracker-state tracker-state-error" role="alert">
          <h3>{text("tracker_error_title")}</h3>
          <p>{text("tracker_error_next")}</p>
          <button
            className="button button-secondary button-inline"
            onClick={() => void load(new AbortController().signal)}
            type="button"
          >
            {text("tracker_retry")}
          </button>
        </div>
      </section>
    );
  }

  const { snapshot } = state;
  if (snapshot.state === "loading") {
    return (
      <section aria-labelledby="tracker-title" className="tracker-section">
        <h2 id="tracker-title">{text("tracker_title")}</h2>
        <p aria-live="polite" role="status">{text("tracker_loading")}</p>
      </section>
    );
  }
  if (snapshot.state === "error") {
    return (
      <section aria-labelledby="tracker-title" className="tracker-section">
        <h2 id="tracker-title">{text("tracker_title")}</h2>
        <div className="tracker-state tracker-state-error" role="alert">
          <h3>{text("tracker_projection_error_title")}</h3>
          <p>{text("tracker_projection_error_next")}</p>
          <CanonicalList text={text} values={snapshot.gaps} />
        </div>
      </section>
    );
  }

  if (snapshot.state === "empty") {
    return (
      <section aria-labelledby="tracker-title" className="tracker-section">
        <h2 id="tracker-title">{text("tracker_title")}</h2>
        <p>{text("tracker_empty")}</p>
      </section>
    );
  }

  const dateLocale = locale === "ru" ? "ru-RU" : "en-US";
  const stale = snapshot.state === "stale"
    || snapshot.freshness.state === "stale"
    || snapshot.freshness.resumeGap;

  return (
    <section aria-labelledby="tracker-title" className="tracker-section">
      <div className="tracker-header">
        <div>
          <p className="eyebrow">{text("tracker_eyebrow")}</p>
          <h2 id="tracker-title">{text("tracker_title")}</h2>
          <p className="small-copy">{text("tracker_intro")}</p>
        </div>
        <div className="tracker-connection" role="status">
          {text(state.connection === "connected"
            ? "tracker_updates_connected"
            : state.connection === "connecting"
              ? "tracker_updates_connecting"
              : "tracker_updates_disconnected")}
        </div>
      </div>

      {stale ? (
        <div className="tracker-state tracker-state-warning" role="status">
          <strong>{text("tracker_stale_title")}</strong>
          <p>{text(snapshot.freshness.resumeGap ? "tracker_resume_gap" : "tracker_stale_next")}</p>
        </div>
      ) : null}
      {snapshot.state === "partial" ? (
        <div className="tracker-state tracker-state-warning" role="status">
          <strong>{text("tracker_partial_title")}</strong>
          <p>{text("tracker_partial_next")}</p>
          <CanonicalList text={text} values={snapshot.gaps} />
        </div>
      ) : null}

      <dl className="tracker-summary">
        <div><dt>{text("tracker_snapshot_time")}</dt><dd>{formatTimestamp(snapshot.freshness.generatedAt, dateLocale)}</dd></div>
        <div><dt>{text("tracker_source_time")}</dt><dd>{formatTimestamp(snapshot.freshness.sourceUpdatedAt, dateLocale)}</dd></div>
        <div><dt>{text("tracker_freshness")}</dt><dd><CanonicalState text={text} value={snapshot.freshness.state} /></dd></div>
        <div><dt>{text("tracker_capacity")}</dt><dd><CanonicalState text={text} value={snapshot.capacity.state} /></dd></div>
      </dl>
      {snapshot.capacity.active !== null ? (
        <p className="small-copy">
          {text("tracker_capacity_detail")}
          {` ${snapshot.capacity.active}/${snapshot.capacity.limit ?? "—"}; `}
          {text("tracker_queue_detail")}
          {` ${snapshot.capacity.queued ?? "—"}/${snapshot.capacity.queueCapacity ?? "—"}.`}
        </p>
      ) : null}

      {snapshot.attention.length > 0 ? (
        <aside aria-labelledby="tracker-attention-title" className="tracker-attention">
          <h3 id="tracker-attention-title">{text("tracker_attention_title")}</h3>
          <ul>
            {snapshot.attention.map((item) => (
              <li key={`${item.kind}:${item.itemId}`}>
                <strong>{item.taskId ?? item.itemId}</strong>
                <CanonicalState text={text} value={item.reasonCode} />
                <CanonicalState text={text} value={item.nextAction} />
              </li>
            ))}
          </ul>
        </aside>
      ) : null}

      <div className="tracker-grid">
        <section aria-labelledby="tracker-dag-title" className="tracker-panel">
          <h3 id="tracker-dag-title">{text("tracker_dag_title")}</h3>
          <p className="small-copy" id="tracker-keyboard-help">{text("tracker_keyboard_help")}</p>
          <ul aria-describedby="tracker-keyboard-help" className="tracker-task-list">
            {tasks.map((task) => (
              <li key={task.taskId}>
                <button
                  aria-pressed={task.taskId === effectiveSelectedTaskId}
                  className={`tracker-task${task.awaitsHuman ? " tracker-task-attention" : ""}`}
                  onClick={() => setSelectedTaskId(task.taskId)}
                  onKeyDown={(event) => moveTaskFocus(event, task.taskId)}
                  ref={(element) => {
                    if (element === null) {
                      taskButtons.current.delete(task.taskId);
                    } else {
                      taskButtons.current.set(task.taskId, element);
                    }
                  }}
                  type="button"
                >
                  <span className="tracker-task-title">{task.title || task.taskId}</span>
                  <CanonicalState text={text} value={task.readiness} />
                </button>
              </li>
            ))}
          </ul>
          <h4>{text("tracker_dependencies_title")}</h4>
          {snapshot.edges.length === 0 ? <p className="small-copy">{text("tracker_no_dependencies")}</p> : (
            <ul className="tracker-edge-list">
              {snapshot.edges.map((edge) => (
                <li key={`${edge.prerequisiteTaskId}:${edge.dependentTaskId}`}>
                  <code>{edge.prerequisiteTaskId}</code>
                  <span aria-hidden="true"> → </span>
                  <code>{edge.dependentTaskId}</code>
                  {edge.prerequisiteMissing ? ` — ${text("tracker_dependency_missing")}` : ""}
                </li>
              ))}
            </ul>
          )}
        </section>

        <section aria-labelledby="tracker-task-detail-title" className="tracker-panel">
          <h3 id="tracker-task-detail-title">{text("tracker_task_detail_title")}</h3>
          {selectedTask === null ? <p>{text("tracker_select_task")}</p> : (
            <dl className="tracker-detail-list">
              <div><dt>{text("tracker_task_label")}</dt><dd>{selectedTask.title || selectedTask.taskId}</dd></div>
              <div><dt>{text("tracker_task_state_label")}</dt><dd><CanonicalState text={text} value={selectedTask.taskState} /></dd></div>
              <div><dt>{text("tracker_readiness_label")}</dt><dd><CanonicalState text={text} value={selectedTask.readiness} /></dd></div>
              <div><dt>{text("tracker_phase_label")}</dt><dd>{selectedTask.phase === null ? "—" : <CanonicalState text={text} value={selectedTask.phase} />}</dd></div>
              <div><dt>{text("tracker_role_label")}</dt><dd>{selectedTask.roleName ?? "—"}</dd></div>
              <div><dt>{text("tracker_provider_label")}</dt><dd><code>{selectedTask.provider ?? "—"}</code></dd></div>
              <div><dt>{text("tracker_profile_label")}</dt><dd><code>{selectedTask.profileId ?? "—"}</code></dd></div>
              <div><dt>{text("tracker_blocked_by_label")}</dt><dd>{selectedTask.blockingDependencyIds.length > 0 ? selectedTask.blockingDependencyIds.join(", ") : text("tracker_not_blocked")}</dd></div>
              <div><dt>{text("tracker_next_action_label")}</dt><dd><CanonicalState text={text} value={selectedTask.nextAction} /></dd></div>
              <div><dt>{text("tracker_evidence_label")}</dt><dd><CanonicalState text={text} value={selectedTask.evidenceState} /></dd></div>
              <div><dt>{text("tracker_gaps_label")}</dt><dd><CanonicalList text={text} values={selectedTask.gaps} /></dd></div>
            </dl>
          )}
          <p className="small-copy">{text("tracker_critical_path_note")}</p>
          <p className="tracker-technical">{snapshot.criticalPathTaskIds.join(" → ") || "—"}</p>
        </section>
      </div>

      <section aria-labelledby="tracker-runs-title" className="tracker-panel tracker-runs">
        <h3 id="tracker-runs-title">{text("tracker_runs_title")}</h3>
        {snapshot.runs.length === 0 ? <p>{text("tracker_runs_empty")}</p> : (
          <ol className="tracker-run-list">
            {snapshot.runs.map((run) => (
              <li key={run.delegationId}>
                <div className="tracker-run-heading">
                  <strong>{run.roleName ?? run.agentId ?? run.delegationId}</strong>
                  <CanonicalState text={text} value={run.phase} />
                </div>
                <dl className="tracker-run-meta">
                  <div><dt>{text("tracker_task_label")}</dt><dd>{run.taskId ?? "—"}</dd></div>
                  <div><dt>{text("tracker_provider_label")}</dt><dd><code>{run.provider ?? "—"}</code></dd></div>
                  <div><dt>{text("tracker_profile_label")}</dt><dd><code>{run.profileId ?? "—"}</code></dd></div>
                  <div><dt>{text("tracker_attempt_label")}</dt><dd><code>{run.attemptId ?? "—"}</code>{run.attemptNumber === null ? "" : ` #${run.attemptNumber}`}</dd></div>
                  <div><dt>{text("tracker_started_at")}</dt><dd>{formatTimestamp(run.startedAt, dateLocale)}</dd></div>
                  <div><dt>{text("tracker_updated_at")}</dt><dd>{formatTimestamp(run.updatedAt, dateLocale)}</dd></div>
                  <div><dt>{text("tracker_finished_at")}</dt><dd>{formatTimestamp(run.finishedAt, dateLocale)}</dd></div>
                  <div><dt>{text("tracker_duration")}</dt><dd>{run.durationSeconds === null ? "—" : `${run.durationSeconds} ${text("tracker_seconds")}`}</dd></div>
                  <div><dt>{text("tracker_freshness")}</dt><dd><CanonicalState text={text} value={run.freshness} /></dd></div>
                  <div><dt>{text("tracker_evidence_label")}</dt><dd><CanonicalState text={text} value={run.evidenceState} /></dd></div>
                  <div><dt>{text("tracker_next_action_label")}</dt><dd><CanonicalState text={text} value={run.nextAction} /></dd></div>
                </dl>
              </li>
            ))}
          </ol>
        )}
      </section>
    </section>
  );
}
