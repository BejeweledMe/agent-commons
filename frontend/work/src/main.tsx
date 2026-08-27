import { type FormEvent, type ReactElement, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";

import { ApiProblem, WorkApi } from "./api";
import { AppHeader } from "./components/AppHeader";
import { FailurePanel } from "./components/FailurePanel";
import { WorkflowCard } from "./components/WorkflowCard";
import type { Failure, WorkspaceData } from "./contracts";
import { type Locale, type MessageKey, translate } from "./i18n";
import "./styles.css";

type AppState =
  | { kind: "checking" }
  | { kind: "ready"; data: WorkspaceData; notice: MessageKey | null }
  | { kind: "failure"; failure: Failure };

type RoleDraft = {
  name: string;
  profileId: string;
  rationale: string;
  contextMode: string;
};

type TaskDraft = {
  title: string;
  description: string;
  criteria: string;
};

type RunDraft = { agentId: string; taskId: string };
type FormErrors = ReadonlySet<string>;

const emptyRole: RoleDraft = { name: "", profileId: "", rationale: "", contextMode: "fresh" };
const emptyTask: TaskDraft = { title: "", description: "", criteria: "" };
const emptyRun: RunDraft = { agentId: "", taskId: "" };

function failureFrom(error: unknown, text: (key: MessageKey) => string): Failure {
  const problem = error instanceof ApiProblem ? error : null;
  const code = problem?.apiError?.code ?? (problem?.status === 401 ? "unauthorized" : "request_unavailable");
  const safeNextActions = problem?.apiError?.safeNextActions ?? [];
  if (code === "unauthorized" || problem?.status === 401 || problem?.status === 404) {
    return {
      code,
      title: text("failure_access_title"),
      nextStep: text("failure_access_next"),
      canRetry: false,
      safeNextActions
    };
  }
  if (code.startsWith("setup_")) {
    return {
      code,
      title: text("failure_setup_title"),
      nextStep: text("failure_setup_next"),
      canRetry: true,
      safeNextActions
    };
  }
  if (code === "launch_not_configured") {
    return {
      code,
      title: text("failure_launch_title"),
      nextStep: text("failure_launch_next"),
      canRetry: true,
      safeNextActions
    };
  }
  if (code === "invalid_request" || code === "ValidationError") {
    return {
      code,
      title: text("failure_validation_title"),
      nextStep: text("failure_validation_next"),
      canRetry: true,
      safeNextActions
    };
  }
  const unavailable = problem !== null && problem.status >= 500;
  return {
    code,
    title: text(unavailable ? "failure_unavailable_title" : "failure_unknown_title"),
    nextStep: text(unavailable ? "failure_unavailable_next" : "failure_unknown_next"),
    canRetry: true,
    safeNextActions
  };
}

function setupLabel(state: string, text: (key: MessageKey) => string): string {
  const keys: Readonly<Record<string, MessageKey>> = {
    setup_not_a_repository: "setup_not_repository",
    setup_uninitialized: "setup_uninitialized",
    setup_unconfigured: "setup_unconfigured",
    setup_configured: "setup_configured"
  };
  return text(keys[state] ?? "not_configured");
}

function validation(errors: readonly string[], field: string): boolean {
  return errors.includes(field);
}

function repositoryBasename(path: string): string {
  const segments = path.split(/[\\/]/).filter(Boolean);
  return segments.at(-1) ?? path;
}

function WorkApp(): ReactElement {
  const [locale, setLocale] = useState<Locale>("en");
  const [state, setState] = useState<AppState>({ kind: "checking" });
  const [activeAction, setActiveAction] = useState<string | null>(null);
  const [role, setRole] = useState<RoleDraft>(emptyRole);
  const [task, setTask] = useState<TaskDraft>(emptyTask);
  const [run, setRun] = useState<RunDraft>(emptyRun);
  const [roleErrors, setRoleErrors] = useState<FormErrors>(new Set());
  const [taskErrors, setTaskErrors] = useState<FormErrors>(new Set());
  const [runErrors, setRunErrors] = useState<FormErrors>(new Set());
  const [showFullProjectPath, setShowFullProjectPath] = useState(false);
  const apiRef = useRef(new WorkApi());
  const text = useMemo(() => (key: MessageKey) => translate(locale, key), [locale]);

  async function load(signal: AbortSignal): Promise<void> {
    setState({ kind: "checking" });
    try {
      await apiRef.current.connect(signal);
      const data = await apiRef.current.load(signal);
      setState({ kind: "ready", data, notice: null });
    } catch (error: unknown) {
      if (error instanceof DOMException && error.name === "AbortError") {
        return;
      }
      setState({ kind: "failure", failure: failureFrom(error, text) });
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, []);

  async function refresh(): Promise<void> {
    const controller = new AbortController();
    await load(controller.signal);
  }

  async function perform(
    action: string,
    work: (signal: AbortSignal) => Promise<void>,
    notice: MessageKey
  ): Promise<boolean> {
    setActiveAction(action);
    const controller = new AbortController();
    try {
      await work(controller.signal);
      const data = await apiRef.current.load(controller.signal);
      setState({ kind: "ready", data, notice });
      return true;
    } catch (error: unknown) {
      if (!(error instanceof DOMException && error.name === "AbortError")) {
        setState({ kind: "failure", failure: failureFrom(error, text) });
      }
      return false;
    } finally {
      setActiveAction(null);
    }
  }

  function submitRole(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    const errors = [
      ...(role.name.trim() ? [] : ["name"]),
      ...(role.profileId ? [] : ["profile"]),
      ...(role.rationale.trim() ? [] : ["rationale"])
    ];
    setRoleErrors(new Set(errors));
    if (errors.length > 0) {
      return;
    }
    void perform(
      "create-role",
      (signal) => apiRef.current.createRole(
        { ...role, name: role.name.trim(), rationale: role.rationale.trim() },
        signal
      ),
      "created_role"
    ).then((succeeded) => {
      if (succeeded) {
        setRole(emptyRole);
      }
    });
  }

  function submitTask(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    const criteria = task.criteria.split("\n").map((item) => item.trim()).filter(Boolean);
    const errors = [
      ...(task.title.trim() ? [] : ["title"]),
      ...(task.description.trim() ? [] : ["description"]),
      ...(criteria.length > 0 ? [] : ["criteria"])
    ];
    setTaskErrors(new Set(errors));
    if (errors.length > 0) {
      return;
    }
    void perform(
      "create-task",
      (signal) => apiRef.current.createTask(
        { title: task.title.trim(), description: task.description.trim(), criteria },
        signal
      ),
      "created_task"
    ).then((succeeded) => {
      if (succeeded) {
        setTask(emptyTask);
      }
    });
  }

  function submitRun(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    const errors = [...(run.agentId ? [] : ["agent"]), ...(run.taskId ? [] : ["task"])];
    setRunErrors(new Set(errors));
    if (errors.length > 0) {
      return;
    }
    void perform("start-run", (signal) => apiRef.current.startRun(run, signal), "run_started");
  }

  if (state.kind === "checking") {
    return (
      <main className="work-app work-app-centered">
        <section className="loading-panel" aria-live="polite" role="status">
          <h1>{text("checking_access")}</h1>
        </section>
      </main>
    );
  }

  if (state.kind === "failure") {
    return (
      <main className="work-app work-app-centered">
        <FailurePanel failure={state.failure} onRetry={() => void refresh()} text={text} />
      </main>
    );
  }

  const { data, notice } = state;
  const configured = data.setup.state === "setup_configured";
  const catalog = data.catalog;
  const launch = data.launch;
  const profileOptions = catalog?.profiles ?? [];
  const roleOptions = launch?.roles ?? [];
  const taskOptions = launch?.tasks ?? [];
  const environmentReady = configured && Boolean(launch?.launchEnabled);

  return (
    <main className="work-app">
      <AppHeader locale={locale} onLocaleChange={setLocale} text={text} />
      <div className="work-layout">
        <aside className="progress-panel" aria-label={text("current_status")}>
          <p className="eyebrow">{text("current_status")}</p>
          <p className="project-label">{text("project_label")}</p>
          <p className="project-name">{showFullProjectPath ? data.meta.repo : repositoryBasename(data.meta.repo)}</p>
          <button
            className="project-path-toggle"
            onClick={() => setShowFullProjectPath((visible) => !visible)}
            type="button"
          >
            {text(showFullProjectPath ? "hide_full_project_path" : "show_full_project_path")}
          </button>
          <p className="small-copy">{text("project_scope_help")}</p>
          <p className="status-title">{setupLabel(data.setup.state, text)}</p>
          <p>{environmentReady ? text("configured") : text("not_configured")}</p>
          <button className="button button-secondary" disabled={activeAction !== null} onClick={() => void refresh()} type="button">
            {text("refresh_status")}
          </button>
          <a className="legacy-link" href="/">{text("open_legacy_panel")}</a>
          <p className="small-copy">{text("legacy_panel_help")}</p>
        </aside>
        <div className="workflow-list">
          {notice !== null ? <p className="notice" role="status">{text(notice)}</p> : null}
          <WorkflowCard ready={environmentReady} title={text("step_environment")}>
            <p>{configured ? text("workspace_ready") : text("workspace_needs_setup")}</p>
            <p className="small-copy">{configured ? text("runtime_help") : text("environment_help")}</p>
            {!configured ? (
              <div className="button-row">
                {data.setup.state === "setup_uninitialized" ? (
                  <button
                    className="button button-primary"
                    disabled={activeAction !== null}
                    onClick={() => void perform("initialize", (signal) => apiRef.current.setup("initialize", signal), "action_complete")}
                    type="button"
                  >
                    {activeAction === "initialize" ? text("working") : text("initialize_workspace")}
                  </button>
                ) : null}
                {data.setup.state !== "setup_not_a_repository" && data.setup.state !== "setup_configured" ? (
                  <button
                    className="button button-primary"
                    disabled={activeAction !== null || data.setup.state === "setup_uninitialized"}
                    onClick={() => void perform("runtime", (signal) => apiRef.current.setup("runtime", signal), "action_complete")}
                    type="button"
                  >
                    {activeAction === "runtime" ? text("working") : text("configure_runtime")}
                  </button>
                ) : null}
              </div>
            ) : null}
          </WorkflowCard>
          <WorkflowCard ready={roleOptions.length > 0} title={text("step_role")}>
            <p className="small-copy">{text("role_help")}</p>
            <form noValidate onSubmit={submitRole}>
              <fieldset disabled={!configured || activeAction !== null}>
                <label htmlFor="role-name">{text("role_name")}</label>
                <input
                  aria-describedby={validation([...roleErrors], "name") ? "role-name-error" : undefined}
                  aria-invalid={validation([...roleErrors], "name")}
                  id="role-name"
                  onChange={(event) => setRole({ ...role, name: event.target.value })}
                  placeholder={text("role_name_placeholder")}
                  value={role.name}
                />
                {validation([...roleErrors], "name") ? <p className="field-error" id="role-name-error">{text("form_error_role_name")}</p> : null}
                <label htmlFor="role-profile">{text("role_profile")}</label>
                <select
                  aria-describedby={validation([...roleErrors], "profile") ? "role-profile-error" : undefined}
                  aria-invalid={validation([...roleErrors], "profile")}
                  id="role-profile"
                  onChange={(event) => setRole({ ...role, profileId: event.target.value })}
                  value={role.profileId}
                >
                  <option value="">{text("select_profile")}</option>
                  {profileOptions.map((profile) => <option key={profile.id} value={profile.id}>{profile.label}</option>)}
                </select>
                {validation([...roleErrors], "profile") ? <p className="field-error" id="role-profile-error">{text("form_error_profile")}</p> : null}
                {profileOptions.length === 0 && configured ? <p className="field-error">{text("no_profiles")}</p> : null}
                <label htmlFor="role-rationale">{text("role_rationale")}</label>
                <textarea
                  aria-describedby={validation([...roleErrors], "rationale") ? "role-rationale-error" : undefined}
                  aria-invalid={validation([...roleErrors], "rationale")}
                  id="role-rationale"
                  onChange={(event) => setRole({ ...role, rationale: event.target.value })}
                  placeholder={text("role_rationale_placeholder")}
                  rows={3}
                  value={role.rationale}
                />
                {validation([...roleErrors], "rationale") ? <p className="field-error" id="role-rationale-error">{text("form_error_rationale")}</p> : null}
                <label htmlFor="context-mode">{text("context_mode")}</label>
                <select id="context-mode" onChange={(event) => setRole({ ...role, contextMode: event.target.value })} value={role.contextMode}>
                  <option value="fresh">{text("context_fresh")}</option>
                  <option value="accumulated">{text("context_accumulated")}</option>
                </select>
                <button className="button button-primary" type="submit">{activeAction === "create-role" ? text("working") : text("create_role")}</button>
              </fieldset>
            </form>
          </WorkflowCard>
          <WorkflowCard ready={taskOptions.length > 0} title={text("step_task")}>
            <p className="small-copy">{text("task_help")}</p>
            <form noValidate onSubmit={submitTask}>
              <fieldset disabled={!configured || activeAction !== null}>
                <label htmlFor="task-title">{text("task_title")}</label>
                <input aria-invalid={validation([...taskErrors], "title")} id="task-title" onChange={(event) => setTask({ ...task, title: event.target.value })} placeholder={text("task_title_placeholder")} value={task.title} />
                {validation([...taskErrors], "title") ? <p className="field-error">{text("form_error_task_title")}</p> : null}
                <label htmlFor="task-description">{text("task_description")}</label>
                <textarea aria-invalid={validation([...taskErrors], "description")} id="task-description" onChange={(event) => setTask({ ...task, description: event.target.value })} placeholder={text("task_description_placeholder")} rows={4} value={task.description} />
                {validation([...taskErrors], "description") ? <p className="field-error">{text("form_error_task_description")}</p> : null}
                <label htmlFor="task-criteria">{text("acceptance_criteria")}</label>
                <textarea aria-invalid={validation([...taskErrors], "criteria")} id="task-criteria" onChange={(event) => setTask({ ...task, criteria: event.target.value })} placeholder={text("acceptance_criteria_placeholder")} rows={3} value={task.criteria} />
                {validation([...taskErrors], "criteria") ? <p className="field-error">{text("form_error_task_criteria")}</p> : null}
                <button className="button button-primary" type="submit">{activeAction === "create-task" ? text("working") : text("create_task")}</button>
              </fieldset>
            </form>
          </WorkflowCard>
          <WorkflowCard ready={false} title={text("step_run")}>
            <p className="small-copy">{text("run_help")}</p>
            {notice === "run_started" ? (
              <div className="notice" role="status">
                <p>{text("run_started_help")}</p>
                <a className="notice-link" href="/">{text("review_in_legacy")}</a>
              </div>
            ) : null}
            <form noValidate onSubmit={submitRun}>
              <fieldset disabled={!environmentReady || activeAction !== null}>
                <label htmlFor="run-role">{text("select_role")}</label>
                <select aria-invalid={validation([...runErrors], "agent")} id="run-role" onChange={(event) => setRun({ ...run, agentId: event.target.value })} value={run.agentId}>
                  <option value="">{text("select_role")}</option>
                  {roleOptions.map((option) => <option key={option.id} value={option.id}>{option.name}</option>)}
                </select>
                {validation([...runErrors], "agent") ? <p className="field-error">{text("form_error_run_role")}</p> : null}
                {roleOptions.length === 0 && configured ? <p className="field-error">{text("no_roles")}</p> : null}
                <label htmlFor="run-task">{text("select_task")}</label>
                <select aria-invalid={validation([...runErrors], "task")} id="run-task" onChange={(event) => setRun({ ...run, taskId: event.target.value })} value={run.taskId}>
                  <option value="">{text("select_task")}</option>
                  {taskOptions.map((option) => <option key={option.id} value={option.id}>{option.title}</option>)}
                </select>
                {validation([...runErrors], "task") ? <p className="field-error">{text("form_error_run_task")}</p> : null}
                {taskOptions.length === 0 && configured ? <p className="field-error">{text("no_tasks")}</p> : null}
                <button className="button button-primary" type="submit">{activeAction === "start-run" ? text("working") : text("start_run")}</button>
              </fieldset>
            </form>
          </WorkflowCard>
        </div>
      </div>
    </main>
  );
}

const rootElement = document.getElementById("root");
if (rootElement === null) {
  throw new Error("work_root_missing");
}
createRoot(rootElement).render(<WorkApp />);
