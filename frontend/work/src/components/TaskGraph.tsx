import { type ReactElement, useId, useMemo, useRef, useState } from "react";
import type { TrackerEdge, TrackerTask } from "../contracts";
import type { Locale } from "../i18n";
import { buildTaskGraph, fitGraphZoom, graphTaskState, graphViewport, GRAPH_ICONS, GRAPH_STATES, NODE_HEIGHT, NODE_WIDTH, type GraphFocus } from "../taskGraph.js";
import { taskGraphText } from "../taskGraphStrings.js";

export function TaskGraph({ tasks, edges, selectedTaskId, onSelectTask, locale }: {
  tasks: readonly TrackerTask[]; edges: readonly TrackerEdge[]; selectedTaskId: string | null;
  onSelectTask: (id: string) => void; locale: Locale;
}): ReactElement {
  const [focus, setFocus] = useState<GraphFocus>("connected");
  const [search, setSearch] = useState("");
  const [zoom, setZoom] = useState(1);
  const marker = useId().replace(/:/g, ""), searchId = useId(), focusId = useId();
  const buttons = useRef(new Map<string, HTMLButtonElement>()), scroll = useRef<HTMLDivElement>(null);
  const layout = useMemo(() => buildTaskGraph(tasks, edges, selectedTaskId, focus, search), [tasks, edges, selectedTaskId, focus, search]);
  const viewport = graphViewport(layout.width, layout.height, zoom);
  const t = (key: Parameters<typeof taskGraphText>[1]): string => taskGraphText(locale, key);
  const canFocus = tasks.some((task) => task.taskId === selectedTaskId);
  const unavailable = layout.tooLarge || layout.invalid || layout.nodes.length === 0;
  function list(): ReactElement {
    return <ul className="task-graph-fallback">{layout.selectedTasks.map((task) => <li key={task.taskId}><button type="button" className="button button-secondary" aria-pressed={task.taskId === selectedTaskId} onClick={() => onSelectTask(task.taskId)}>{task.title || task.taskId} · {t(graphTaskState(task))}</button></li>)}</ul>;
  }
  return <section className="task-graph" aria-label={t("graph")}>
    <p className="small-copy">{t("graphHelp")}</p>
    <div className="task-graph-toolbar">
      <label className="task-graph-field" htmlFor={searchId}>{t("search")}<input id={searchId} type="search" value={search} maxLength={256} onChange={(event) => setSearch(event.currentTarget.value)} /></label>
      <label className="task-graph-field" htmlFor={focusId}>{t("focusLabel")}<select id={focusId} value={canFocus ? focus : "all"} onChange={(event) => setFocus(event.currentTarget.value as GraphFocus)}>
        <option value="all">{t("all")}</option>
        <option value="connected" disabled={!canFocus}>{t("focus")}</option>
        <option value="upstream" disabled={!canFocus}>{t("upstream")}</option>
        <option value="downstream" disabled={!canFocus}>{t("downstream")}</option>
      </select></label>
      <div className="task-graph-zoom" role="group" aria-label={t("zoom")}>
        <button type="button" className="button button-secondary button-inline" disabled={unavailable || viewport.zoom <= 0.25} aria-label={t("zoomOut")} onClick={() => setZoom(graphViewport(layout.width, layout.height, viewport.zoom - 0.25).zoom)}>−</button>
        <output aria-label={t("zoom")}>{Math.round(viewport.zoom * 100)}%</output>
        <button type="button" className="button button-secondary button-inline" disabled={unavailable || viewport.zoom >= 2} aria-label={t("zoomIn")} onClick={() => setZoom(graphViewport(layout.width, layout.height, viewport.zoom + 0.25).zoom)}>+</button>
        <button type="button" className="button button-secondary button-inline" disabled={unavailable} onClick={() => { setZoom(fitGraphZoom(layout.width, scroll.current?.clientWidth ?? 0)); scroll.current?.scrollTo({ top: 0, left: 0 }); }}>{t("fit")}</button>
      </div>
    </div>
    <ul className="task-graph-legend" aria-label={t("legend")}>{GRAPH_STATES.map((state) => <li data-graph-state={state} key={state}><span aria-hidden="true">{GRAPH_ICONS[state]}</span> {t(state)}</li>)}</ul>
    <p className="small-copy">{layout.selectedTasks.length}/{layout.total} {t("selectedCount")} · {t("edgeMeaning")}</p>
    {layout.hiddenPrerequisites > 0 ? <p className="task-graph-boundary" role="status">{t("hiddenPrerequisites")}: {layout.hiddenPrerequisites}</p> : null}
    {layout.tooLarge || layout.invalid ? <>
      <p className="inspector-notice" role="status">{t(layout.tooLarge ? "graphLimit" : "graphInvalid")}</p>
      {list()}
    </> : layout.nodes.length === 0 ? <p role="status">{t("noMatches")}</p> : <>
      <div className="task-graph-scroll" tabIndex={0} role="region" aria-label={t("graph")} ref={scroll}>
        <svg width={viewport.width} height={viewport.height} viewBox={`0 0 ${layout.width} ${layout.height}`} aria-label={t("graph")}>
          <defs><marker id={marker} markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M 0 0 L 8 4 L 0 8 Z" /></marker></defs>
          <g className="task-graph-edges" aria-hidden="true">{layout.edges.map((edge) => <path key={`${edge.from}:${edge.to}`} d={edge.path} markerEnd={`url(#${marker})`}><title>{t("edgeMeaning")}</title></path>)}</g>
          {layout.nodes.map(({ task, x, y, state, hiddenPrerequisites }) => <foreignObject key={task.taskId} x={x} y={y} width={NODE_WIDTH} height={NODE_HEIGHT}>
            <button type="button" className="task-graph-node" data-graph-state={state} aria-pressed={task.taskId === selectedTaskId}
              title={task.title} onClick={() => onSelectTask(task.taskId)}
              ref={(button) => { if (button) buttons.current.set(task.taskId, button); else buttons.current.delete(task.taskId); }}
              onKeyDown={(event) => {
                if (!["ArrowDown", "ArrowUp", "ArrowRight", "ArrowLeft", "Home", "End"].includes(event.key)) return;
                event.preventDefault();
                const index = layout.nodes.findIndex((node) => node.task.taskId === task.taskId);
                const next = event.key === "Home" ? 0 : event.key === "End" ? layout.nodes.length - 1
                  : (index + (event.key === "ArrowDown" || event.key === "ArrowRight" ? 1 : -1) + layout.nodes.length) % layout.nodes.length;
                const button = buttons.current.get(layout.nodes[next].task.taskId);
                button?.focus(); button?.scrollIntoView({ block: "nearest", inline: "nearest" });
              }}>
              <span className="task-graph-node-status"><span className="task-graph-node-icon" aria-hidden="true">{GRAPH_ICONS[state]}</span>{t(state)}</span>
              <strong className="task-graph-node-title">{task.title || task.taskId}</strong>
              <span className="task-graph-node-role">{task.roleName
                ? `${task.roleName}${task.provider ? ` · ${task.provider}` : ""}`
                : task.suggestedRoleName
                  ? `${t("suggested")}: ${task.suggestedRoleName}${task.suggestedProvider ? ` · ${task.suggestedProvider}` : ""}`
                  : t("unassigned")}</span>
              <span className="task-graph-node-run">{task.phase ?? t("noRun")}</span>
              {hiddenPrerequisites > 0 ? <span className="task-graph-node-boundary">{t("hiddenPrerequisites")}: {hiddenPrerequisites}</span> : null}
            </button>
          </foreignObject>)}
        </svg>
      </div>
      <details className="task-graph-list"><summary>{t("list")}</summary>{list()}</details>
      <p className="visually-hidden">{t("keyboard")}</p>
    </>}
  </section>;
}
