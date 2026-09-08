export type WorkView = "work" | "team" | "library" | "settings";
export type WorkFilter = "all" | "attention" | "ready" | "assigned" | "active" | "blocked" | "completed" | "review" | "accepted" | "cancelled";
export type WorkRoute = {
  projectId: string | null;
  view: WorkView;
  taskId: string | null;
  filter: WorkFilter;
  libraryTab: "roles" | "skills" | "blueprints" | "context" | "design";
  composer: boolean;
};

const views = new Set(["work", "team", "library", "settings"]);
const filters = new Set(["all", "attention", "ready", "assigned", "active", "blocked", "completed", "review", "accepted", "cancelled"]);
const tabs = new Set(["roles", "skills", "blueprints", "context", "design"]);
const taskId = /^task\.[0-7][0-9A-HJKMNP-TV-Z]{25}$/;
const projectId = /^project\.[a-f0-9]{32}$/;

export function parseWorkRoute(search: string): WorkRoute {
  const query = new URLSearchParams(search);
  const task = query.get("task");
  const project = query.get("project");
  const libraryTab = query.get("tab") === "templates" ? "blueprints" : query.get("tab");
  return {
    projectId: project !== null && projectId.test(project) ? project : null,
    view: views.has(query.get("view") ?? "") ? query.get("view") as WorkView : "work",
    taskId: task !== null && taskId.test(task) ? task : null,
    filter: filters.has(query.get("filter") ?? "") ? query.get("filter") as WorkFilter : "all",
    libraryTab: tabs.has(libraryTab ?? "") ? libraryTab as WorkRoute["libraryTab"] : "roles",
    composer: query.get("new") === "task"
  };
}

export function workRouteHref(route: WorkRoute): string {
  const query = new URLSearchParams();
  if (route.projectId !== null) query.set("project", route.projectId);
  if (route.view !== "work") query.set("view", route.view);
  if (route.taskId !== null) query.set("task", route.taskId);
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
