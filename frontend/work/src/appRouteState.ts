export type WorkView = "work" | "team" | "library" | "settings";
export type WorkFilter = "all" | "attention" | "ready" | "assigned" | "active" | "blocked" | "completed" | "review" | "accepted" | "cancelled";
export type WorkRoute = {
  view: WorkView;
  taskId: string | null;
  filter: WorkFilter;
  libraryTab: "templates" | "context" | "design";
  composer: boolean;
};

const views = new Set(["work", "team", "library", "settings"]);
const filters = new Set(["all", "attention", "ready", "assigned", "active", "blocked", "completed", "review", "accepted", "cancelled"]);
const tabs = new Set(["templates", "context", "design"]);
const taskId = /^task\.[0-7][0-9A-HJKMNP-TV-Z]{25}$/;

export function parseWorkRoute(search: string): WorkRoute {
  const query = new URLSearchParams(search);
  const task = query.get("task");
  return {
    view: views.has(query.get("view") ?? "") ? query.get("view") as WorkView : "work",
    taskId: task !== null && taskId.test(task) ? task : null,
    filter: filters.has(query.get("filter") ?? "") ? query.get("filter") as WorkFilter : "all",
    libraryTab: tabs.has(query.get("tab") ?? "") ? query.get("tab") as WorkRoute["libraryTab"] : "templates",
    composer: query.get("new") === "task"
  };
}

export function workRouteHref(route: WorkRoute): string {
  const query = new URLSearchParams();
  if (route.view !== "work") query.set("view", route.view);
  if (route.taskId !== null) query.set("task", route.taskId);
  if (route.filter !== "all") query.set("filter", route.filter);
  if (route.libraryTab !== "templates") query.set("tab", route.libraryTab);
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
