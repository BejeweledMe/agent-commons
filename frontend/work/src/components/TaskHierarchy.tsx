import { type KeyboardEvent as ReactKeyboardEvent, type ReactElement, useCallback, useId, useMemo, useRef, useState } from "react";
import type { TrackerTask } from "../contracts.js";
import type { MessageKey } from "../i18n.js";
import { buildHierarchyGraph, hierarchyRows } from "../taskHierarchy.js";
import { stateLabel } from "../taskPresentation.js";
import { NODE_HEIGHT, NODE_WIDTH } from "../taskGraph.js";
import { viewportKey, type Extent, type MapViewportStore, type Viewport } from "../mapViewport.js";
import type { MapRect } from "../mapZoomBands.js";
import { MapCanvas, type MapCanvasHandle } from "./MapCanvas.js";
import { TaskState } from "./TaskInspector.js";

/**
 * Containment, drawn on its own canvas. Structure and Dependencies stay two
 * different questions: this one reads explicit parents only and never a
 * readiness edge, and each keeps its own reading position.
 */
export function TaskHierarchy({ tasks, selectedTaskId, onSelectTask, onFocusTask, text, viewports, projectId, selectionKey = "", registerTaskButton }: {
  tasks: readonly TrackerTask[]; selectedTaskId: string | null;
  onSelectTask: (id: string) => void;
  onFocusTask?: (id: string) => void;
  text: (key: MessageKey) => string;
  viewports?: MapViewportStore;
  projectId?: string | null;
  selectionKey?: string;
  registerTaskButton?: (id: string, element: HTMLButtonElement | null) => void;
}): ReactElement {
  const [focus, setFocus] = useState(false), [search, setSearch] = useState("");
  const [anchor, setAnchor] = useState<string | null>(null);
  const buttons = useRef(new Map<string, HTMLButtonElement>());
  const canvas = useRef<MapCanvasHandle>(null);
  const searchId = useId(), marker = useId().replace(/:/g, "");
  const layout = useMemo(() => buildHierarchyGraph(tasks, anchor, focus, search), [tasks, anchor, focus, search]);
  const { rows } = hierarchyRows(layout.selectedTasks);
  const unavailable = layout.tooLarge || layout.invalid || !layout.nodes.length;
  const key = viewportKey({ projectId: projectId ?? null, kind: "structure", focus: JSON.stringify([focus, anchor, search, selectionKey]) });
  const [, repaint] = useState(0);
  const stored = viewports?.get(key) ?? null;
  const change = useCallback((next: Viewport) => {
    viewports?.set(key, next);
    repaint((value) => value + 1);
  }, [viewports, key]);
  function nodeText(task: TrackerTask): ReactElement {
    return <><span className="small-copy">{text(task.taskKind === "component" ? "hierarchy_component" : "hierarchy_task")}</span>
      <strong className="task-graph-node-title">{task.title}</strong><TaskState domain="task" value={task.taskState} text={text} plain /></>;
  }
  function list(): ReactElement {
    return <ul className="task-hierarchy-list">{rows.map(({ task, depth, parentOutside }) => <li key={task.taskId} className={`hierarchy-depth-${Math.min(depth, 6)}`}>
      <button type="button" ref={unavailable ? (element) => registerTaskButton?.(task.taskId, element) : undefined} className={`task-row-button${selectedTaskId === task.taskId ? " selected" : ""}`} aria-pressed={selectedTaskId === task.taskId} onClick={() => onSelectTask(task.taskId)}>
        {nodeText(task)}
        {task.parentTaskId ? <span className="small-copy">{parentOutside ? text("hierarchy_parent_missing") : text("hierarchy_parent")}: {tasks.find((item) => item.taskId === task.parentTaskId)?.title ?? task.parentTaskId}</span> : null}
      </button>
    </li>)}</ul>;
  }
  // Structure gets the same reveal as Dependencies: minimal pan, no document
  // scroll, no zoom change, and nothing at all for an already visible node.
  const content: Extent = { width: layout.width, height: layout.height };
  const rectOf = (id: string | null): MapRect | null => {
    const node = layout.nodes.find((item) => item.id === id);
    return node ? { x: node.x, y: node.y, width: NODE_WIDTH, height: NODE_HEIGHT } : null;
  };
  const reveal = (id: string): void => {
    const rect = rectOf(id);
    if (rect) canvas.current?.reveal(rect);
  };
  const workingZoom = (): void => {
    const first = layout.nodes.find((node) => node.task !== null) ?? layout.nodes[0] ?? null;
    const rect = rectOf(selectedTaskId) ?? (first ? { x: first.x, y: first.y, width: NODE_WIDTH, height: NODE_HEIGHT } : null);
    canvas.current?.workingZoom(rect);
  };
  const moveFocus = (id: string) => (event: ReactKeyboardEvent<HTMLButtonElement>): void => {
    if (!["ArrowDown", "ArrowUp", "ArrowRight", "ArrowLeft", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const nodes = layout.nodes.filter((node) => node.task !== null), index = nodes.findIndex((node) => node.id === id);
    const next = event.key === "Home" ? 0 : event.key === "End" ? nodes.length - 1 : (index + (["ArrowDown", "ArrowRight"].includes(event.key) ? 1 : -1) + nodes.length) % nodes.length;
    onFocusTask?.(nodes[next].id);
    buttons.current.get(nodes[next].id)?.focus({ preventScroll: true });
    reveal(nodes[next].id);
  };
  const selectedTask = tasks.find((task) => task.taskId === selectedTaskId) ?? null;
  const strip = <>
    {selectedTask === null ? <p className="map-selected-empty">{text("task_graph.selectNode")}</p> : <>
      <span className="map-selected-state"><TaskState domain="task" value={selectedTask.taskState} text={text} plain /></span>
      <strong className="map-selected-title">{selectedTask.title || selectedTask.taskId}</strong>
      <button type="button" className="button button-secondary button-inline" onClick={() => onSelectTask(selectedTask.taskId)}>{text("task_graph.openSelected")}</button>
    </>}
    <button type="button" className="button button-secondary button-inline" title={text("task_graph.workingZoomHelp")}
      disabled={layout.nodes.length === 0} onClick={workingZoom}>{text("task_graph.workingZoom")}</button>
  </>;
  const toolbar = <>
    <label className="task-graph-field" htmlFor={searchId}>{text("task_graph.search")}<input id={searchId} type="search" maxLength={256} value={search} onChange={(event) => setSearch(event.currentTarget.value)} /></label>
    <button type="button" className="button button-secondary button-inline" aria-pressed={focus} disabled={!selectedTaskId} onClick={() => { setFocus(true); setAnchor(selectedTaskId); }}>{text("hierarchy_focus")}</button>
    {focus ? <button type="button" className="button button-secondary button-inline" onClick={() => { setFocus(false); setAnchor(null); }}>{text("task_graph.all")}</button> : null}
  </>;
  const footer = <>
    <p className="small-copy">{layout.selectedTasks.length}/{tasks.length} · {text("hierarchy_edge")}</p>
    {layout.hiddenParents ? <p role="status">{text("hierarchy_parent_missing")}: {layout.hiddenParents}</p> : null}
    <details className="task-graph-list"><summary>{text("task_graph.list")}</summary>{list()}</details>
  </>;
  return <section className="task-graph task-hierarchy" aria-label={text("hierarchy_structure")}>
    <p className="visually-hidden">{text("hierarchy_help")}</p>
    {unavailable ? <>
      <div className="task-graph-toolbar">{toolbar}</div>
      <p role="status">{text(layout.tooLarge ? "task_graph.graphLimit" : layout.invalid ? "hierarchy_cycle" : "task_graph.noMatches")}</p>
      {list()}
    </> : <MapCanvas ref={canvas}
      labels={{ options: text("task_graph.options"), help: text("task_graph.help"), canvas: text("hierarchy_structure"), gestures: text("task_graph.gestures"), zoom: text("task_graph.zoom"),
        zoomIn: text("task_graph.zoomIn"), zoomOut: text("task_graph.zoomOut"), fit: text("task_graph.fit"), reset: text("task_graph.reset"), fitHint: text("task_graph.fitHint") }}
      content={content} viewport={stored} onViewportChange={change}
      toolbar={toolbar} strip={strip} footer={footer}
      defs={<defs><marker id={marker} markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M 0 0 L 8 4 L 0 8 Z" /></marker></defs>}>
      <g className="task-graph-edges task-hierarchy-edges" aria-hidden="true">{layout.edges.map((edge) => <path key={`${edge.from}:${edge.to}`} d={edge.path} markerEnd={`url(#${marker})`}><title>{text("hierarchy_edge")}</title></path>)}</g>
      {layout.nodes.map(({ id, task, x, y }) => <foreignObject key={id} x={x} y={y} width={NODE_WIDTH} height={NODE_HEIGHT}>
        {task === null ? <div className="task-graph-node task-hierarchy-root"><strong>{text("hierarchy_project_root")}</strong><span className="small-copy">{text("hierarchy_structure")}</span></div>
          : <button type="button" className="task-graph-node" data-task-kind={task.taskKind} data-graph-state={task.taskState} title={task.title} aria-label={`${task.title || task.taskId} — ${text(stateLabel("task", task.taskState))}`} aria-pressed={task.taskId === selectedTaskId} onClick={() => onSelectTask(task.taskId)}
            ref={(button) => { if (button) buttons.current.set(id, button); else buttons.current.delete(id); registerTaskButton?.(id, button); }}
            onKeyDown={moveFocus(id)}>{nodeText(task)}</button>}
      </foreignObject>)}
    </MapCanvas>}
  </section>;
}
