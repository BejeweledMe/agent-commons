import { translate } from "../i18n.js";
import { stateLabel } from "../taskPresentation.js";
import { Icon, type IconName } from "./Icon.js";
import { type KeyboardEvent as ReactKeyboardEvent, type ReactElement, useCallback, useId, useMemo, useRef, useState } from "react";
import type { TrackerEdge, TrackerTask } from "../contracts";
import type { Locale } from "../i18n";
import { buildTaskGraph, graphTaskState, GRAPH_ICONS, GRAPH_STATES, NODE_HEIGHT, NODE_WIDTH, type GraphFocus } from "../taskGraph.js";
import { viewportKey, type Extent, type MapViewportStore, type Viewport } from "../mapViewport.js";
import type { MapRect } from "../mapZoomBands.js";
import { taskGraphText } from "../taskGraphStrings.js";
import { MapCanvas, type MapCanvasHandle } from "./MapCanvas.js";

export function TaskGraph({ tasks, edges, selectedTaskId, onSelectTask, onFocusTask, locale, viewports, projectId, selectionKey = "", registerTaskButton }: {
  tasks: readonly TrackerTask[]; edges: readonly TrackerEdge[]; selectedTaskId: string | null;
  onSelectTask: (id: string) => void;
  /** Keyboard movement between nodes selects without opening a panel. */
  onFocusTask?: (id: string) => void;
  locale: Locale;
  viewports?: MapViewportStore;
  projectId?: string | null;
  selectionKey?: string;
  registerTaskButton?: (id: string, element: HTMLButtonElement | null) => void;
}): ReactElement {
  const [focus, setFocus] = useState<GraphFocus>("all");
  const [anchor, setAnchor] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const marker = useId().replace(/:/g, ""), searchId = useId(), focusId = useId();
  const buttons = useRef(new Map<string, HTMLButtonElement>());
  const canvas = useRef<MapCanvasHandle>(null);
  const layout = useMemo(() => buildTaskGraph(tasks, edges, anchor, focus, search), [tasks, edges, anchor, focus, search]);
  const t = (key: Parameters<typeof taskGraphText>[1]): string => taskGraphText(locale, key);
  const canFocus = tasks.some((task) => task.taskId === selectedTaskId);
  const unavailable = layout.tooLarge || layout.invalid || layout.nodes.length === 0;
  // The reading position belongs to this exact content: an automatic refresh
  // that changes nothing about the drawing leaves the viewport untouched.
  const key = viewportKey({ projectId: projectId ?? null, kind: "dependencies", focus: JSON.stringify([focus, anchor, search, selectionKey]) });
  const [, repaint] = useState(0);
  const stored = viewports?.get(key) ?? null;
  const change = useCallback((next: Viewport) => {
    viewports?.set(key, next);
    repaint((value) => value + 1);
  }, [viewports, key]);
  const content: Extent = { width: layout.width, height: layout.height };
  const rectOf = (taskId: string | null): MapRect | null => {
    const node = layout.nodes.find((item) => item.task.taskId === taskId);
    return node ? { x: node.x, y: node.y, width: NODE_WIDTH, height: NODE_HEIGHT } : null;
  };
  /**
   * Keyboard focus reveal: the smallest pan that brings the node inside the
   * frame, and nothing at all when it is already visible. The document is
   * never scrolled, and the zoom is never changed.
   */
  const reveal = (taskId: string): void => {
    const rect = rectOf(taskId);
    if (rect) canvas.current?.reveal(rect);
  };
  const workingZoom = (): void => {
    const first = layout.nodes[0];
    const rect = rectOf(selectedTaskId) ?? (first ? { x: first.x, y: first.y, width: NODE_WIDTH, height: NODE_HEIGHT } : null);
    canvas.current?.workingZoom(rect);
  };
  const moveFocus = (taskId: string) => (event: ReactKeyboardEvent<HTMLButtonElement>): void => {
    if (!["ArrowDown", "ArrowUp", "ArrowRight", "ArrowLeft", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const index = layout.nodes.findIndex((node) => node.task.taskId === taskId);
    const next = event.key === "Home" ? 0 : event.key === "End" ? layout.nodes.length - 1
      : (index + (event.key === "ArrowDown" || event.key === "ArrowRight" ? 1 : -1) + layout.nodes.length) % layout.nodes.length;
    const nextId = layout.nodes[next].task.taskId;
    onFocusTask?.(nextId);
    buttons.current.get(nextId)?.focus({ preventScroll: true });
    reveal(nextId);
  };
  function list(): ReactElement {
    return <ul className="task-graph-fallback">{layout.selectedTasks.map((task) => <li key={task.taskId}><button type="button" className="button button-secondary" ref={unavailable ? (element) => registerTaskButton?.(task.taskId, element) : undefined} aria-pressed={task.taskId === selectedTaskId} onClick={() => onSelectTask(task.taskId)}>{task.title || task.taskId} · {t(graphTaskState(task))}</button></li>)}</ul>;
  }
  const toolbar = <>
    <label className="task-graph-field" htmlFor={searchId}>{t("search")}<input id={searchId} type="search" value={search} maxLength={256} onChange={(event) => setSearch(event.currentTarget.value)} /></label>
    <label className="task-graph-field" htmlFor={focusId}>{t("focusLabel")}<select id={focusId} value={focus} onChange={(event) => { const next = event.currentTarget.value as GraphFocus; setFocus(next); setAnchor(next === "all" ? null : selectedTaskId); }}>
      <option value="all">{t("all")}</option>
      <option value="connected" disabled={!canFocus}>{t("focus")}</option>
      <option value="upstream" disabled={!canFocus}>{t("upstream")}</option>
      <option value="downstream" disabled={!canFocus}>{t("downstream")}</option>
    </select></label>
    <button type="button" className="button button-secondary button-inline" disabled={!canFocus} onClick={() => { setFocus(focus === "all" ? "connected" : focus); setAnchor(selectedTaskId); }}>{t("focus")}</button>
  </>;
  // The selected element at document scale. On a far or overview reading the
  // node's own secondary lines are hidden, so this strip — not an eight-button
  // row drawn too small to read — is where its actions stay reachable.
  const selectedTask = tasks.find((task) => task.taskId === selectedTaskId) ?? null;
  const strip = <>
    {selectedTask === null ? <p className="map-selected-empty">{t("selectNode")}</p> : <>
      <span className="map-selected-state" data-graph-state={graphTaskState(selectedTask)}>{t(graphTaskState(selectedTask))}</span>
      <strong className="map-selected-title">{selectedTask.title || selectedTask.taskId}</strong>
      <button type="button" className="button button-secondary button-inline" onClick={() => onSelectTask(selectedTask.taskId)}>{t("openSelected")}</button>
    </>}
    <button type="button" className="button button-secondary button-inline" title={t("workingZoomHelp")}
      disabled={layout.nodes.length === 0} onClick={workingZoom}>{t("workingZoom")}</button>
  </>;
  const footer = <>
    <details className="task-graph-list"><summary>{t("legend")}</summary><ul className="task-graph-legend" aria-label={t("legend")}>{GRAPH_STATES.map((state) => <li data-graph-state={state} key={state}><span aria-hidden="true">{<Icon name={({ accepted: "permissions", completed: "check", review: "search", active: "run", ready: "tasks", queued: "dots", blocked: "alert", cancelled: "close", unknown: "info" } as Record<string, IconName>)[state]} size={14} />}</span> {t(state)}</li>)}</ul></details>
    <p className="small-copy">{layout.selectedTasks.length}/{layout.total} {t("selectedCount")} · {t("edgeMeaning")}</p>
    {layout.hiddenPrerequisites > 0 ? <p className="task-graph-boundary" role="status">{t("hiddenPrerequisites")}: {layout.hiddenPrerequisites}</p> : null}
    <details className="task-graph-list"><summary>{t("list")}</summary>{list()}</details>
    <p className="visually-hidden">{t("keyboard")}</p>
  </>;
  return <section className="task-graph task-graph-dependencies" aria-label={t("graph")}>
    <p className="visually-hidden">{t("graphHelp")}</p>
    {unavailable ? <>
      <div className="task-graph-toolbar">{toolbar}</div>
      <p className="inspector-notice" role="status">{t(layout.tooLarge ? "graphLimit" : layout.invalid ? "graphInvalid" : "noMatches")}</p>
      {layout.nodes.length === 0 && !layout.tooLarge && !layout.invalid ? null : list()}
    </> : <MapCanvas ref={canvas}
      labels={{ options: t("options"), help: t("help"), canvas: t("graph"), gestures: t("gestures"), zoom: t("zoom"), zoomIn: t("zoomIn"), zoomOut: t("zoomOut"), fit: t("fit"), reset: t("reset"), fitHint: t("fitHint") }}
      content={content} viewport={stored} onViewportChange={change}
      toolbar={toolbar} strip={strip} footer={footer}
      defs={<defs><marker id={marker} markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M 0 0 L 8 4 L 0 8 Z" /></marker></defs>}>
      <g className="task-graph-edges" aria-hidden="true">{layout.edges.map((edge) => <path key={`${edge.from}:${edge.to}`} d={edge.path} markerEnd={`url(#${marker})`}><title>{t("edgeMeaning")}</title></path>)}</g>
      {layout.nodes.map(({ task, x, y, state, hiddenPrerequisites }) => <foreignObject key={task.taskId} x={x} y={y} width={NODE_WIDTH} height={NODE_HEIGHT}>
        <button type="button" className="task-graph-node" data-graph-state={state} aria-pressed={task.taskId === selectedTaskId}
          title={task.title} aria-label={`${task.title || task.taskId} — ${t(state)}`} onClick={() => onSelectTask(task.taskId)}
          ref={(button) => { if (button) buttons.current.set(task.taskId, button); else buttons.current.delete(task.taskId); registerTaskButton?.(task.taskId, button); }}
          onKeyDown={moveFocus(task.taskId)}>
          <span className="task-graph-node-status"><span className="task-graph-node-icon" aria-hidden="true">{<Icon name={({ accepted: "permissions", completed: "check", review: "search", active: "run", ready: "tasks", queued: "dots", blocked: "alert", cancelled: "close", unknown: "info" } as Record<string, IconName>)[state]} size={14} />}</span>{t(state)}</span>
          <strong className="task-graph-node-title">{task.title || task.taskId}</strong>
          <span className="task-graph-node-role">{task.roleName
            ? `${task.roleName}${task.provider ? ` · ${task.provider}` : ""}`
            : task.suggestedRoleName
              ? `${t("suggested")}: ${task.suggestedRoleName}${task.suggestedProvider ? ` · ${task.suggestedProvider}` : ""}`
              : t("unassigned")}</span>
          <span className="task-graph-node-run">{task.phase ? translate(locale, stateLabel("run", task.phase)) : t("noRun")}</span>
          {hiddenPrerequisites > 0 ? <span className="task-graph-node-boundary">{t("hiddenPrerequisites")}: {hiddenPrerequisites}</span> : null}
        </button>
      </foreignObject>)}
    </MapCanvas>}
  </section>;
}
