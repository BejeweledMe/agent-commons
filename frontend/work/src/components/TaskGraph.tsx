import { type ReactElement, useId, useRef, useState } from "react";
import type { TrackerEdge, TrackerTask } from "../contracts";
import type { Locale } from "../i18n";
import { buildTaskGraph, GRAPH_ICONS, GRAPH_STATES, NODE_HEIGHT, NODE_WIDTH } from "../taskGraph.js";
import { taskGraphText } from "../taskGraphStrings.js";

export function TaskGraph({ tasks, edges, selectedTaskId, onSelectTask, locale }: {
  tasks: readonly TrackerTask[]; edges: readonly TrackerEdge[]; selectedTaskId: string | null;
  onSelectTask: (id: string) => void; locale: Locale;
}): ReactElement {
  const [focused, setFocused] = useState(true);
  const marker = useId().replace(/:/g, "");
  const buttons = useRef(new Map<string, HTMLButtonElement>());
  const layout = buildTaskGraph(tasks, edges, selectedTaskId, focused);
  const t = (key: Parameters<typeof taskGraphText>[1]): string => taskGraphText(locale, key);
  return <section className="task-graph" aria-label={t("graph")}>
    <p className="small-copy">{t("graphHelp")}</p>
    <ul className="task-graph-legend" aria-label={t("legend")}>{GRAPH_STATES.map((state) => <li data-graph-state={state} key={state}><span aria-hidden="true">{GRAPH_ICONS[state]}</span> {t(state)}</li>)}</ul>
    {selectedTaskId !== null ? <label className="task-graph-focus"><input type="checkbox" checked={focused} onChange={(event) => setFocused(event.target.checked)} />{t("focus")}</label> : null}
    {layout.tooLarge || layout.invalid ? <>
      <p className="inspector-notice" role="status">{t(layout.tooLarge ? "graphLimit" : "graphInvalid")}</p>
      <ul className="task-graph-fallback">{tasks.map((task) => <li key={task.taskId}><button type="button" className="button button-secondary" aria-pressed={task.taskId === selectedTaskId} onClick={() => onSelectTask(task.taskId)}>{task.title}</button></li>)}</ul>
    </> : <>
      <p className="small-copy">{layout.nodes.length}/{layout.total} {t("selectedCount")}</p>
      <div className="task-graph-scroll" tabIndex={0} role="region" aria-label={t("graph")}>
        <svg width={layout.width} height={layout.height} viewBox={`0 0 ${layout.width} ${layout.height}`} aria-label={t("graph")}>
          <defs><marker id={marker} markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M 0 0 L 8 4 L 0 8 Z" /></marker></defs>
          <g className="task-graph-edges" aria-hidden="true">{layout.edges.map((edge) => <path key={`${edge.from}:${edge.to}`} d={edge.path} markerEnd={`url(#${marker})`} />)}</g>
          {layout.nodes.map(({ task, x, y, state }) => <foreignObject key={task.taskId} x={x} y={y} width={NODE_WIDTH} height={NODE_HEIGHT}>
            <button type="button" className="task-graph-node" data-graph-state={state} aria-pressed={task.taskId === selectedTaskId}
              title={task.title} onClick={() => onSelectTask(task.taskId)}
              ref={(button) => { if (button) buttons.current.set(task.taskId, button); else buttons.current.delete(task.taskId); }}
              onKeyDown={(event) => {
                if (!["ArrowDown", "ArrowUp", "ArrowRight", "ArrowLeft", "Home", "End"].includes(event.key)) return;
                event.preventDefault();
                const index = layout.nodes.findIndex((node) => node.task.taskId === task.taskId);
                const next = event.key === "Home" ? 0 : event.key === "End" ? layout.nodes.length - 1
                  : (index + (event.key === "ArrowDown" || event.key === "ArrowRight" ? 1 : -1) + layout.nodes.length) % layout.nodes.length;
                buttons.current.get(layout.nodes[next].task.taskId)?.focus();
              }}>
              <span className="task-graph-node-status"><span className="task-graph-node-icon" aria-hidden="true">{GRAPH_ICONS[state]}</span>{t(state)}</span>
              <strong className="task-graph-node-title">{task.title || task.taskId}</strong>
              <span className="task-graph-node-role">{task.roleName
                ? `${task.roleName}${task.provider ? ` · ${task.provider}` : ""}`
                : task.suggestedRoleName
                  ? `${t("suggested")}: ${task.suggestedRoleName}${task.suggestedProvider ? ` · ${task.suggestedProvider}` : ""}`
                  : t("unassigned")}</span>
              <span className="task-graph-node-run">{task.phase ?? t("noRun")}</span>
            </button>
          </foreignObject>)}
        </svg>
      </div>
      <p className="visually-hidden">{t("keyboard")}</p>
    </>}
  </section>;
}
