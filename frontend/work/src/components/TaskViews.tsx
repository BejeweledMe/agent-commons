import { type KeyboardEvent, type ReactElement, type ReactNode, useRef, useState } from "react";
import { TASKS_VIEWS, type TasksView } from "../appRouteState.js";
import type { TrackerSnapshot, TrackerTask } from "../contracts";
import type { Locale, MessageKey } from "../i18n";
import type { MapViewportStore } from "../mapViewport.js";
import { taskGraphText } from "../taskGraphStrings.js";
import { NOW_COLUMNS, nowColumnEmpty, nowColumnLabel, type NowBuckets } from "../taskPresentation.js";
import { OutputsButton } from "./OutputsPanel.js";
import { TaskHierarchy } from "./TaskHierarchy.js";
import { TaskGraph } from "./TaskGraph.js";
import { TaskState } from "./TaskInspector.js";

const tabLabel: Readonly<Record<TasksView, MessageKey>> = {
  now: "task_view_tab_now", map: "task_view_tab_map", all: "task_view_tab_all"
};

type Props = {
  filterControl?: ReactNode;
  view: TasksView;
  onViewChange: (view: TasksView) => void;
  snapshot: TrackerSnapshot;
  /** Status filter, search and role filter applied; drives Map and All tasks. */
  visibleTasks: readonly TrackerTask[];
  /** The Now columns: the snapshot narrowed by the role filter only. */
  buckets: NowBuckets;
  selectedTaskId: string | null;
  locale: Locale;
  text: (key: MessageKey) => string;
  onSelectTask: (taskId: string) => void;
  /** Move the selection without opening a panel over the map. */
  onFocusTask?: (taskId: string) => void;
  onTaskKeyDown: (event: KeyboardEvent<HTMLButtonElement>, taskId: string) => void;
  registerTaskButton: (taskId: string, element: HTMLButtonElement | null) => void;
  onClearFilters: () => void;
  /** Reading positions, kept per project, map kind and drawing. */
  viewports?: MapViewportStore;
  projectId?: string | null;
  selectionKey?: string;
};

/**
 * The three presentations of the work area: Map, Now and All tasks. Map is the
 * default and owns the full height of the pane; exactly one presentation is in
 * the DOM at a time. Map owns its scoped capacity check so a small selected
 * component remains navigable in a large project.
 */
