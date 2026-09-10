import { type ReactElement, type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Background, Controls, Handle, MiniMap, Position, ReactFlow, ReactFlowProvider, applyNodeChanges, useReactFlow,
  type Connection, type Edge, type Node, type NodeChange, type NodeProps
} from "@xyflow/react";
import { ApiProblem, type WorkApi } from "../api.js";
import type { RoleOption } from "../contracts.js";
import type { Locale, MessageKey } from "../i18n.js";
import type { BlueprintApplication } from "../libraryTypes.js";
import type { TrackerViewState } from "../trackerState.js";
import {
  BoardError, EMPTY_LAYOUT, addFrame, buildBoardModel, frameForApplication, layoutBody, moveFrame, moveRole, parseBoardGraph, parseBoardLayout,
  removeFrame, renameFrame, resetPositions, type BoardFrame, type BoardGraph, type BoardLayout, type BoardRoleNode
} from "../boardState.js";
import { ConversationButton } from "./ConversationPanel.js";
import { OutputsButton } from "./OutputsPanel.js";

type Text = (key: MessageKey) => string;

type RoleData = {
  role: BoardRoleNode; text: Text; writesEnabled: boolean; canGiveTask: boolean;
  onOpenTasks: (agentId: string) => void; onGiveTask: (agentId: string) => void;
};
type FrameData = { frame: BoardFrame; text: Text; writesEnabled: boolean; onRename: (frameId: string, title: string) => void; onRemove: (frameId: string) => void };
type RoleFlowNode = Node<RoleData, "role">;
type FrameFlowNode = Node<FrameData, "frame">;
type BoardNode = RoleFlowNode | FrameFlowNode;

/** A standing role on the board. Every action is an existing server operation or a navigation. */
function RoleNode({ data }: NodeProps<RoleFlowNode>): ReactElement {
  const { role, text } = data;
  return <div className="board-role" data-activity={role.activity} data-agent-id={role.id}>
    <Handle type="target" position={Position.Top} isConnectable={data.writesEnabled} />
    <div className="board-role-head">
      <span className="board-role-name">{role.name}</span>
      <span className="board-role-activity">{text(`board_activity_${role.activity}` as MessageKey)}</span>
    </div>
    <p className="board-role-meta">{role.specialization ? <><code>{role.specialization}</code> · </> : null}<code>{role.profileId}</code>{role.model ? <> · <code>{role.model}</code></> : null}</p>
    <p className="board-role-tasks">{role.taskCount === 0 ? text("board_role_no_tasks") : `${text("board_role_tasks")}: ${role.taskCount} · ${role.openTaskCount} ${text("board_role_open_tasks")}`}{role.runPhase ? <> · <code>{role.runPhase}</code></> : null}</p>
    <div className="board-role-actions">
      <ConversationButton scope={{ kind: "agent", id: role.id }} title={role.name} />
      <OutputsButton scope={{ kind: "agent", id: role.id }} title={role.name} />
      <button type="button" className="button button-secondary button-inline" onClick={() => data.onOpenTasks(role.id)}>{text("board_role_tasks")}</button>
      <button type="button" className="button button-primary button-inline" disabled={!data.writesEnabled || !data.canGiveTask} title={data.canGiveTask ? undefined : text("board_select_task_first")} onClick={() => data.onGiveTask(role.id)}>{text("board_role_give_task")}</button>
    </div>
    <Handle type="source" position={Position.Bottom} isConnectable={data.writesEnabled} />
  </div>;
}

/** A department frame: an arrangement on the board, not a canonical Team. */
function FrameNode({ data }: NodeProps<FrameFlowNode>): ReactElement {
  const { frame, text } = data;
  return <div className="board-frame" data-frame-id={frame.id}>
    <div className="board-frame-head">
      <input className="board-frame-title nodrag" aria-label={text("board_frame_rename")} defaultValue={frame.title} disabled={!data.writesEnabled} maxLength={120}
        onBlur={(event) => { if (event.currentTarget.value.trim() && event.currentTarget.value.trim() !== frame.title) data.onRename(frame.id, event.currentTarget.value); }}
        onKeyDown={(event) => { if (event.key === "Enter") event.currentTarget.blur(); }} />
      <span className="board-frame-count">{frame.members.length} {text("board_frame_members")}</span>
      {data.writesEnabled ? <button type="button" className="notice-link board-frame-remove nodrag" title={text("board_frame_remove_help")} onClick={() => data.onRemove(frame.id)}>{text("board_frame_remove")}</button> : null}
    </div>
  </div>;
}

