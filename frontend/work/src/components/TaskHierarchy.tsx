import { type ReactElement, useId, useMemo, useRef, useState } from "react";
import type { TrackerTask } from "../contracts.js";
import type { MessageKey } from "../i18n.js";
import { buildHierarchyGraph, hierarchyRows } from "../taskHierarchy.js";
import { fitGraphZoom, graphViewport, NODE_HEIGHT, NODE_WIDTH } from "../taskGraph.js";
import { TaskState } from "./TaskInspector.js";

export function TaskHierarchy({ tasks, selectedTaskId, onSelectTask, text }: {
  tasks: readonly TrackerTask[]; selectedTaskId: string | null;
  onSelectTask: (id: string) => void; text: (key: MessageKey) => string;
}): ReactElement {
  const [focus, setFocus] = useState(true), [search, setSearch] = useState(""), [zoom, setZoom] = useState(1);
  const scroll = useRef<HTMLDivElement>(null), buttons = useRef(new Map<string, HTMLButtonElement>());
  const searchId = useId(), marker = useId().replace(/:/g, "");
  const layout = useMemo(() => buildHierarchyGraph(tasks, selectedTaskId, focus, search), [tasks, selectedTaskId, focus, search]);
  const { rows } = hierarchyRows(layout.selectedTasks);
  const viewport = graphViewport(layout.width, layout.height, zoom);
  const unavailable = layout.tooLarge || layout.invalid || !layout.nodes.length;
  function nodeText(task: TrackerTask): ReactElement {
    return <><span className="small-copy">{text(task.taskKind === "component" ? "hierarchy_component" : "hierarchy_task")}</span>
      <strong className="task-graph-node-title">{task.title}</strong><TaskState domain="task" value={task.taskState} text={text} /></>;
  }
  function list(): ReactElement {
    return <ul className="task-hierarchy-list">{rows.map(({ task, depth, parentOutside }) => <li key={task.taskId} className={`hierarchy-depth-${Math.min(depth, 6)}`}>
      <button type="button" className={`task-row-button${selectedTaskId === task.taskId ? " selected" : ""}`} aria-pressed={selectedTaskId === task.taskId} onClick={() => onSelectTask(task.taskId)}>
        {nodeText(task)}
        {task.parentTaskId ? <span className="small-copy">{parentOutside ? text("hierarchy_parent_missing") : text("hierarchy_parent")}: {tasks.find((item) => item.taskId === task.parentTaskId)?.title ?? task.parentTaskId}</span> : null}
      </button>
    </li>)}</ul>;
  }
  return <section className="task-graph task-hierarchy" aria-label={text("hierarchy_structure")}>
    <p className="small-copy">{text("hierarchy_help")}</p>
    <div className="task-graph-toolbar">
      <label className="task-graph-field" htmlFor={searchId}>{text("task_graph.search")}<input id={searchId} type="search" maxLength={256} value={search} onChange={(event) => setSearch(event.currentTarget.value)} /></label>
      <button type="button" className="button button-secondary" aria-pressed={focus} disabled={!selectedTaskId} onClick={() => setFocus(!focus)}>{text("hierarchy_focus")}</button>
      <div className="task-graph-zoom" role="group" aria-label={text("task_graph.zoom")}>
        <button type="button" className="button button-secondary" aria-label={text("task_graph.zoomOut")} disabled={unavailable || viewport.zoom <= .25} onClick={() => setZoom(viewport.zoom - .25)}>−</button>
        <output aria-label={text("task_graph.zoom")}>{Math.round(viewport.zoom * 100)}%</output>
        <button type="button" className="button button-secondary" aria-label={text("task_graph.zoomIn")} disabled={unavailable || viewport.zoom >= 2} onClick={() => setZoom(viewport.zoom + .25)}>+</button>
        <button type="button" className="button button-secondary" disabled={unavailable} onClick={() => { setZoom(fitGraphZoom(layout.width, scroll.current?.clientWidth ?? 0)); scroll.current?.scrollTo({ top: 0, left: 0 }); }}>{text("task_graph.fit")}</button>
      </div>
    </div>
    <p className="small-copy">{layout.selectedTasks.length}/{tasks.length} · {text("hierarchy_edge")}</p>
    {layout.hiddenParents ? <p role="status">{text("hierarchy_parent_missing")}: {layout.hiddenParents}</p> : null}
    {unavailable ? <><p role="status">{text(layout.tooLarge ? "task_graph.graphLimit" : layout.invalid ? "hierarchy_cycle" : "task_graph.noMatches")}</p>{list()}</> : <>
      <div className="task-graph-scroll" tabIndex={0} role="region" aria-label={text("hierarchy_structure")} ref={scroll}>
        <svg width={viewport.width} height={viewport.height} viewBox={`0 0 ${layout.width} ${layout.height}`} aria-label={text("hierarchy_structure")}>
          <defs><marker id={marker} markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M 0 0 L 8 4 L 0 8 Z" /></marker></defs>
          <g className="task-graph-edges task-hierarchy-edges" aria-hidden="true">{layout.edges.map((edge) => <path key={`${edge.from}:${edge.to}`} d={edge.path} markerEnd={`url(#${marker})`}><title>{text("hierarchy_edge")}</title></path>)}</g>
          {layout.nodes.map(({ id, task, x, y }) => <foreignObject key={id} x={x} y={y} width={NODE_WIDTH} height={NODE_HEIGHT}>
            {task === null ? <div className="task-graph-node task-hierarchy-root"><strong>{text("hierarchy_project_root")}</strong><span className="small-copy">{text("hierarchy_structure")}</span></div>
              : <button type="button" className="task-graph-node" data-task-kind={task.taskKind} data-graph-state={task.taskState} title={task.title} aria-pressed={task.taskId === selectedTaskId} onClick={() => onSelectTask(task.taskId)}
                ref={(button) => { if (button) buttons.current.set(id, button); else buttons.current.delete(id); }}
                onKeyDown={(event) => {
                  if (!["ArrowDown", "ArrowUp", "ArrowRight", "ArrowLeft", "Home", "End"].includes(event.key)) return;
                  event.preventDefault();
                  const nodes = layout.nodes.filter((node) => node.task !== null), index = nodes.findIndex((node) => node.id === id);
                  const next = event.key === "Home" ? 0 : event.key === "End" ? nodes.length - 1 : (index + (["ArrowDown", "ArrowRight"].includes(event.key) ? 1 : -1) + nodes.length) % nodes.length;
                  const button = buttons.current.get(nodes[next].id); button?.focus(); button?.scrollIntoView({ block: "nearest", inline: "nearest" });
                }}>{nodeText(task)}</button>}
          </foreignObject>)}
        </svg>
      </div>
      <details className="task-graph-list"><summary>{text("task_graph.list")}</summary>{list()}</details>
    </>}
  </section>;
}
