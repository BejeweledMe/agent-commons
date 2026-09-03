import { type FormEvent, type ReactElement, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";

import { ApiProblem, WorkApi } from "./api";
import { AppHeader } from "./components/AppHeader";
import { FailurePanel } from "./components/FailurePanel";
import { ContextPacksSection } from "./components/ContextPacksSection";
import { StarterPacksSection } from "./components/StarterPacksSection";
import { TrackerSection } from "./components/TrackerSection";
import { WorkflowCard } from "./components/WorkflowCard";
import type {
  ContextPackOption,
  Failure,
  ProviderAvailabilityRefusalCode,
  ProviderCapabilityRefusalCode,
  ProviderAuthAction,
  ProviderAuthState,
  ProviderAuthStatus,
  SetupGuidanceNextActionKey,
  WorkspaceData
} from "./contracts";
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
  dependencyIds: readonly string[];
};

type RunDraft = { agentId: string; taskId: string; contextPackKey: string };
type FormErrors = ReadonlySet<string>;

const emptyRole: RoleDraft = { name: "", profileId: "", rationale: "", contextMode: "fresh" };
const emptyTask: TaskDraft = { title: "", description: "", criteria: "", dependencyIds: [] };
const emptyRun: RunDraft = { agentId: "", taskId: "", contextPackKey: "" };

function contextPackKey(option: ContextPackOption): string {
  return `${option.contextPackId}@${option.revision}`;
}

const guidanceActionMessage: Readonly<Record<SetupGuidanceNextActionKey, MessageKey>> = {
  choose_git_repository: "guidance_choose_repository",
  initialize_workspace: "guidance_initialize_workspace",
  install_provider_and_check_again: "guidance_install_provider",
  install_support_tool_and_check_again: "guidance_install_support_tool",
  configure_runtime: "guidance_configure_runtime",
  repair_workspace_configuration: "guidance_repair_configuration",
  setup_ready: "guidance_ready"
};

const providerAuthStateMessage: Readonly<Record<ProviderAuthState, MessageKey>> = {
  ready: "provider_auth_ready",
  authentication_required: "provider_auth_required",
  authenticating: "provider_auth_authenticating",
  timed_out: "provider_auth_timed_out",
  cancelled: "provider_auth_cancelled",
  failed: "provider_auth_failed",
  unsupported: "provider_auth_unsupported",
  credential_store_unavailable: "provider_auth_credential_store_unavailable"
};

const providerAuthActionMessage: Readonly<Record<ProviderAuthAction, MessageKey>> = {
  authenticate: "provider_auth_authenticate",
  cancel_authentication: "provider_auth_cancel",
  check_again: "provider_auth_check_again",
  continue_launch: "provider_auth_continue_launch"
};

const availabilityRefusalMessage: Readonly<Record<ProviderAvailabilityRefusalCode, MessageKey>> = {
  provider_installation_unavailable: "provider_availability_installation",
  provider_initialization_failed: "provider_availability_initialization",
  provider_qualification_required: "provider_availability_qualification_required",
  provider_qualification_failed: "provider_availability_qualification_failed",
  provider_authentication_required: "provider_availability_authentication_required",
  provider_authentication_unconfirmed: "provider_availability_authentication_unconfirmed"
};