const nodeTypes = { role: RoleNode, frame: FrameNode };

export type ProjectBoardProps = {
  api: WorkApi; locale: Locale; text: Text; roles: readonly RoleOption[]; tracker: TrackerViewState; writesEnabled: boolean;
  /** Bumped by the shell after any confirmed write so the graph is re-read. */
  refreshKey: number;
  selectedTaskId: string | null;
  onOpenTasks: (agentId: string) => void;
  onGiveTask: (agentId: string) => void;
  onAddDepartment: () => void;
  /** A blueprint application the shell just confirmed; the board frames it once and reports back. */
  pendingApplication: { application: BlueprintApplication; title: string } | null;
  onApplicationPlaced: () => void;
  hirePanel: ReactNode; hireOpen: boolean; onHireOpenChange: (open: boolean) => void;
};

export function ProjectBoard(props: ProjectBoardProps): ReactElement {
  return <ReactFlowProvider><BoardCanvas {...props} /></ReactFlowProvider>;
}

function BoardCanvas({ api, text, roles, tracker, writesEnabled, refreshKey, selectedTaskId, onOpenTasks, onGiveTask, onAddDepartment, pendingApplication, onApplicationPlaced, hirePanel, hireOpen, onHireOpenChange }: ProjectBoardProps): ReactElement {
  const flow = useReactFlow();
  const [graph, setGraph] = useState<BoardGraph | null>(null);
  const [layout, setLayout] = useState<BoardLayout>(EMPTY_LAYOUT);
  const [loadState, setLoadState] = useState<"loading" | "ready" | "failed">("loading");
  const [status, setStatus] = useState<MessageKey | null>(null);
  const layoutRef = useRef(layout);
  layoutRef.current = layout;
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Read the role structure and the arrangement; a failed read never invents either.
  useEffect(() => {
    const controller = new AbortController();
    setLoadState("loading");
    void (async () => {
      try {
        const [graphValue, layoutValue] = await Promise.all([api.readGraph(controller.signal), api.readBoardLayout(controller.signal)]);
        if (controller.signal.aborted) return;
        setGraph(parseBoardGraph(graphValue));
        setLayout(parseBoardLayout(layoutValue));
        setLoadState("ready");
      } catch (error) {
        if (controller.signal.aborted) return;
        if (error instanceof BoardError || error instanceof ApiProblem) setLoadState("failed"); else throw error;
      }
    })();
    return () => controller.abort();
  }, [api, refreshKey]);

  const persist = useCallback((next: BoardLayout) => {
    setLayout(next);
    if (!writesEnabled) return;
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      saveTimer.current = null;
      const controller = new AbortController();
      setStatus("board_saving");
      void (async () => {
        try {
          const saved = parseBoardLayout(await api.writeBoardLayout(layoutBody(layoutRef.current), controller.signal));
          // The server revision is the only revision; positions from the reply are what was stored.
          setLayout((current) => ({ ...current, revision: saved.revision }));
          setStatus("board_saved");
        } catch (error) {
          if (error instanceof ApiProblem && error.apiError?.code === "board_revision_conflict") setStatus("board_layout_conflict");
          else if (error instanceof ApiProblem || error instanceof BoardError) setStatus("board_layout_save_failed");
          else throw error;
        }
      })();
    }, 600);
  }, [api, writesEnabled]);

  // A confirmed blueprint application becomes a department frame exactly once.
  useEffect(() => {
    if (!pendingApplication || loadState !== "ready") return;
    persist(addFrame(layoutRef.current, frameForApplication(pendingApplication.application, pendingApplication.title, layoutRef.current)));
    onApplicationPlaced();
  }, [pendingApplication, loadState, persist, onApplicationPlaced]);

  const model = useMemo(() => buildBoardModel(roles, graph, tracker, layout), [roles, graph, tracker, layout]);

  const handlers = useMemo(() => ({
    rename: (frameId: string, title: string) => persist(renameFrame(layoutRef.current, frameId, title)),
    remove: (frameId: string) => persist(removeFrame(layoutRef.current, frameId))
  }), [persist]);

  const derivedNodes = useMemo((): BoardNode[] => [
    ...model.frames.map((frame): FrameFlowNode => ({
      id: frame.id, type: "frame", position: { x: frame.x, y: frame.y }, width: frame.width, height: frame.height, zIndex: -1, selectable: true, draggable: writesEnabled,
      data: { frame, text, writesEnabled, onRename: handlers.rename, onRemove: handlers.remove }
    })),
    ...model.nodes.map((role): RoleFlowNode => ({
      id: role.id, type: "role", position: role.position, draggable: writesEnabled,
      data: { role, text, writesEnabled, canGiveTask: selectedTaskId !== null, onOpenTasks, onGiveTask }
    }))
  ], [model, text, writesEnabled, selectedTaskId, onOpenTasks, onGiveTask, handlers]);

  const [nodes, setNodes] = useState<BoardNode[]>(derivedNodes);
  useEffect(() => { setNodes(derivedNodes); }, [derivedNodes]);

  const edges = useMemo((): Edge[] => [
    ...model.reportsTo.map((edge) => ({ id: `reports|${edge.from}|${edge.to}`, source: edge.to, target: edge.from, className: "board-edge-reports", type: "smoothstep" })),
    ...model.links.map((link) => ({ id: link.id, source: link.from, target: link.to, className: "board-edge-link", type: "smoothstep", label: link.allowedAction ?? undefined }))
  ], [model]);

  const onNodesChange = useCallback((changes: NodeChange<BoardNode>[]) => setNodes((current) => applyNodeChanges(changes, current)), []);

  const onNodeDragStop = useCallback((_: unknown, node: BoardNode) => {
    if (!writesEnabled) return;
    persist(node.type === "frame" ? moveFrame(layoutRef.current, node.id, node.position) : moveRole(layoutRef.current, node.id, node.position));
  }, [persist, writesEnabled]);

  const onConnect = useCallback((connection: Connection) => {
    if (!writesEnabled || !connection.source || !connection.target || connection.source === connection.target) return;
    const controller = new AbortController();
    void (async () => {
      try {
        await api.openAgentLink({ fromAgentId: connection.source, toAgentId: connection.target, allowedAction: "ask", reason: text("board_link_reason") }, controller.signal);
        setGraph(parseBoardGraph(await api.readGraph(controller.signal)));
        setStatus("board_link_opened");
      } catch (error) {
        if (error instanceof ApiProblem || error instanceof BoardError) setStatus("board_link_failed"); else throw error;
      }
    })();
  }, [api, text, writesEnabled]);

  const relayout = useCallback(() => { persist(resetPositions(layoutRef.current)); window.setTimeout(() => flow.fitView({ padding: 0.2 }), 50); }, [persist, flow]);

  return <div className="board-section">
    <div className="board-toolbar">
      <p className="small-copy">{text("board_intro")}</p>
      <div className="board-toolbar-actions">
        <button type="button" className="button button-primary button-inline" disabled={!writesEnabled} onClick={() => onHireOpenChange(!hireOpen)} aria-expanded={hireOpen}>{text("board_hire")}</button>
        <button type="button" className="button button-secondary button-inline" disabled={!writesEnabled} onClick={onAddDepartment}>{text("board_add_department")}</button>
        <button type="button" className="button button-secondary button-inline" onClick={() => flow.fitView({ padding: 0.2 })}>{text("board_fit")}</button>
        <button type="button" className="button button-secondary button-inline" disabled={!writesEnabled} onClick={relayout}>{text("board_relayout")}</button>
      </div>
    </div>
    <p className="board-status" role="status">{loadState === "loading" ? text("board_loading") : loadState === "failed" ? text("board_unavailable") : status ? text(status) : text("board_parallel_note")}</p>
    <div className="board-body">
      <div className="board-canvas" aria-label={text("shell_nav_board")}>
        <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} onNodesChange={onNodesChange} onNodeDragStop={onNodeDragStop} onConnect={onConnect}
          fitView minZoom={0.2} maxZoom={1.6} nodesConnectable={writesEnabled} elementsSelectable proOptions={{ hideAttribution: true }} deleteKeyCode={null}>
          <Background gap={24} />
          <Controls showInteractive={false} />
          <MiniMap pannable zoomable />
        </ReactFlow>
        {loadState === "ready" && model.nodes.length === 0 ? <div className="board-empty"><p>{text("board_empty")}</p></div> : null}
      </div>
      {hireOpen ? <aside className="board-panel" aria-label={text("shell_board_hire_title")}>
        <div className="board-panel-header"><h2>{text("shell_board_hire_title")}</h2><button type="button" className="button button-secondary button-inline" onClick={() => onHireOpenChange(false)}>{text("board_close_panel")}</button></div>
        {hirePanel}
      </aside> : null}
    </div>
  </div>;
}
