import { type FormEvent, type ReactElement, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";

import { ApiProblem, WorkApi } from "./api";
import { ContextPackRetryIdentity } from "./contextPackEditorState";
import { RolePresetPicker } from "./components/RolePresetPicker";
import { chooseRolePreset, chooseRoleProvider, chooseRoleProfile } from "./rolePresetState";
import { AppHeader } from "./components/AppHeader";
import { ProjectSidebar } from "./components/ProjectSidebar";
import { FailurePanel } from "./components/FailurePanel";
import { LibrarySection } from "./components/LibrarySection";
import { TaskComposer } from "./components/TaskComposer";
import { LaunchRecoveryPanel } from "./components/LaunchRecoveryPanel";
import { freezeLaunchIntent, launchIntentIsVisible, restoreLaunchDraft, type LaunchIntent } from "./launchIntentState.js";
import { taskDependencyCatalog } from "./taskDependencyState.js";
import type { TrackerViewState } from "./trackerState.js";
import { isEditingTarget, parseWorkRoute, workRouteHref, type WorkRoute } from "./appRouteState";
import { ProjectDraftStore, ProjectReadRequest, ProjectRegistryApi, ProjectSelection, type ProjectInspection, type ProjectList } from "./projectWorkspace.js";
import { TrackerSection } from "./components/TrackerSection";
import { WorkflowCard } from "./components/WorkflowCard";
import { SpecializationPicker } from "./components/SpecializationPicker.js";
import { LibraryApi } from "./libraryApi.js";
import { sameLibraryRef, type LibraryRef, type LibraryRole, type LibraryState } from "./libraryTypes.js";
import { TaskGraphApi } from "./taskGraphApi.js";
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
  provider: string;
  model: string;
  modelMode: "profile" | "explicit";
  specializationRef: LibraryRef | null;
  specializationName: string;
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
type ActionError = { failure: Failure; retry: () => void; uncertain: boolean };
type ProjectTransient = {
  pendingLaunch: LaunchIntent | null;
  pendingLaunchRetry: (() => Promise<void>) | null;
  taskRetry: ReturnType<ContextPackRetryIdentity["snapshot"]>;
  actionErrors: Record<string, ActionError>;
  lastHiredRole: { id: string; name: string } | null;
  roleErrors: FormErrors;
  taskErrors: FormErrors;
  runErrors: FormErrors;
  selectedTaskId: string | null;
};

const emptyRole: RoleDraft = { fromPresetId: "", name: "", profileId: "", rationale: "", contextMode: "fresh", provider: "", model: "", modelMode: "profile", specializationRef: null, specializationName: "" };
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
  const [actionErrors, setActionErrors] = useState<Record<string, ActionError>>({});
  const newTaskButton = useRef<HTMLButtonElement>(null);
  const navigationRevision = useRef(0);
  const renderedNavigationRevision = navigationRevision.current;
  const [locale, setLocale] = useState<Locale>("en");
  const [state, setState] = useState<AppState>({ kind: "checking" });
  const [activeAction, setActiveAction] = useState<string | null>(null);
  const [role, setRole] = useState<RoleDraft>(emptyRole);
  const [task, setTask] = useState<TaskDraft>(emptyTask);
  const [run, setRun] = useState<RunDraft>(emptyRun);
  const draftsRef = useRef(new ProjectDraftStore<{ role: RoleDraft; task: TaskDraft; run: RunDraft }>());
  const currentDraftRef = useRef({ role: emptyRole, task: emptyTask, run: emptyRun });
  currentDraftRef.current = { role, task, run };
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
  const hostApiRef = useRef(new WorkApi());
  const apiRef = useRef(hostApiRef.current);
  const libraryApiRef = useRef(new LibraryApi(apiRef.current));
  const registryApiRef = useRef(new ProjectRegistryApi(hostApiRef.current));
  const projectSelectionRef = useRef(new ProjectSelection());
  const readRequestRef = useRef(new ProjectReadRequest());
  const projectMutationKeys = useRef(new Map<string, string>());
  const [projectList, setProjectList] = useState<ProjectList | null>(null);
  const [legacyProjectHost, setLegacyProjectHost] = useState(false);
  const [libraryState, setLibraryState] = useState<LibraryState>({ kind: "loading" });
  const [libraryRefresh, setLibraryRefresh] = useState(0);
  const [lastHiredRole, setLastHiredRole] = useState<{ id: string; name: string } | null>(null);
  const taskRetryIdentity = useRef(new ContextPackRetryIdentity());
  const projectTransientRef = useRef(new ProjectDraftStore<ProjectTransient>());
  const currentTransientRef = useRef<Omit<ProjectTransient, "pendingLaunch" | "pendingLaunchRetry" | "taskRetry">>({
    actionErrors: {}, lastHiredRole: null, roleErrors: new Set(), taskErrors: new Set(), runErrors: new Set(), selectedTaskId: null
  });
  currentTransientRef.current = { actionErrors, lastHiredRole, roleErrors, taskErrors, runErrors, selectedTaskId: route.taskId };
  const authPanelRef = useRef<HTMLElement | null>(null);
  const pendingLaunchRetry = useRef<(() => Promise<void>) | null>(null);
  const launchSelectionVersion = useRef(0);
  const text = useMemo(() => (key: MessageKey) => translate(locale, key), [locale]);

  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

  const libraryAccessible = state.kind === "ready" && state.data.setup.state !== "setup_uninitialized" && state.data.setup.state !== "setup_not_a_repository";
  useEffect(() => {
    if (!libraryAccessible) return;
    const controller = new AbortController();
    const generation = projectSelectionRef.current.currentGeneration();
    const projectId = routeRef.current.projectId;
    const api = libraryApiRef.current;
    const stillCurrent = (): boolean => !controller.signal.aborted
      && projectSelectionRef.current.isCurrent(generation) && routeRef.current.projectId === projectId;
    void api.load(controller.signal).then((catalog) => {
      if (stillCurrent()) setLibraryState({ kind: "ready", catalog });
    }).catch(() => {
      if (stillCurrent()) setLibraryState((current) => ({ kind: "error", ...(current.kind !== "loading" && current.catalog ? { catalog: current.catalog } : {}) }));
    });
    return () => controller.abort();
  }, [libraryAccessible, libraryRefresh]);

  function chooseSpecialization(selected: LibraryRole | null, showTeam = false): void {
    setRole((current) => ({ ...current, fromPresetId: "", specializationRef: selected?.ref ?? null,
      specializationName: selected?.name ?? "", name: current.name || selected?.name || "",
      rationale: current.rationale || selected?.description || "" }));
    if (showTeam) navigate({ view: "team" });
  }


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
    const selectionVersion = ++launchSelectionVersion.current;
    const generation = projectSelectionRef.current.currentGeneration();
    const projectId = routeRef.current.projectId;
    const api = apiRef.current;
    setRun((current) => pendingLaunchRef.current?.input.taskId === taskId ? restoreLaunchDraft(pendingLaunchRef.current) : current.taskId === taskId ? current : { ...emptyRun, taskId });
    navigate({ view: "work", taskId, composer: false });
    setLaunchOpen(true);
    window.setTimeout(() => {
      if (projectSelectionRef.current.isCurrent(generation) && routeRef.current.projectId === projectId) {
        document.getElementById("run-role")?.focus();
      }
    }, 0);
    if (state.kind === "ready" && !pendingLaunchRef.current) {
      const activeRoles = new Set(state.data.launch?.roles.map((option) => option.id) ?? []);
      void new TaskGraphApi(api).detail(taskId, new AbortController().signal).then((detail) => {
        if (!projectSelectionRef.current.isCurrent(generation) || routeRef.current.projectId !== projectId
          || launchSelectionVersion.current !== selectionVersion || pendingLaunchRef.current || routeRef.current.view !== "work" || routeRef.current.taskId !== taskId
          || !detail.suggestedAgentId || !activeRoles.has(detail.suggestedAgentId)) return;
        const suggested = detail.suggestedAgentId;
        setRun((current) => current.taskId === taskId && !current.agentId ? { ...current, agentId: suggested } : current);
      }).catch(() => { /* The optional hint never replaces an explicit role choice. */ });
    }
  }

  useEffect(() => {
    const onPop = (): void => {
      navigationRevision.current += 1;
      const next = parseWorkRoute(window.location.search);
      if (next.projectId !== routeRef.current.projectId) bindProject(next.projectId);
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

  async function load(): Promise<void> {
    const projectGeneration = projectSelectionRef.current.currentGeneration();
    const read = readRequestRef.current.begin();
    const stillCurrent = (): boolean => projectSelectionRef.current.isCurrent(projectGeneration)
      && readRequestRef.current.isCurrent(read.generation);
    setState((current) => current.kind === "ready" ? current : { kind: "checking" });
    try {
      const host = hostApiRef.current;
      await host.connect(read.signal);
      let projectApi = host;
      try {
        const projects = await registryApiRef.current.list(read.signal);
        if (!stillCurrent()) return;
        setLegacyProjectHost(false);
        setProjectList(projects);
        const selectedId = routeRef.current.projectId ?? projects.defaultProjectId ?? projects.projects.find((item) => item.available && item.state === "ready")?.id ?? null;
        if (selectedId === null) {
          setState({ kind: "failure", failure: { code: "project_required", title: text("project_none"), nextStep: text("project_new"), canRetry: true, safeNextActions: [] } });
          return;
        }
        if (routeRef.current.projectId !== selectedId) {
          const next = { ...routeRef.current, projectId: selectedId };
          routeRef.current = next;
          window.history.replaceState(null, "", workRouteHref(next));
          setRoute(next);
        }
        projectApi = host.forProject(selectedId);
      } catch (error: unknown) {
        if (!(error instanceof ApiProblem) || error.status !== 404) throw error;
        // A single-workspace host predates the registry endpoint. Keep its
        // authenticated transport and do not erase it merely because projects is absent.
        setLegacyProjectHost(true);
        setProjectList(null);
      }
      apiRef.current = projectApi;
      libraryApiRef.current = new LibraryApi(projectApi);
      const data = await projectApi.load(read.signal);
      if (!stillCurrent()) return;
      setState((current) => ({ kind: "ready", data, notice: current.kind === "ready" ? current.notice : null }));
    } catch (error: unknown) {
      if (error instanceof DOMException && error.name === "AbortError") {
        return;
      }
      if (!stillCurrent()) return;
      setState((current) => current.kind === "ready" ? current : { kind: "failure", failure: failureFrom(error, text) });
      recordActionError("refresh", error, () => void refresh());
    } finally { /* The next project selection owns cancellation of this request. */ }
  }

  useEffect(() => {
    void load();
    return () => readRequestRef.current.abort();
  }, [route.projectId]);

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
    const generation = projectSelectionRef.current.currentGeneration();
    const api = apiRef.current;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      void Promise.allSettled(
        activeProfiles.map((profileId) => api.providerAuthStatus(profileId, controller.signal))
      ).then((results) => {
        if (!projectSelectionRef.current.isCurrent(generation)) return;
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
    await load();
  }

  function selectProject(projectId: string): void {
    if (routeRef.current.projectId === projectId) return;
    // Drafts are intentionally RAM-only and keyed by opaque project identity.
    // Selection also closes the active tracker through a new immutable client.
    const selectedTaskId = bindProject(projectId);
    navigate({ projectId, taskId: selectedTaskId, composer: false });
  }

  function bindProject(projectId: string | null): string | null {
    const previousId = routeRef.current.projectId;
    if (previousId !== null) {
      draftsRef.current.set(previousId, currentDraftRef.current);
      projectTransientRef.current.set(previousId, {
        pendingLaunch: pendingLaunchRef.current,
        pendingLaunchRetry: pendingLaunchRetry.current,
        taskRetry: taskRetryIdentity.current.snapshot(),
        ...currentTransientRef.current
      });
    }
    const nextDraft = projectId === null ? { role: emptyRole, task: emptyTask, run: emptyRun }
      : draftsRef.current.get(projectId, { role: emptyRole, task: emptyTask, run: emptyRun });
    setRole(nextDraft.role); setTask(nextDraft.task); setRun(nextDraft.run);
    const transient = projectId === null
      ? { pendingLaunch: null, pendingLaunchRetry: null, taskRetry: null, actionErrors: {}, lastHiredRole: null, roleErrors: new Set<string>(), taskErrors: new Set<string>(), runErrors: new Set<string>(), selectedTaskId: null }
      : projectTransientRef.current.get(projectId, { pendingLaunch: null, pendingLaunchRetry: null, taskRetry: null, actionErrors: {}, lastHiredRole: null, roleErrors: new Set<string>(), taskErrors: new Set<string>(), runErrors: new Set<string>(), selectedTaskId: null });
    pendingLaunchRef.current = transient.pendingLaunch;
    pendingLaunchRetry.current = transient.pendingLaunchRetry;
    setPendingLaunch(transient.pendingLaunch);
    taskRetryIdentity.current.restore(transient.taskRetry);
    projectSelectionRef.current.select();
    readRequestRef.current.abort();
    if (projectId !== null) {
      try { apiRef.current = hostApiRef.current.forProject(projectId); libraryApiRef.current = new LibraryApi(apiRef.current); }
      catch { /* The route loader presents the typed host failure. */ }
    }
    // Never retain a ready snapshot while its project heading/client changes.
    // The route effect owns the next scoped read and will replace this shell.
    setState({ kind: "checking" });
    setLibraryState({ kind: "loading" });
    setActiveAction(null);
    setActionErrors(transient.actionErrors);
    setLastHiredRole(transient.lastHiredRole);
    setRoleErrors(transient.roleErrors);
    setTaskErrors(transient.taskErrors);
    setRunErrors(transient.runErrors);
    setTrackerObservation({ kind: "loading" });
    setLaunchOpen(false);
    return transient.selectedTaskId;
  }

  async function inspectProject(input: { mode: "new" | "existing"; path: string; name: string }): Promise<ProjectInspection> {
    return registryApiRef.current.inspect(input, new AbortController().signal);
  }

  async function createProject(inspection: ProjectInspection): Promise<void> {
    const intent = `create:${inspection.inspectionId}`;
    const key = projectMutationKeys.current.get(intent) ?? `project-create-${crypto.randomUUID()}`;
    projectMutationKeys.current.set(intent, key);
    const result = await registryApiRef.current.create(inspection.inspectionId, key, new AbortController().signal);
    setProjectList((current) => current === null ? current : { ...current, revision: result.revision,
      defaultProjectId: current.defaultProjectId ?? result.project.id,
      projects: [...current.projects.filter((item) => item.id !== result.project.id), result.project] });
    selectProject(result.project.id);
    // Blueprint and profile choices are intentionally collected only after the
    // registry has created this project, through the existing scoped apply flow.
    navigate({ view: "library", libraryTab: "blueprints" });
    projectMutationKeys.current.delete(intent);
  }

  async function updateProject(projectId: string, expectedRevision: string, input: { name: string; archived: boolean }): Promise<void> {
    const intent = `update:${projectId}:${expectedRevision}:${JSON.stringify(input)}`;
    const key = projectMutationKeys.current.get(intent) ?? `project-update-${crypto.randomUUID()}`;
    projectMutationKeys.current.set(intent, key);
    const result = await registryApiRef.current.update(projectId, expectedRevision, input, key, new AbortController().signal);
    setProjectList((current) => current === null ? current : { ...current, revision: result.revision,
      projects: current.projects.map((item) => item.id === result.project.id ? result.project : item) });
    projectMutationKeys.current.delete(intent);
  }

  function openProjectGallery(): void {
    const projectId = routeRef.current.projectId;
    if (projectId !== null) window.location.assign(`/gallery?project=${encodeURIComponent(projectId)}`);
  }

  async function perform(
    action: string,
    api: WorkApi,
    work: (signal: AbortSignal) => Promise<void>,
    notice: MessageKey,
    projectId = routeRef.current.projectId
  ): Promise<boolean> {
    const generation = projectSelectionRef.current.currentGeneration();
    const stillCurrent = (): boolean => projectSelectionRef.current.isCurrent(generation)
      && routeRef.current.projectId === projectId;
    setActiveAction(action);
    clearActionError(action);
    const controller = new AbortController();
    try {
      await work(controller.signal);
      const data = await api.load(controller.signal);
      if (!stillCurrent()) return false;
      setState({ kind: "ready", data, notice });
      return true;
    } catch (error: unknown) {
      if (stillCurrent() && !(error instanceof DOMException && error.name === "AbortError")) {
        // The retry retains the original immutable project client and input
        // closure. It may only run after this same project is visible again.
        recordActionError(action, error, () => {
          if (routeRef.current.projectId === projectId) void perform(action, api, work, notice, projectId);
        });
      }
      return false;
    } finally {
      if (stillCurrent()) setActiveAction(null);
    }
  }

  function submitRole(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    if (actionErrors["create-role"]?.uncertain || state.kind !== "ready" || !state.data.meta.writesEnabled || state.data.setup.state !== "setup_configured") return;
    const errors = [
      ...(role.name.trim() ? [] : ["name"]),
      ...(role.profileId && (role.fromPresetId || state.data.catalog?.profiles.some((profile) => profile.id === role.profileId && profile.configured !== false)) ? [] : ["profile"]),
      ...(role.rationale.trim() ? [] : ["rationale"]),
      ...(!role.fromPresetId && role.modelMode === "explicit" && !/^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/.test(role.model.trim()) ? ["model"] : []),
      ...(role.specializationRef && (libraryState.kind !== "ready" || !libraryState.catalog.roles.some((item) => sameLibraryRef(item.ref, role.specializationRef))) ? ["specialization"] : [])
    ];
    setRoleErrors(new Set(errors));
    if (errors.length > 0) {
      return;
    }
    const roleInput = { ...role, name: role.name.trim(), rationale: role.rationale.trim() };
    const roleKey = crypto.randomUUID();
    const generation = projectSelectionRef.current.currentGeneration();
    const projectId = routeRef.current.projectId;
    const api = apiRef.current;
    const operationNavigation = navigationRevision.current;
    const operationSelection = launchSelectionVersion.current;
    const operationRun = { taskId: runRef.current.taskId, agentId: runRef.current.agentId };
    const targetTaskId = routeRef.current.taskId ?? operationRun.taskId;
    void perform(
      "create-role",
      api,
      async (signal) => {
        const created = await api.createRole(roleInput, signal, roleKey);
        if (!projectSelectionRef.current.isCurrent(generation) || routeRef.current.projectId !== projectId) return;
        setLastHiredRole({ id: created.agentId, name: roleInput.name });
        if (!pendingLaunchRef.current && navigationRevision.current === operationNavigation && launchSelectionVersion.current === operationSelection) {
          setRun((current) => current.taskId === operationRun.taskId && current.agentId === operationRun.agentId
            ? { ...current, agentId: created.agentId, taskId: targetTaskId, contextPackKey: "", designPackageKey: "" } : current);
        }
        setRole((current) => JSON.stringify({ ...current, name: current.name.trim(), rationale: current.rationale.trim() }) === JSON.stringify(roleInput) ? emptyRole : current);
      },
      "created_role",
      projectId
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
    const projectId = routeRef.current.projectId;
    const api = apiRef.current;
    const submit = async (): Promise<void> => {
      // A retry may happen after A → B → A. It keeps A's immutable client,
      // input and idempotency key, while taking A's current selection generation.
      if (routeRef.current.projectId !== projectId) return;
      const generation = projectSelectionRef.current.currentGeneration();
      const stillCurrent = (): boolean => projectSelectionRef.current.isCurrent(generation)
        && routeRef.current.projectId === projectId;
      setActiveAction("create-task");
      clearActionError("create-task");
      try {
        const result = await api.createTask(input, new AbortController().signal, idempotencyKey);
        if (!stillCurrent()) return;
        api.forgetTaskWrite(idempotencyKey);
        taskRetryIdentity.current.reset();
        setTask((current) => JSON.stringify({ title: current.title.trim(), description: current.description.trim(), criteria: current.criteria.split("\n").map((item) => item.trim()).filter(Boolean), dependencyIds: [...current.dependencyIds] }) === JSON.stringify(input) ? emptyTask : current);
        if (navigationRevision.current === operationNavigation) {
          navigate({ view: "work", taskId: result.taskId, composer: false });
          setRun((current) => ({ ...current, taskId: result.taskId }));
        }
        setState((current) => current.kind === "ready" ? { ...current, notice: "created_task" } : current);
        void refresh();
      } catch (error: unknown) {
        if (!stillCurrent()) return;
        recordActionError("create-task", error, () => void submit());
      } finally { if (stillCurrent()) setActiveAction(null); }
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

  function focusAuthRecovery(projectId = routeRef.current.projectId, generation = projectSelectionRef.current.currentGeneration()): void {
    window.setTimeout(() => {
      if (projectSelectionRef.current.isCurrent(generation) && routeRef.current.projectId === projectId) {
        authPanelRef.current?.focus();
      }
    }, 0);
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
    const generation = projectSelectionRef.current.currentGeneration();
    const projectId = routeRef.current.projectId;
    const api = apiRef.current;
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
      const attemptGeneration = projectSelectionRef.current.currentGeneration();
      if (routeRef.current.projectId !== projectId) return;
      const stillCurrent = (): boolean => projectSelectionRef.current.isCurrent(attemptGeneration)
        && routeRef.current.projectId === projectId;
      setActiveAction("start-run");
      clearActionError("start-run");
      const controller = new AbortController();
      try {
        await api.startRun(intent.input, intent.key, controller.signal);
        if (!stillCurrent()) return;
        setPendingLaunch(null);
        pendingLaunchRef.current = null;
        setLaunchedTaskId(intent.input.taskId);
        pendingLaunchRetry.current = null;
        setState((current) => current.kind === "ready" ? { ...current, notice: "run_started" } : current);
        void refresh();
      } catch (error: unknown) {
        if (!stillCurrent()) return;
        const problem = error instanceof ApiProblem ? error : null;
        setPendingLaunch(intent);
        pendingLaunchRetry.current = execute;
        if (profileId !== undefined && ["provider_auth_required", "provider_auth_unknown", "credential_store_unavailable"].includes(problem?.apiError?.code ?? "")) {
          try {
            const status = await api.providerAuthStatus(profileId, controller.signal);
            if (!stillCurrent()) return;
            replaceProviderStatus(status);
          } catch {
            if (!stillCurrent()) return;
            /* The unavailable recovery state stays visible. */
          }
          if (!stillCurrent()) return;
          focusAuthRecovery(projectId, attemptGeneration);
        }
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          recordActionError("start-run", error, () => void execute());
        }
      } finally { if (stillCurrent()) setActiveAction(null); }
    };
    pendingLaunchRetry.current = execute;
    if (profileId !== undefined && (authStatus?.blocksLaunch === true || state.data.providerAuthErrors.includes(profileId))) {
      setPendingLaunch(intent);
      focusAuthRecovery(projectId, generation);
      return;
    }
    await execute();
  }

  async function runProviderAuthAction(
    profileId: string,
    action: "login" | "cancel" | "check"
  ): Promise<void> {
    const generation = projectSelectionRef.current.currentGeneration();
    const api = apiRef.current;
    setActiveAction(`provider-auth-${action}`);
    const controller = new AbortController();
    try {
      const status = await api.providerAuthAction(profileId, action, controller.signal);
      if (!projectSelectionRef.current.isCurrent(generation)) return;
      replaceProviderStatus(status);
      focusAuthRecovery();
    } catch (error: unknown) {
      if (projectSelectionRef.current.isCurrent(generation) && !(error instanceof DOMException && error.name === "AbortError")) {
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
      if (projectSelectionRef.current.isCurrent(generation)) setActiveAction(null);
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
    if (state.failure.code === "project_required") {
      return <main className="work-app project-empty-app"><ProjectSidebar currentProjectId={null} legacy={false} locale={locale}
        projects={projectList} text={text} onGallery={openProjectGallery} onInspect={inspectProject}
        onCreate={createProject} onSelect={selectProject} onUpdate={updateProject} /></main>;
    }
    return (
      <main className="work-app work-app-centered">
        <FailurePanel failure={state.failure} onRetry={() => void refresh()} text={text} />
      </main>
    );
  }

  const { data, notice } = state;
  const renderedProjectId = route.projectId;
  const renderedProjectGeneration = projectSelectionRef.current.currentGeneration();
  const renderedProjectApi = apiRef.current;
  const configured = data.setup.state === "setup_configured";
  const catalog = data.catalog;
  const launch = data.launch;
  const profileOptions = catalog?.profiles ?? [];
  const selectedHireProfile = profileOptions.find((profile) => profile.id === role.profileId);
  const hireProvider = role.provider || selectedHireProfile?.provider || "";
  const hireProviders = [...new Set(profileOptions.filter((profile) => profile.configured !== false).map((profile) => profile.provider ?? profile.id.split("-")[0]))];
  const hireProfiles = hireProvider ? profileOptions.filter((profile) => (profile.provider ?? profile.id.split("-")[0]) === hireProvider) : profileOptions;
  const modelOffers = catalog?.modelOptions?.[hireProvider] ?? [];
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
    const api = apiRef.current;
    void perform("runtime", api, (signal) => api.setup("runtime", signal), "action_complete");
  }

  const workspaceInitialized = !["setup_uninitialized", "setup_not_a_repository"].includes(data.setup.state);
  return (
    <main className="work-app">
      <aside className="app-rail" aria-label={text("shell_navigation")}>
        <ProjectSidebar
          currentProjectId={route.projectId}
          legacy={legacyProjectHost}
          locale={locale}
          projects={projectList}
          text={text}
          onGallery={openProjectGallery}
          onInspect={inspectProject}
          onCreate={createProject}
          onSelect={selectProject}
          onUpdate={updateProject}
        />
        <p className="project-label">{text("project_label")}</p>
        <p className="project-name">{projectList?.projects.find((project) => project.id === route.projectId)?.name ?? repositoryBasename(data.meta.repo)}</p>
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
                <select aria-invalid={validation([...runErrors], "agent")} id="run-role" onChange={(event) => { ++launchSelectionVersion.current; setRun({ ...run, agentId: event.target.value, contextPackKey: "", designPackageKey: "" }); }} value={run.agentId}>
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
            <div className="workspace-toolbar"><p className="small-copy">{text("shell_team_intro")}</p><button className="button button-secondary button-inline" type="button" onClick={() => navigate({ view: "library", libraryTab: "blueprints" })}>{text("shell_browse_recipes")}</button></div>
            <div className="team-task-target" role="status">
              <p className="small-copy">{text("shell_team_target")}</p>
              <strong>{selectedTeamTask?.title ?? text(route.taskId ? "shell_team_target_unavailable" : "shell_team_select_task")}</strong>
            </div>
            {lastHiredRole ? <p className="notice" role="status">{text("created_role")}: <strong>{lastHiredRole.name}</strong>{route.taskId ? <button type="button" className="notice-link" onClick={() => { if (route.taskId) { prepareLaunch(route.taskId); if (!pendingLaunchRef.current) setRun((current) => ({ ...current, agentId: lastHiredRole.id })); } }}>{text("shell_use_role")}</button> : null}</p> : null}
            <ul className="team-role-list">{roleOptions.map((option) => <li key={option.id}><div><strong>{option.name}</strong><p className="small-copy">{option.specializationRef ? <><code>{option.specializationRef.id}</code> · </> : null}<code>{option.profileId}</code>{option.model ? <> · <code>{option.model}</code></> : null} · {option.contextMode === "fresh" ? text("context_fresh") : text("context_accumulated")}</p></div>
              <button className="button button-secondary button-inline" type="button" disabled={!selectedTeamTask || !data.meta.writesEnabled || activeAction !== null}
                onClick={() => { if (!selectedTeamTask) return; prepareLaunch(selectedTeamTask.taskId); if (pendingLaunchRef.current?.input.taskId !== selectedTeamTask.taskId) setRun((current) => ({ ...current, agentId: option.id })); }}>{text("shell_use_role")}</button>
            </li>)}</ul>
            {roleOptions.length === 0 ? <p className="empty-guidance">{text("shell_no_active_roles")}</p> : null}
          <WorkflowCard ready={roleOptions.length > 0} title={text("step_role")}>
            <p className="small-copy">{text("role_help")}</p>
            <form id="hire-role-form" noValidate onSubmit={submitRole}>
              <fieldset disabled={!configured || !data.meta.writesEnabled || activeAction !== null}>
                <SpecializationPicker library={libraryState} value={role.specializationRef} selectedName={role.specializationName} text={text} onChange={(selected) => chooseSpecialization(selected)} onRefresh={() => setLibraryRefresh((current) => current + 1)} />
                {roleErrors.has("specialization") ? <p className="field-error">{text("library_selection_unavailable")}</p> : null}
                <details className="role-legacy-presets"><summary>{text("role_legacy_presets")}</summary>
                <RolePresetPicker
                  presets={presetOptions}
                  value={role.fromPresetId}
                  text={text}
                  onChange={(id) => setRole((current) => ({ ...chooseRolePreset(current, id, presetOptions), specializationRef: null, specializationName: "", provider: "" }))}
                />
                </details>
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
                <label htmlFor="role-provider">{text("role_provider")}</label>
                <select id="role-provider" disabled={role.fromPresetId !== ""} value={hireProvider} onChange={(event) => { const provider = event.currentTarget.value; setRole((current) => chooseRoleProvider(current, provider)); }}>
                  <option value="">{text("role_select_provider")}</option>{hireProviders.map((provider) => <option key={provider} value={provider}>{provider}</option>)}
                </select>
                <label htmlFor="role-profile">{text("role_profile")}</label>
                <select
                  aria-describedby={validation([...roleErrors], "profile") ? "role-profile-error" : undefined}
                  aria-invalid={validation([...roleErrors], "profile")}
                  disabled={role.fromPresetId !== ""}
                  id="role-profile"
                  onChange={(event) => { const profileId = event.currentTarget.value; setRole((current) => chooseRoleProfile(current, profileId)); }}
                  value={role.profileId}
                >
                  <option value="">{text("select_profile")}</option>
                  {hireProfiles.map((profile) => <option key={profile.id} value={profile.id} disabled={profile.configured === false}>{profile.id.endsWith("-independent-reviewer") ? `${text("shell_profile_reviewer")} · ` : profile.id.endsWith("-builder") ? `${text("shell_profile_builder")} · ` : ""}{profile.label}</option>)}
                </select>
                {validation([...roleErrors], "profile") ? <p className="field-error" id="role-profile-error">{text("form_error_profile")}</p> : null}
                {profileOptions.length === 0 && configured ? <p className="field-error">{text("no_profiles")}</p> : null}
                {!role.fromPresetId ? <><label htmlFor="role-model-mode">{text("role_model")}</label><select id="role-model-mode" value={role.modelMode} onChange={(event) => { const modelMode = event.currentTarget.value as "profile" | "explicit"; setRole((current) => ({ ...current, modelMode })); }}><option value="profile">{text("role_model_profile")}</option><option value="explicit">{text("role_model_explicit")}</option></select>
                  {role.modelMode === "explicit" ? <><label htmlFor="role-model">{text("role_model")}</label><input id="role-model" list="role-model-offers" value={role.model} aria-invalid={roleErrors.has("model")} aria-describedby="role-model-help" onChange={(event) => { const model = event.currentTarget.value; setRole((current) => ({ ...current, model })); }} /><datalist id="role-model-offers">{modelOffers.map((model) => <option key={model} value={model} />)}</datalist>{roleErrors.has("model") ? <p className="field-error">{text("role_model_error")}</p> : null}</> : <p className="small-copy">{text("role_model_current")} <code>{selectedHireProfile?.model ?? text("role_model_unspecified")}</code></p>}
                  <p className="small-copy" id="role-model-help">{text("role_model_help")}</p></> : null}
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
            {workspaceInitialized ? <LibrarySection key={route.projectId ?? "legacy"} api={apiRef.current} text={text} writesEnabled={data.meta.writesEnabled}
              libraryApi={libraryApiRef.current} libraryState={libraryState} onRefreshLibrary={() => setLibraryRefresh((current) => current + 1)} onChooseRole={(selected) => chooseSpecialization(selected, true)}
              profiles={profileOptions} locale={locale} projectId={route.projectId} onBlueprintApplied={(application) => {
                if (!projectSelectionRef.current.isCurrent(renderedProjectGeneration) || routeRef.current.projectId !== renderedProjectId) return;
                const first = application.tasks[0];
                if (first && navigationRevision.current === renderedNavigationRevision && routeRef.current.view === "library") {
                  navigate({ view: "work", taskId: first.taskId }); setLaunchOpen(false);
                  if (!pendingLaunchRef.current) setRun({ ...emptyRun, taskId: first.taskId, agentId: first.agentId });
                }
                void refresh();
              }}
              tab={route.libraryTab} onTabChange={(libraryTab) => navigate({ libraryTab })}
              onApplied={async () => {
                const updated = await renderedProjectApi.load(new AbortController().signal);
                if (projectSelectionRef.current.isCurrent(renderedProjectGeneration) && routeRef.current.projectId === renderedProjectId) {
                  setState((current) => current.kind === "ready" ? { ...current, data: updated } : current);
                }
              }}
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
                    onClick={() => { const api = apiRef.current; void perform("initialize", api, (signal) => api.setup("initialize", signal), "action_complete"); }}
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