export function TaskViews({ filterControl, view, onViewChange, snapshot, visibleTasks, buckets, selectedTaskId, locale, text, onSelectTask, onFocusTask, onTaskKeyDown, registerTaskButton, onClearFilters, viewports, projectId, selectionKey }: Props): ReactElement {
  const [mapKind, setMapKind] = useState<"structure" | "dependencies">("dependencies");
  const tabs = useRef(new Map<TasksView, HTMLButtonElement>());
  const t = (key: Parameters<typeof taskGraphText>[1]): string => taskGraphText(locale, key);

  function moveTabFocus(event: KeyboardEvent<HTMLButtonElement>): void {
    if (!["ArrowRight", "ArrowDown", "ArrowLeft", "ArrowUp", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const current = TASKS_VIEWS.indexOf(view);
    const index = event.key === "Home" ? 0 : event.key === "End" ? TASKS_VIEWS.length - 1
      : event.key === "ArrowRight" || event.key === "ArrowDown" ? (current + 1) % TASKS_VIEWS.length
        : (current - 1 + TASKS_VIEWS.length) % TASKS_VIEWS.length;
    const next = TASKS_VIEWS[index];
    onViewChange(next);
    tabs.current.get(next)?.focus();
  }

  function taskRow(task: TrackerTask): ReactElement {
    return <li key={task.taskId}>
      <button className={`task-row-button${task.taskId === selectedTaskId ? " selected" : ""}${task.awaitsHuman ? " attention" : ""}`}
        aria-pressed={task.taskId === selectedTaskId} type="button" onClick={() => onSelectTask(task.taskId)} onKeyDown={(event) => onTaskKeyDown(event, task.taskId)}
        ref={(element) => { registerTaskButton(task.taskId, element); }}>
        <span className="task-row-title">{task.title || task.taskId}</span>
        <span className="task-row-meta"><span><span className="task-domain-label">{text("tracker_task_label")}: </span><TaskState domain="task" value={task.taskState} text={text} /></span><span className="task-row-role">{task.roleName ?? text("inspector_unassigned")}</span></span>
        <span className="task-row-readiness"><span className="task-domain-label">{text("tracker_readiness_label")}: </span><TaskState domain="readiness" value={task.readiness} text={text} /></span>
      </button>
      <OutputsButton scope={{ kind: "task", id: task.taskId }} title={task.title || task.taskId} />
    </li>;
  }

  /** The All tasks list; Map owns its bounded selection and list equivalent. */
  function taskList(): ReactElement {
    return <ul className="task-list" aria-describedby="tracker-keyboard-help">{visibleTasks.map(taskRow)}</ul>;
  }

  function filteredToNothing(): ReactElement {
    return <div className="task-view-empty">
      <p>{text("task_view_empty_filter")}</p>
      <button className="button button-secondary" type="button" onClick={onClearFilters}>{text("task_view_clear_filter")}</button>
    </div>;
  }

  function nowView(): ReactElement {
    return <>
      <div className="now-columns">
        {NOW_COLUMNS.map((column) => <section className="now-column" key={column} aria-labelledby={`now-column-${column}`}>
          <h3 id={`now-column-${column}`}>{text(nowColumnLabel[column])} <span className="task-list-count">{buckets[column].length}</span></h3>
          {buckets[column].length === 0
            ? <p className="now-column-empty">{text(nowColumnEmpty[column])}</p>
            : <ul className="now-column-list" aria-describedby="tracker-keyboard-help">{buckets[column].map(taskRow)}</ul>}
        </section>)}
      </div>
      {buckets.settled.length > 0 ? <p className="now-settled small-copy">
        {text("task_view_now_settled")} <span className="task-list-count">{buckets.settled.length}</span>
        <button type="button" className="notice-link" onClick={() => { onClearFilters(); onViewChange("all"); }}>{text("task_view_tab_all")}</button>
      </p> : null}
    </>;
  }

  function mapView(): ReactElement {
    if (visibleTasks.length === 0 && snapshot.tasks.length > 0) return filteredToNothing();
    return <>
      {visibleTasks.length !== snapshot.tasks.length ? <p className="visually-hidden">{t("filtered")}</p> : null}
      {mapKind === "structure" ? <TaskHierarchy tasks={visibleTasks} selectedTaskId={selectedTaskId} onSelectTask={onSelectTask} onFocusTask={onFocusTask} text={text} viewports={viewports} projectId={projectId} selectionKey={selectionKey} registerTaskButton={registerTaskButton} />
        : <TaskGraph tasks={visibleTasks} edges={snapshot.edges} selectedTaskId={selectedTaskId} onSelectTask={onSelectTask} onFocusTask={onFocusTask} locale={locale} viewports={viewports} projectId={projectId} selectionKey={selectionKey} registerTaskButton={registerTaskButton} />}
    </>;
  }

  function allView(): ReactElement {
    if (visibleTasks.length === 0 && snapshot.tasks.length > 0) return filteredToNothing();
    return taskList();
  }

  return <>
    <div className="task-view-navigation">{filterControl}
    <div className="task-view-tabs" role="tablist" aria-label={text("task_view_tabs_label")}>
      {TASKS_VIEWS.map((value) => <button className="button button-secondary" key={value} type="button" role="tab"
        id={`task-view-tab-${value}`} aria-controls="task-view-panel" aria-selected={view === value} tabIndex={view === value ? 0 : -1}
        onClick={() => onViewChange(value)} onKeyDown={moveTabFocus}
        ref={(element) => { if (element) tabs.current.set(value, element); else tabs.current.delete(value); }}>
        {text(tabLabel[value])}
      </button>)}
    </div>
    {view === "map" ? <label className="map-kind-control"><span className="visually-hidden">{text("hierarchy_view")}</span><select value={mapKind} onChange={(event) => setMapKind(event.currentTarget.value as "structure" | "dependencies")}><option value="structure">{text("hierarchy_structure")}</option><option value="dependencies">{text("map_dependencies_short")}</option></select></label> : null}
    </div>
    <p className="visually-hidden" id="tracker-keyboard-help">{text("tracker_keyboard_help")}</p>
    <div className={`task-view-panel task-view-panel-${view}`} id="task-view-panel" role="tabpanel" aria-labelledby={`task-view-tab-${view}`}>
      {snapshot.state === "empty" ? <p>{text("tracker_empty")}</p>
        : view === "now" ? nowView() : view === "map" ? mapView() : allView()}
    </div>
  </>;
}
