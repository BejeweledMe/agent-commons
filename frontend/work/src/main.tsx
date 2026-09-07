import { type FormEvent, type ReactElement, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";

import { ApiProblem, WorkApi } from "./api";
import { ContextPackRetryIdentity } from "./contextPackEditorState";
import { RolePresetPicker } from "./components/RolePresetPicker";
import { chooseRolePreset } from "./rolePresetState";
import { AppHeader } from "./components/AppHeader";
import { FailurePanel } from "./components/FailurePanel";
import { LibrarySection } from "./components/LibrarySection";
import { TaskComposer } from "./components/TaskComposer";
import { LaunchRecoveryPanel } from "./components/LaunchRecoveryPanel";
import { freezeLaunchIntent, launchIntentIsVisible, restoreLaunchDraft, type LaunchIntent } from "./launchIntentState.js";
import { taskDependencyCatalog } from "./taskDependencyState.js";
import type { TrackerViewState } from "./trackerState.js";
import { isEditingTarget, parseWorkRoute, workRouteHref, type WorkRoute } from "./appRouteState";
import { TrackerSection } from "./components/TrackerSection";
import { WorkflowCard } from "./components/WorkflowCard";
import type {
  ContextPackOption,
  DesignPackageOption,
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
  fromPresetId: string;
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

type RunDraft = {
  agentId: string;
  taskId: string;
  contextPackKey: string;
  designPackageKey: string;
};
type FormErrors = ReadonlySet<string>;

const emptyRole: RoleDraft = { fromPresetId: "", name: "", profileId: "", rationale: "", contextMode: "fresh" };
const emptyTask: TaskDraft = { title: "", description: "", criteria: "", dependencyIds: [] };
const emptyRun: RunDraft = { agentId: "", taskId: "", contextPackKey: "", designPackageKey: "" };

function contextPackKey(option: ContextPackOption): string {
  return `${option.contextPackId}@${option.revision}`;
}

function designPackageKey(option: DesignPackageOption): string {
  return `${option.designPackageId}@${option.revision}`;
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
  const [route, setRoute] = useState<WorkRoute>(() => parseWorkRoute(window.location.search));
  const routeRef = useRef(route);
  const [search, setSearch] = useState("");
  const [launchOpen, setLaunchOpen] = useState(false);
  const [actionErrors, setActionErrors] = useState<Record<string, { failure: Failure; retry: () => void; uncertain: boolean }>>({});
  const newTaskButton = useRef<HTMLButtonElement>(null);
  const navigationRevision = useRef(0);
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
  const [pendingLaunch, setPendingLaunch] = useState<LaunchIntent | null>(null);
  const pendingLaunchRef = useRef<LaunchIntent | null>(null);
  const runRef = useRef(run);
  runRef.current = run;
  const [launchedTaskId, setLaunchedTaskId] = useState<string | null>(null);
  const [trackerObservation, setTrackerObservation] = useState<TrackerViewState>({ kind: "loading" });
  const dependencies = useMemo(() => taskDependencyCatalog(trackerObservation), [trackerObservation]);
  const apiRef = useRef(new WorkApi());
  const taskRetryIdentity = useRef(new ContextPackRetryIdentity());
  const authPanelRef = useRef<HTMLElement | null>(null);
  const pendingLaunchRetry = useRef<(() => Promise<void>) | null>(null);
  const text = useMemo(() => (key: MessageKey) => translate(locale, key), [locale]);

  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);


  function navigate(patch: Partial<WorkRoute>, replace = false): void {
    navigationRevision.current += 1;
    const previous = routeRef.current;
    const next = { ...previous, ...patch };
    const destinationChanged = next.view !== previous.view
      || (next.view === "library" && next.libraryTab !== previous.libraryTab);
    routeRef.current = next;
    const href = workRouteHref(next);
    if (replace) window.history.replaceState(null, "", href);
    else if (href !== window.location.pathname + window.location.search) window.history.pushState(null, "", href);
    setRoute(next);
    if (destinationChanged) {
      const revision = navigationRevision.current;
      window.requestAnimationFrame(() => {
        if (navigationRevision.current === revision) window.scrollTo({ top: 0, left: 0, behavior: "auto" });
      });
    }
  }

  function selectTask(taskId: string | null): void {
    navigate({ taskId });
    setLaunchOpen(false);
    if (taskId !== null && window.matchMedia("(max-width: 1000px)").matches) {
      window.requestAnimationFrame(() => {
        if (routeRef.current.view !== "work" || routeRef.current.taskId !== taskId) return;
        const inspector = document.querySelector<HTMLElement>(".task-workspace-selected .task-inspector");
        inspector?.scrollIntoView({ block: "start", behavior: "auto" });
        inspector?.focus({ preventScroll: true });
      });
    }
  }

  function closeComposer(): void {
    navigate({ composer: false });
    window.setTimeout(() => newTaskButton.current?.focus(), 0);
  }

  function prepareLaunch(taskId: string): void {
    setRun((current) => pendingLaunchRef.current?.input.taskId === taskId ? restoreLaunchDraft(pendingLaunchRef.current) : current.taskId === taskId ? current : { ...emptyRun, taskId });
    navigate({ view: "work", taskId, composer: false });
    setLaunchOpen(true);
    window.setTimeout(() => document.getElementById("run-role")?.focus(), 0);
  }

  useEffect(() => {
    const onPop = (): void => {
      navigationRevision.current += 1;
      const next = parseWorkRoute(window.location.search);
      routeRef.current = next;
      setRoute(next);
      setLaunchOpen(false);
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent): void => {
      if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey || isEditingTarget(event.target)) return;
      if (event.key.toLowerCase() === "n") {
        event.preventDefault();
        navigate({ view: "work", composer: true });
        window.setTimeout(() => document.getElementById("task-title")?.focus(), 0);
      } else if (event.key === "/") {
        event.preventDefault();
        navigate({ view: "work" });
        window.setTimeout(() => document.querySelector<HTMLInputElement>(".task-view-toolbar input")?.focus(), 0);
      } else if (event.key === "Escape" && route.composer) closeComposer();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [route]);

  function clearActionError(action: string): void {
    setActionErrors((current) => { const next = { ...current }; delete next[action]; return next; });
  }
  function recordActionError(action: string, error: unknown, retry: () => void): void {
    setActionErrors((current) => ({ ...current, [action]: { failure: failureFrom(error, text), retry,
      uncertain: !(error instanceof ApiProblem) || error.status >= 500 } }));
  }
  function actionFeedback(action: string): ReactElement | null {
    const actionError = actionErrors[action];
    if (!actionError) return null;
    return <div className="action-failure" role="alert">
      <h3>{actionError.failure.title}</h3>
      <p>{text("shell_retry_help")}</p>
      <p>{actionError.failure.nextStep}</p>
      <button className="button button-secondary button-inline" disabled={activeAction !== null} onClick={actionError.retry} type="button">{text("shell_retry_original")}</button>
      <details><summary>{text("shell_technical_details")}</summary><code>{actionError.failure.code}</code>
        <ul>{actionError.failure.safeNextActions.map((item) => <li key={item}>{item}</li>)}</ul>
      </details>
    </div>;
  }

  async function load(signal: AbortSignal): Promise<void> {
    setState((current) => current.kind === "ready" ? current : { kind: "checking" });
    try {
      await apiRef.current.connect(signal);
      const data = await apiRef.current.load(signal);
      setState((current) => ({ kind: "ready", data, notice: current.kind === "ready" ? current.notice : null }));
    } catch (error: unknown) {
      if (error instanceof DOMException && error.name === "AbortError") {
        return;
      }
      setState((current) => current.kind === "ready" ? current : { kind: "failure", failure: failureFrom(error, text) });
      recordActionError("refresh", error, () => void refresh());
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
    clearActionError("refresh");
    const controller = new AbortController();
    await load(controller.signal);
  }

  async function perform(
    action: string,
    work: (signal: AbortSignal) => Promise<void>,
    notice: MessageKey
  ): Promise<boolean> {
    setActiveAction(action);
    clearActionError(action);
    const controller = new AbortController();
    try {
      await work(controller.signal);
      const data = await apiRef.current.load(controller.signal);
      setState({ kind: "ready", data, notice });
      return true;
    } catch (error: unknown) {
      if (!(error instanceof DOMException && error.name === "AbortError")) {
        recordActionError(action, error, () => void perform(action, work, notice));
      }
      return false;
    } finally {
      setActiveAction(null);
    }
  }

  function submitRole(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    if (actionErrors["create-role"]?.uncertain || state.kind !== "ready" || !state.data.meta.writesEnabled || state.data.setup.state !== "setup_configured") return;
    const errors = [
      ...(role.name.trim() ? [] : ["name"]),
      ...(role.profileId ? [] : ["profile"]),
      ...(role.rationale.trim() ? [] : ["rationale"])
    ];
    setRoleErrors(new Set(errors));
    if (errors.length > 0) {
      return;
    }
    const roleInput = { ...role, name: role.name.trim(), rationale: role.rationale.trim() };
    const roleKey = crypto.randomUUID();
    void perform(
      "create-role",
      async (signal) => {
        await apiRef.current.createRole(roleInput, signal, roleKey);
        setRole((current) => JSON.stringify({ ...current, name: current.name.trim(), rationale: current.rationale.trim() }) === JSON.stringify(roleInput) ? emptyRole : current);
      },
      "created_role"
    );
  }

  function submitTask(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    if (actionErrors["create-task"]?.uncertain) return;
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
    const input = {
      title: task.title.trim(), description: task.description.trim(),
      criteria, dependencyIds: [...task.dependencyIds]
    };
    const idempotencyKey = taskRetryIdentity.current.forOperation(JSON.stringify(input));
    const operationNavigation = navigationRevision.current;
    const submit = async (): Promise<void> => {
      setActiveAction("create-task");
      clearActionError("create-task");
      try {
        const result = await apiRef.current.createTask(input, new AbortController().signal, idempotencyKey);
        apiRef.current.forgetTaskWrite(idempotencyKey);
        taskRetryIdentity.current.reset();
        setTask((current) => JSON.stringify({ title: current.title.trim(), description: current.description.trim(), criteria: current.criteria.split("\n").map((item) => item.trim()).filter(Boolean), dependencyIds: [...current.dependencyIds] }) === JSON.stringify(input) ? emptyTask : current);
        if (navigationRevision.current === operationNavigation) {
          navigate({ view: "work", taskId: result.taskId, composer: false });
          setRun((current) => ({ ...current, taskId: result.taskId }));
        }
        setState((current) => current.kind === "ready" ? { ...current, notice: "created_task" } : current);
        void refresh();
      } catch (error: unknown) {
        recordActionError("create-task", error, () => void submit());
      } finally { setActiveAction(null); }
    };
    void submit();
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


  function restorePendingLaunch(): void {
    const intent = pendingLaunchRef.current;
    if (intent === null) return;
    const restored = restoreLaunchDraft(intent);
    runRef.current = restored;
    setRun(restored);
    navigate({ view: "work", taskId: intent.input.taskId, composer: false });
    setLaunchOpen(true);
  }

  function retryPendingLaunch(): void {
    const intent = pendingLaunchRef.current;
    if (intent === null) return;
    if (routeRef.current.view !== "work" || !launchIntentIsVisible(intent, routeRef.current.taskId, runRef.current)) {
      restorePendingLaunch();
      return;
    }
    void pendingLaunchRetry.current?.();
  }

  function editRefusedLaunch(): void {
    if (actionErrors["start-run"]?.uncertain || activeAction !== null) return;
    restorePendingLaunch();
    pendingLaunchRef.current = null;
    pendingLaunchRetry.current = null;
    setPendingLaunch(null);
    clearActionError("start-run");
  }

  async function launchSelectedRun(): Promise<void> {
    if (pendingLaunchRef.current !== null || state.kind !== "ready" || !state.data.meta.writesEnabled) {
      return;
    }
    const selectedRole = state.data.launch?.roles.find((option) => option.id === run.agentId);
    const selectedPack = state.data.launch?.contextPacks.find(
      (option) => contextPackKey(option) === run.contextPackKey
    );
    const selectedDesignPackage = state.data.launch?.designPackages.find(
      (option) => designPackageKey(option) === run.designPackageKey
    );
    const profileId = selectedRole?.profileId;
    const authStatus = state.data.providerAuth.find((status) => status.profileId === profileId);
    const key = crypto.randomUUID();
    const input = {
      agentId: run.agentId, taskId: run.taskId,
      contextPackId: selectedPack?.contextPackId ?? null,
      contextPackRevision: selectedPack?.revision ?? null,
      designPackageId: selectedDesignPackage?.designPackageId ?? null,
      designPackageRevision: selectedDesignPackage?.revision ?? null
    };
    const intent = freezeLaunchIntent({ key, input, draft: run,
      taskTitle: dependencies.tasks.find((task) => task.id === run.taskId)?.title
        ?? state.data.launch?.tasks.find((task) => task.id === run.taskId)?.title ?? run.taskId,
      roleName: selectedRole?.name ?? run.agentId, profileId: profileId ?? null });
    pendingLaunchRef.current = intent;
    setPendingLaunch(intent);
    const execute = async (): Promise<void> => {
      setActiveAction("start-run");
      clearActionError("start-run");
      const controller = new AbortController();
      try {
        await apiRef.current.startRun(intent.input, intent.key, controller.signal);
        setPendingLaunch(null);
        pendingLaunchRef.current = null;
        setLaunchedTaskId(intent.input.taskId);
        pendingLaunchRetry.current = null;
        setState((current) => current.kind === "ready" ? { ...current, notice: "run_started" } : current);
        void refresh();
      } catch (error: unknown) {
        const problem = error instanceof ApiProblem ? error : null;
        setPendingLaunch(intent);
        pendingLaunchRetry.current = execute;
        if (profileId !== undefined && ["provider_auth_required", "provider_auth_unknown", "credential_store_unavailable"].includes(problem?.apiError?.code ?? "")) {
          try { replaceProviderStatus(await apiRef.current.providerAuthStatus(profileId, controller.signal)); } catch { /* The unavailable recovery state stays visible. */ }
          focusAuthRecovery();
        }
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          recordActionError("start-run", error, () => void execute());
        }
      } finally { setActiveAction(null); }
    };
    pendingLaunchRetry.current = execute;
    if (profileId !== undefined && (authStatus?.blocksLaunch === true || state.data.providerAuthErrors.includes(profileId))) {
      setPendingLaunch(intent);
      focusAuthRecovery();
      return;
    }
    await execute();
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
    if (pendingLaunchRef.current !== null || state.kind !== "ready" || !state.data.meta.writesEnabled) return;
    const selectedRole = state.kind === "ready"
      ? state.data.launch?.roles.find((option) => option.id === run.agentId)
      : undefined;
    const selectedPack = state.kind === "ready"
      ? state.data.launch?.contextPacks.find(
        (option) => contextPackKey(option) === run.contextPackKey
      )
      : undefined;
    const selectedDesignPackage = state.kind === "ready" && run.designPackageKey !== ""
      ? state.data.launch?.designPackages.find(
        (option) => designPackageKey(option) === run.designPackageKey
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
      ...(run.designPackageKey !== "" && selectedDesignPackage === undefined
        ? ["design-package"]
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
  const presetOptions = catalog?.presets ?? [];
  const roleOptions = launch?.roles ?? [];
  const taskOptions = launch?.tasks ?? [];
  const contextPackOptions = launch?.contextPacks ?? [];
  const designPackageOptions = launch?.designPackages ?? [];
  const environmentReady = configured && Boolean(launch?.launchEnabled);
  const guidance = data.guidance?.blockerCode === null ? null : data.guidance;
  const canConfigureRuntime = data.setup.state === "setup_unconfigured"
    && guidance?.nextActionKey === "configure_runtime";
  const selectedRole = roleOptions.find((option) => option.id === run.agentId);
  const pendingLaunchVisible = pendingLaunch !== null && launchIntentIsVisible(pendingLaunch, route.taskId, run);
  const selectedProfileId = pendingLaunchVisible ? pendingLaunch?.profileId ?? undefined : selectedRole?.profileId;
  const selectedTeamTask = trackerObservation.kind === "ready" ? trackerObservation.snapshot.tasks.find((task) => task.taskId === route.taskId) : undefined;
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
    || (pendingLaunchVisible && visibleAuth?.state === "ready")
    || authStatusUnavailable;

  function confirmRuntimeConfiguration(): void {
    setConfigurationConfirmationOpen(false);
    void perform("runtime", (signal) => apiRef.current.setup("runtime", signal), "action_complete");
  }

  const workspaceInitialized = !["setup_uninitialized", "setup_not_a_repository"].includes(data.setup.state);
  return (
    <main className="work-app">
      <aside className="app-rail" aria-label={text("shell_navigation")}>
        <p className="app-brand">Agent Commons</p>
        <p className="project-label">{text("project_label")}</p>
        <p className="project-name">{repositoryBasename(data.meta.repo)}</p>
        <nav className="primary-navigation">
          {(["work", "team", "library", "settings"] as const).map((view) => <a key={view}
            aria-current={route.view === view ? "page" : undefined}
            href={workRouteHref({ ...route, view })}
            onClick={(event) => { if (!event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) { event.preventDefault(); navigate({ view }); } }}>
            {text(`shell_nav_${view}`)}
          </a>)}
        </nav>
        <div className="rail-status"><span className="status-dot" aria-hidden="true" />{setupLabel(data.setup.state, text)}</div>
        <p className="small-copy rail-help">{text("shell_local_workspace")}</p>
      </aside>
      <div className="app-content">
        <AppHeader locale={locale} onLocaleChange={setLocale} text={text} title={text(`shell_nav_${route.view}`)} />
        <div className="workspace-content">
          {notice !== null ? <p className="notice workspace-notice" role="status">{text(notice)}</p> : null}
          {actionFeedback("refresh")}
          <section hidden={route.view !== "work"} aria-label={text("shell_nav_work")}>
            <div className="workspace-toolbar"><p className="small-copy">{text("shell_work_intro")}</p>
              <button ref={newTaskButton} className="button button-primary" disabled={!workspaceInitialized || !data.meta.writesEnabled} onClick={() => { navigate({ composer: true }); window.setTimeout(() => document.getElementById("task-title")?.focus(), 0); }} type="button">{text("shell_new_task")}</button>
            </div>
            {!configured ? <div className="notice setup-notice" role="status"><p>{text(workspaceInitialized ? "shell_provider_setup_later" : "workspace_needs_setup")}</p><button className="notice-link" onClick={() => navigate({ view: "settings" })} type="button">{text("shell_open_settings")}</button></div> : null}
            <div hidden={!route.composer}>
              <TaskComposer draft={task} onChange={setTask} submitBlocked={actionErrors["create-task"]?.uncertain === true} errors={taskErrors} busy={activeAction !== null} writesEnabled={workspaceInitialized && data.meta.writesEnabled} tasks={dependencies.tasks} dependencyStatus={dependencies.status} onSubmit={submitTask} onClose={closeComposer} onDependency={setTaskDependency} text={text} />
              {actionFeedback("create-task")}
            </div>
            {pendingLaunch !== null ? <LaunchRecoveryPanel intent={pendingLaunch}
              visible={pendingLaunchVisible && launchOpen} busy={activeAction !== null || !data.meta.writesEnabled}
              uncertain={actionErrors["start-run"]?.uncertain === true}
              failureCode={actionErrors["start-run"]?.failure.code ?? null}
              onRestore={restorePendingLaunch} onRetry={retryPendingLaunch} onEdit={editRefusedLaunch} text={text} /> : null}
            <div hidden={!launchOpen || route.taskId !== run.taskId} className="launch-composer">
              <div className="composer-heading"><h2>{text("shell_launch_task")}</h2><button className="button button-secondary button-inline" type="button" onClick={() => setLaunchOpen(false)}>{text("shell_close")}</button></div>
          <WorkflowCard ready={false} title={text("step_run")}>
            <p className="small-copy">{text("shell_launch_help")}</p>
            <p className="launch-task-title">{taskOptions.find((option) => option.id === run.taskId)?.title ?? run.taskId}</p>
            <button className="notice-link" type="button" onClick={() => navigate({ view: "team" })}>{text("shell_choose_team")}</button>
            {selectedAvailability && !selectedAvailability.launchable ? <p className="field-error" role="status">{text(availabilityRefusalMessage[selectedAvailability.refusal?.code ?? "provider_authentication_unconfirmed"])}</p> : null}
            {selectedRole && selectedAvailability === undefined ? <p className="field-error" role="status">{text("provider_availability_unavailable")}</p> : null}
            <button className="notice-link" type="button" onClick={() => navigate({ view: "settings" })}>{text("shell_open_settings")}</button>
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
                  {pendingLaunchVisible && visibleAuth?.state === "ready" ? (
                    <button
                      className="button button-primary"
                      disabled={activeAction !== null}
                      onClick={retryPendingLaunch}
                      type="button"
                    >
                      {text(providerAuthActionMessage.continue_launch)}
                    </button>
                  ) : null}
                </div>
                <p className="small-copy">{text("provider_auth_new_run_only")}</p>
              </section>
            ) : null}
            {notice === "run_started" && launchedTaskId === run.taskId ? (
              <div className="notice" role="status">
                <p>{text("run_started_help")}</p>
                <button className="notice-link" type="button" onClick={() => { navigate({ view: "work", taskId: run.taskId }); setLaunchOpen(false); }}>{text("shell_view_task")}</button>
              </div>
            ) : null}
            <form noValidate onSubmit={submitRun}>
              <fieldset disabled={!data.meta.writesEnabled || activeAction !== null || pendingLaunchVisible}>
                <label htmlFor="run-role">{text("select_role")}</label>
                <select aria-invalid={validation([...runErrors], "agent")} id="run-role" onChange={(event) => setRun({ ...run, agentId: event.target.value, contextPackKey: "", designPackageKey: "" })} value={run.agentId}>
                  <option value="">{text("select_role")}</option>
                  {roleOptions.map((option) => <option key={option.id} value={option.id}>{option.name}</option>)}
                </select>
                {validation([...runErrors], "agent") ? <p className="field-error">{text("form_error_run_role")}</p> : null}
                {roleOptions.length === 0 && configured ? <p className="field-error">{text("no_roles")}</p> : null}
                <label htmlFor="run-design-package">{text("select_design_package")}</label>
                <select
                  aria-describedby="run-design-package-help"
                  aria-invalid={validation([...runErrors], "design-package")}
                  id="run-design-package"
                  onChange={(event) => setRun({ ...run, designPackageKey: event.target.value })}
                  value={run.designPackageKey}
                >
                  <option value="">{text("no_design_package")}</option>
                  {designPackageOptions.map((option) => (
                    <option key={designPackageKey(option)} value={designPackageKey(option)}>
                      {option.title} — {option.screenCount} — {option.designPackageId} @ {option.revision}
                    </option>
                  ))}
                </select>
                <p className="small-copy" id="run-design-package-help">{text("design_package_exact_help")}</p>
                {validation([...runErrors], "design-package") ? <p className="field-error">{text("form_error_design_package")}</p> : null}
                {launch?.designPackageOptionsStatus.truncated ? (
                  <p className="field-error" role="status">{text("design_package_options_truncated")}</p>
                ) : null}
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
                <button className="button button-primary" disabled={!environmentReady || selectedAvailability?.launchable !== true || pendingLaunch !== null} type="submit">{activeAction === "start-run" ? text("working") : text("start_run")}</button>
              </fieldset>
            </form>

          </WorkflowCard>
            </div>
            {workspaceInitialized ? <TrackerSection api={apiRef.current} locale={locale} text={text} writesEnabled={data.meta.writesEnabled} onObservation={setTrackerObservation}
              selectedTaskId={route.taskId} onSelectTask={selectTask} onLaunchTask={prepareLaunch}
              search={search} onSearchChange={setSearch} filter={route.filter} onFilterChange={(filter) => navigate({ filter })} /> : null}
          </section>
          <section hidden={route.view !== "team"} aria-label={text("shell_nav_team")} className="team-section">
            <div className="workspace-toolbar"><p className="small-copy">{text("shell_team_intro")}</p><button className="button button-secondary button-inline" type="button" onClick={() => navigate({ view: "library", libraryTab: "templates" })}>{text("shell_browse_recipes")}</button></div>
            <div className="team-task-target" role="status">
              <p className="small-copy">{text("shell_team_target")}</p>
              <strong>{selectedTeamTask?.title ?? text(route.taskId ? "shell_team_target_unavailable" : "shell_team_select_task")}</strong>
            </div>
            <ul className="team-role-list">{roleOptions.map((option) => <li key={option.id}><div><strong>{option.name}</strong><p className="small-copy"><code>{option.profileId}</code> · {option.contextMode === "fresh" ? text("context_fresh") : text("context_accumulated")}</p></div>
              <button className="button button-secondary button-inline" type="button" disabled={!selectedTeamTask || !data.meta.writesEnabled || activeAction !== null}
                onClick={() => { if (!selectedTeamTask) return; prepareLaunch(selectedTeamTask.taskId); if (pendingLaunchRef.current?.input.taskId !== selectedTeamTask.taskId) setRun((current) => ({ ...current, agentId: option.id })); }}>{text("shell_use_role")}</button>
            </li>)}</ul>
            {roleOptions.length === 0 ? <p className="empty-guidance">{text("shell_no_active_roles")}</p> : null}
          <WorkflowCard ready={roleOptions.length > 0} title={text("step_role")}>
            <p className="small-copy">{text("role_help")}</p>
            <form id="hire-role-form" noValidate onSubmit={submitRole}>
              <fieldset disabled={!configured || !data.meta.writesEnabled || activeAction !== null}>
                <RolePresetPicker
                  presets={presetOptions}
                  value={role.fromPresetId}
                  text={text}
                  onChange={(id) => setRole((current) => chooseRolePreset(current, id, presetOptions))}
                />
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
                  disabled={role.fromPresetId !== ""}
                  id="role-profile"
                  onChange={(event) => setRole({ ...role, profileId: event.target.value })}
                  value={role.profileId}
                >
                  <option value="">{text("select_profile")}</option>
                  {profileOptions.map((profile) => <option key={profile.id} value={profile.id}>{profile.id.endsWith("-independent-reviewer") ? `${text("shell_profile_reviewer")} · ` : profile.id.endsWith("-builder") ? `${text("shell_profile_builder")} · ` : ""}{profile.label}</option>)}
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
                <select disabled={role.fromPresetId !== ""} id="context-mode" onChange={(event) => setRole({ ...role, contextMode: event.target.value })} value={role.contextMode}>
                  <option value="fresh">{text("context_fresh")}</option>
                  <option value="accumulated">{text("context_accumulated")}</option>
                </select>
                <button className="button button-primary" disabled={actionErrors["create-role"]?.uncertain === true} type="submit">{activeAction === "create-role" ? text("working") : text("create_role")}</button>
              </fieldset>
            </form>
            {actionFeedback("create-role")}
          </WorkflowCard>
          </section>
          <section hidden={route.view !== "library"} aria-label={text("shell_nav_library")}>
            {workspaceInitialized ? <LibrarySection api={apiRef.current} text={text} writesEnabled={data.meta.writesEnabled}
              tab={route.libraryTab} onTabChange={(libraryTab) => navigate({ libraryTab })}
              onApplied={async () => { const updated = await apiRef.current.load(new AbortController().signal); setState((current) => current.kind === "ready" ? { ...current, data: updated } : current); }}
              onChooseTeam={() => navigate({ view: "team" })} /> : <p>{text("workspace_needs_setup")}</p>}
          </section>
          <section hidden={route.view !== "settings"} aria-label={text("shell_nav_settings")} className="settings-section">
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
            {actionFeedback("initialize")}
            {actionFeedback("runtime")}
          </WorkflowCard>
            <div className="settings-panel"><h2>{text("shell_project_details")}</h2><p className="project-name">{showFullProjectPath ? <code>{data.meta.repo}</code> : repositoryBasename(data.meta.repo)}</p><button className="notice-link" type="button" onClick={() => setShowFullProjectPath((visible) => !visible)}>{text(showFullProjectPath ? "hide_full_project_path" : "show_full_project_path")}</button></div>
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
            <div className="settings-panel"><h2>{text("shell_diagnostics")}</h2><p className="small-copy">{text("legacy_panel_help")}</p><a className="legacy-link" href="/">{text("open_legacy_panel")}</a><button className="button button-secondary" disabled={activeAction !== null} onClick={() => void refresh()} type="button">{text("refresh_status")}</button></div>
          </section>
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