const capabilityRefusalMessage: Readonly<Record<ProviderCapabilityRefusalCode, MessageKey>> = {
  provider_resume_unavailable: "provider_capability_resume_unavailable",
  provider_skill_projection_unavailable: "provider_capability_skills_unavailable",
  provider_monetary_budget_unavailable: "provider_capability_monetary_unavailable"
};

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
  const [configurationConfirmationOpen, setConfigurationConfirmationOpen] = useState(false);
  const [pendingLaunchKey, setPendingLaunchKey] = useState<string | null>(null);
  const apiRef = useRef(new WorkApi());
  const authPanelRef = useRef<HTMLElement | null>(null);
  const text = useMemo(() => (key: MessageKey) => translate(locale, key), [locale]);

  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

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

  useEffect(() => {
    if (state.kind !== "ready") {
      return;
    }
    const activeProfiles = state.data.providerAuth
      .filter((status) => status.state === "authenticating")
      .map((status) => status.profileId);
    if (activeProfiles.length === 0) {
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      void Promise.allSettled(
        activeProfiles.map((profileId) => apiRef.current.providerAuthStatus(profileId, controller.signal))
      ).then((results) => {
        setState((current) => {
          if (current.kind !== "ready") {
            return current;
          }
          const byProfile = new Map(current.data.providerAuth.map((status) => [status.profileId, status]));
          const errors = new Set(current.data.providerAuthErrors);
          results.forEach((result, index) => {
            const profileId = activeProfiles[index];
            if (result.status === "fulfilled") {
              byProfile.set(profileId, result.value);
              errors.delete(profileId);
            } else {
              errors.add(profileId);
            }
          });
          return {
            ...current,
            data: {
              ...current.data,
              providerAuth: [...byProfile.values()],
              providerAuthErrors: [...errors]
            }
          };
        });
      });
    }, 1500);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [state]);

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
        {
          title: task.title.trim(),
          description: task.description.trim(),
          criteria,
          dependencyIds: task.dependencyIds
        },
        signal
      ),
      "created_task"
    ).then((succeeded) => {
      if (succeeded) {
        setTask(emptyTask);
      }
    });
  }

  function setTaskDependency(taskId: string, selected: boolean): void {
    setTask((current) => {
      const dependencyIds = selected
        ? [...new Set([...current.dependencyIds, taskId])]
        : current.dependencyIds.filter((candidate) => candidate !== taskId);
      return { ...current, dependencyIds };
    });
  }

  function focusAuthRecovery(): void {
    window.setTimeout(() => authPanelRef.current?.focus(), 0);
  }

  function replaceProviderStatus(status: ProviderAuthStatus): void {
    setState((current) => {
      if (current.kind !== "ready") {
        return current;
      }
      const remaining = current.data.providerAuth.filter(
        (candidate) => candidate.profileId !== status.profileId
      );
      const providerAvailability = current.data.providerAvailability.map((availability) => {
        if (availability.profileId !== status.profileId) {
          return availability;
        }
        const oldRefusal = availability.refusal;
        const nonAuthRefusal = oldRefusal !== null
          && !["provider_authentication_required", "provider_authentication_unconfirmed"].includes(oldRefusal.code)
          ? oldRefusal
          : null;
        const authRefusal = status.blocksLaunch
          ? {
              code: status.state === "authentication_required"
                ? "provider_authentication_required" as const
                : "provider_authentication_unconfirmed" as const,
              remediation: status.state === "authentication_required"
                ? ["authenticate_provider"]
                : ["check_provider_authentication"]
            }
          : null;
        const refusal = nonAuthRefusal ?? authRefusal;
        return {
          ...availability,
          authentication: { state: status.state, freshness: status.freshness },
          refusal,
          launchable: refusal === null
            && availability.installationState === "installed"
            && availability.initializationState === "ready"
            && availability.qualification.state === "qualified"
            && (status.state === "ready" || status.state === "unsupported")
        };
      });
      return {
        ...current,
        data: {
          ...current.data,
          providerAuth: [...remaining, status],
          providerAvailability,
          providerAuthErrors: current.data.providerAuthErrors.filter(
            (profileId) => profileId !== status.profileId
          )
        }
      };
    });
  }

  async function launchSelectedRun(): Promise<void> {
    if (state.kind !== "ready") {
      return;
    }
    const selectedRole = state.data.launch?.roles.find((option) => option.id === run.agentId);
    const selectedPack = state.data.launch?.contextPacks.find(
      (option) => contextPackKey(option) === run.contextPackKey
    );
    const profileId = selectedRole?.profileId;
    const authStatus = state.data.providerAuth.find((status) => status.profileId === profileId);
    const key = pendingLaunchKey ?? crypto.randomUUID();
    if (
      profileId !== undefined
      && (authStatus?.blocksLaunch === true || state.data.providerAuthErrors.includes(profileId))
    ) {
      setPendingLaunchKey(key);
      focusAuthRecovery();
      return;
    }
    setActiveAction("start-run");
    const controller = new AbortController();
    try {
      await apiRef.current.startRun(
        {
          agentId: run.agentId,
          taskId: run.taskId,
          contextPackId: selectedPack?.contextPackId ?? null,
          contextPackRevision: selectedPack?.revision ?? null
        },
        key,
        controller.signal
      );
      const data = await apiRef.current.load(controller.signal);
      setPendingLaunchKey(null);
      setState({ kind: "ready", data, notice: "run_started" });
    } catch (error: unknown) {
      const problem = error instanceof ApiProblem ? error : null;
      if (
        profileId !== undefined
        && ["provider_auth_required", "provider_auth_unknown", "credential_store_unavailable"].includes(
          problem?.apiError?.code ?? ""
        )
      ) {
        setPendingLaunchKey(key);
        try {
          replaceProviderStatus(
            await apiRef.current.providerAuthStatus(profileId, controller.signal)
          );
        } catch {
          // The inline unavailable state remains actionable through Check again.
        }
        focusAuthRecovery();
      } else if (!(error instanceof DOMException && error.name === "AbortError")) {
        setState({ kind: "failure", failure: failureFrom(error, text) });
      }
    } finally {
      setActiveAction(null);
    }
  }

  async function runProviderAuthAction(
    profileId: string,
    action: "login" | "cancel" | "check"
  ): Promise<void> {
    setActiveAction(`provider-auth-${action}`);
    const controller = new AbortController();
    try {
      const status = await apiRef.current.providerAuthAction(profileId, action, controller.signal);
      replaceProviderStatus(status);
      focusAuthRecovery();
    } catch (error: unknown) {
      if (!(error instanceof DOMException && error.name === "AbortError")) {
        setState((current) => {
          if (current.kind !== "ready") {
            return { kind: "failure", failure: failureFrom(error, text) };
          }
          return {
            ...current,
            data: {
              ...current.data,
              providerAuthErrors: [...new Set([...current.data.providerAuthErrors, profileId])]
            }
          };
        });
        focusAuthRecovery();
      }
    } finally {
      setActiveAction(null);
    }
  }

  function submitRun(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    const selectedRole = state.kind === "ready"
      ? state.data.launch?.roles.find((option) => option.id === run.agentId)
      : undefined;
    const selectedPack = state.kind === "ready"
      ? state.data.launch?.contextPacks.find(
        (option) => contextPackKey(option) === run.contextPackKey
      )
      : undefined;
    const selectedAvailability = state.kind === "ready"
      ? state.data.providerAvailability.find(
        (availability) => availability.profileId === selectedRole?.profileId
      )
      : undefined;
    const errors = [
      ...(run.agentId ? [] : ["agent"]),
      ...(run.taskId ? [] : ["task"]),
      ...(selectedRole?.contextMode === "accumulated" && selectedPack === undefined
        ? ["context-pack"]
        : []),
      ...(selectedRole !== undefined && selectedAvailability?.launchable !== true
        ? ["provider-availability"]
        : [])
    ];
    setRunErrors(new Set(errors));
    if (errors.length > 0) {
      return;
    }
    void launchSelectedRun();
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
  const contextPackOptions = launch?.contextPacks ?? [];
  const environmentReady = configured && Boolean(launch?.launchEnabled);
  const guidance = data.guidance?.blockerCode === null ? null : data.guidance;
  const canConfigureRuntime = data.setup.state === "setup_unconfigured"
    && guidance?.nextActionKey === "configure_runtime";
  const selectedRole = roleOptions.find((option) => option.id === run.agentId);
  const selectedProfileId = selectedRole?.profileId;
  const selectedAvailability = data.providerAvailability.find(
    (availability) => availability.profileId === selectedProfileId
  );
  const selectedAuth = data.providerAuth.find(
    (status) => status.profileId === selectedProfileId
  );
  const visibleAuth = selectedProfileId === undefined
    ? data.providerAuth.find((status) => status.blocksLaunch) ?? null
    : selectedAuth ?? null;
  const authStatusUnavailable = selectedProfileId !== undefined
    && data.providerAuthErrors.includes(selectedProfileId);
  const authActionProfileId = selectedProfileId ?? visibleAuth?.profileId;
  const showAuthRecovery = visibleAuth?.blocksLaunch === true
    || (pendingLaunchKey !== null && visibleAuth?.state === "ready")
    || authStatusUnavailable;

  function confirmRuntimeConfiguration(): void {
    setConfigurationConfirmationOpen(false);
    void perform("runtime", (signal) => apiRef.current.setup("runtime", signal), "action_complete");
  }

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
              <>
                {guidance !== null ? (
                  <section aria-live="polite" className="notice">
                    {guidance.tools.length > 0 ? (
                      <p>{text("guidance_missing_tools")} <strong>{guidance.tools.join(", ")}</strong></p>
                    ) : null}
                    <p>{text(guidanceActionMessage[guidance.nextActionKey])}</p>
                    <button
                      className="button button-secondary"
                      disabled={activeAction !== null}
                      onClick={() => void refresh()}
                      type="button"
                    >
                      {text("check_again")}
                    </button>
                  </section>
                ) : null}
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
                  {canConfigureRuntime ? (
                  <button
                    className="button button-primary"
                    disabled={activeAction !== null}
                    onClick={() => setConfigurationConfirmationOpen(true)}
                    type="button"
                  >
                    {text("configure_runtime")}
                  </button>
                  ) : null}
                </div>
                {configurationConfirmationOpen ? (
                  <section
                    aria-describedby="configuration-confirmation-details"
                    aria-labelledby="configuration-confirmation-title"
                    aria-modal="true"
                    className="notice"
                    role="dialog"
                  >
                    <h3 id="configuration-confirmation-title">{text("configuration_confirmation_title")}</h3>
                    <p id="configuration-confirmation-details">{text("configuration_confirmation_write")}</p>
                    <p>{text("configuration_confirmation_non_actions")}</p>
                    <div className="button-row">
                      <button className="button button-primary" disabled={activeAction !== null} onClick={confirmRuntimeConfiguration} type="button">
                        {activeAction === "runtime" ? text("working") : text("configuration_confirmation_confirm")}
                      </button>
                      <button className="button button-secondary" disabled={activeAction !== null} onClick={() => setConfigurationConfirmationOpen(false)} type="button">
                        {text("configuration_confirmation_cancel")}
                      </button>
                    </div>
                  </section>
                ) : null}
              </>
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
                {taskOptions.length > 0 ? (
                  <fieldset className="dependency-picker">
                    <legend>{text("task_dependencies")}</legend>
                    <p className="small-copy">{text("task_dependencies_help")}</p>
                    <div className="dependency-option-list">
                      {taskOptions.map((option) => (
                        <label className="dependency-option" htmlFor={`task-dependency-${option.id}`} key={option.id}>
                          <input
                            checked={task.dependencyIds.includes(option.id)}
                            id={`task-dependency-${option.id}`}
                            onChange={(event) => setTaskDependency(option.id, event.target.checked)}
                            type="checkbox"
                          />
                          <span>{option.title}</span>
                          <span className="dependency-option-state">{option.state}</span>
                        </label>
                      ))}
                    </div>
                  </fieldset>
                ) : null}
                <button className="button button-primary" type="submit">{activeAction === "create-task" ? text("working") : text("create_task")}</button>
              </fieldset>
            </form>
          </WorkflowCard>
          <WorkflowCard ready={false} title={text("step_run")}>
            <p className="small-copy">{text("run_help")}</p>
            <section aria-labelledby="provider-availability-title" className="provider-availability" role="status">
              <h3 id="provider-availability-title">{text("provider_availability_title")}</h3>
              {data.providerAvailability.length === 0 ? (
                <p className="field-error">{text("provider_availability_unavailable")}</p>
              ) : data.providerAvailability.map((availability) => (
                <article className="provider-availability-item" key={availability.profileId}>
                  <p>
                    <strong>{availability.profileId}</strong> — {availability.provider}
                    {availability.model === null ? "" : ` / ${availability.model}`}
                  </p>
                  <p className="small-copy">
                    {text("provider_availability_install_label")}: {availability.installationState}. {text("provider_availability_init_label")}: {availability.initializationState}. {text("provider_availability_qualification_label")}: {availability.qualification.state}. {text("provider_availability_auth_label")}: {availability.authentication.state}.
                  </p>
                  <p className={availability.launchable ? "small-copy" : "field-error"}>
                    {availability.launchable
                      ? text("provider_availability_launchable")
                      : text(availabilityRefusalMessage[availability.refusal?.code ?? "provider_authentication_unconfirmed"])}
                  </p>
                  <ul className="small-copy">
                    {availability.capabilityRefusals.map((refusal) => (
                      <li key={refusal.code}>{text(capabilityRefusalMessage[refusal.code])}</li>
                    ))}
                  </ul>
                </article>
              ))}
            </section>
            {showAuthRecovery ? (
              <section
                aria-labelledby="provider-auth-title"
                aria-live="assertive"
                className="provider-auth-critical"
                ref={authPanelRef}
                role={visibleAuth?.blocksLaunch === true || authStatusUnavailable ? "alert" : "status"}
                tabIndex={-1}
              >
                <p className="provider-auth-severity">{text("provider_auth_critical")}</p>
                <h3 id="provider-auth-title">{text("provider_auth_title")}</h3>
                <p>
                  {visibleAuth === null
                    ? text("provider_auth_status_unavailable")
                    : text(providerAuthStateMessage[visibleAuth.state])}
                </p>
                {visibleAuth !== null ? (
                  <p className="small-copy">
                    {text("provider_auth_profile")} <strong>{visibleAuth.profileId}</strong>. {text("provider_auth_checked_at")} {visibleAuth.checkedAt}
                  </p>
                ) : null}
                <p className="small-copy">{text("provider_auth_secret_boundary")}</p>
                {visibleAuth?.state === "credential_store_unavailable" ? (
                  <p className="small-copy">{text("provider_auth_repair_host_help")}</p>
                ) : null}
                <div className="button-row">
                  {visibleAuth?.actionIds.includes("authenticate") ? (
                    <button
                      className="button button-primary"
                      disabled={activeAction !== null || !data.meta.writesEnabled}
                      onClick={() => void runProviderAuthAction(visibleAuth.profileId, "login")}
                      type="button"
                    >
                      {text(providerAuthActionMessage.authenticate)}
                    </button>
                  ) : null}
                  {visibleAuth?.actionIds.includes("cancel_authentication") ? (
                    <button
                      className="button button-secondary button-inline"
                      disabled={activeAction !== null || !data.meta.writesEnabled}
                      onClick={() => void runProviderAuthAction(visibleAuth.profileId, "cancel")}
                      type="button"
                    >
                      {text(providerAuthActionMessage.cancel_authentication)}
                    </button>
                  ) : null}
                  {(visibleAuth?.actionIds.includes("check_again") || authStatusUnavailable) && authActionProfileId !== undefined ? (
                    <button
                      className="button button-secondary button-inline"
                      disabled={activeAction !== null || !data.meta.writesEnabled}
                      onClick={() => void runProviderAuthAction(authActionProfileId, "check")}
                      type="button"
                    >
                      {text(providerAuthActionMessage.check_again)}
                    </button>
                  ) : null}
                  {pendingLaunchKey !== null && visibleAuth?.state === "ready" ? (
                    <button
                      className="button button-primary"
                      disabled={activeAction !== null}
                      onClick={() => void launchSelectedRun()}
                      type="button"
                    >
                      {text(providerAuthActionMessage.continue_launch)}
                    </button>
                  ) : null}
                </div>
                <p className="small-copy">{text("provider_auth_new_run_only")}</p>
              </section>
            ) : null}
            {notice === "run_started" ? (
              <div className="notice" role="status">
                <p>{text("run_started_help")}</p>
                <a className="notice-link" href="/">{text("review_in_legacy")}</a>
              </div>
            ) : null}
            <form noValidate onSubmit={submitRun}>
              <fieldset disabled={!environmentReady || activeAction !== null || (selectedRole !== undefined && selectedAvailability?.launchable !== true)}>
                <label htmlFor="run-role">{text("select_role")}</label>
                <select aria-invalid={validation([...runErrors], "agent")} id="run-role" onChange={(event) => setRun({ ...run, agentId: event.target.value, contextPackKey: "" })} value={run.agentId}>
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
                {selectedRole?.contextMode === "accumulated" ? (
                  <>
                    <label htmlFor="run-context-pack">{text("select_context_pack")}</label>
                    <select
                      aria-describedby="run-context-pack-help"
                      aria-invalid={validation([...runErrors], "context-pack")}
                      id="run-context-pack"
                      onChange={(event) => setRun({ ...run, contextPackKey: event.target.value })}
                      value={run.contextPackKey}
                    >
                      <option value="">{text("select_context_pack")}</option>
                      {contextPackOptions.map((option) => (
                        <option key={contextPackKey(option)} value={contextPackKey(option)}>
                          {option.summary} — {option.contextPackId} @ {option.revision}
                        </option>
                      ))}
                    </select>
                    <p className="small-copy" id="run-context-pack-help">{text("context_pack_exact_help")}</p>
                    {validation([...runErrors], "context-pack") ? <p className="field-error">{text("form_error_context_pack")}</p> : null}
                    {contextPackOptions.length === 0 ? <p className="field-error">{text("no_context_packs")}</p> : null}
                    {launch?.contextPackOptionsStatus.truncated ? (
                      <p className="field-error" role="status">{text("context_pack_options_truncated")}</p>
                    ) : null}
                    <p className="small-copy">{text("context_resume_unavailable")}</p>
                  </>
                ) : selectedRole?.contextMode === "fresh" ? (
                  <p className="small-copy">{text("context_fresh_run_help")}</p>
                ) : null}
                {validation([...runErrors], "provider-availability") ? <p className="field-error">{text("form_error_provider_availability")}</p> : null}
                <button className="button button-primary" type="submit">{activeAction === "start-run" ? text("working") : text("start_run")}</button>
              </fieldset>
            </form>
          </WorkflowCard>
          <StarterPacksSection api={apiRef.current} text={text} />
          {configured ? (
            <ContextPacksSection
              api={apiRef.current}
              text={text}
              writesEnabled={data.meta.writesEnabled}
            />
          ) : null}
          {configured ? <TrackerSection api={apiRef.current} locale={locale} text={text} /> : null}
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
