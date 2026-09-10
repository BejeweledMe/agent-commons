export type WorkView = "board" | "work" | "library" | "settings";
export type WorkFilter = "all" | "attention" | "ready" | "assigned" | "active" | "blocked" | "completed" | "review" | "accepted" | "cancelled";
export type WorkRoute = {
  projectId: string | null;
  view: WorkView;
  taskId: string | null;
  /** A role whose tasks the tracker is narrowed to; navigation state only. */
  agentId: string | null;
  filter: WorkFilter;
  libraryTab: "roles" | "skills" | "blueprints" | "context";
  composer: boolean;
};

// The project board is the home view (ADR 0020). "team" survives as an alias so
// links from the task-first period still open the roles, now on the board.
const views = new Set(["board", "work", "library", "settings"]);
const filters = new Set(["all", "attention", "ready", "assigned", "active", "blocked", "completed", "review", "accepted", "cancelled"]);
const tabs = new Set(["roles", "skills", "blueprints", "context"]);
const taskId = /^task\.[0-7][0-9A-HJKMNP-TV-Z]{25}$/;
const agentId = /^agent\.[0-7][0-9A-HJKMNP-TV-Z]{25}$/;
const projectId = /^project\.[a-f0-9]{32}$/;

export function parseWorkRoute(search: string): WorkRoute {
  const query = new URLSearchParams(search);
  const task = query.get("task");
  const agent = query.get("agent");
  const project = query.get("project");
  const libraryTab = query.get("tab") === "templates" ? "blueprints" : query.get("tab");
  const requested = query.get("view") === "team" ? "board" : query.get("view");
  return {
    projectId: project !== null && projectId.test(project) ? project : null,
    // Legacy Design-gallery links carry a task, so they still open the tracker.
    view: requested === "library" && libraryTab === "design" ? "work" : views.has(requested ?? "") ? requested as WorkView : "board",
    taskId: task !== null && taskId.test(task) ? task : null,
    agentId: agent !== null && agentId.test(agent) ? agent : null,
    filter: filters.has(query.get("filter") ?? "") ? query.get("filter") as WorkFilter : "all",
    libraryTab: tabs.has(libraryTab ?? "") ? libraryTab as WorkRoute["libraryTab"] : "roles",
    composer: query.get("new") === "task"
  };
}

export function workRouteHref(route: WorkRoute): string {
  const query = new URLSearchParams();
  if (route.projectId !== null) query.set("project", route.projectId);
  if (route.view !== "board") query.set("view", route.view);
  if (route.taskId !== null) query.set("task", route.taskId);
  // The role filter belongs to the tracker; other views do not carry it forward.
  if (route.agentId !== null && route.view === "work") query.set("agent", route.agentId);
  if (route.filter !== "all") query.set("filter", route.filter);
  if (route.libraryTab !== "roles") query.set("tab", route.libraryTab);
  if (route.composer) query.set("new", "task");
  return `/work${query.size ? `?${query}` : ""}`;
}

export function sanitizedWorkLocation(_pathname: string, search: string): string {
  return workRouteHref(parseWorkRoute(search));
}

export function isEditingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  return target.closest("input, textarea, select, [contenteditable='true'], [role='textbox']") !== null;
}
