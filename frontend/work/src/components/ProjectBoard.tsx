import type { AgentBoardSession } from "../agentBoardState.js";
import { AgentSettings, type AgentTab } from "./AgentSettings.js";
import { Icon, IconButton } from "./Icon.js";
import { Modal } from "./Modal.js";
import type { LibraryState } from "../libraryTypes.js";
import { type ReactElement, type ReactNode, useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import {
  Background, Controls, Handle, Position, ReactFlow, ReactFlowProvider, applyNodeChanges, getNodesBounds, useReactFlow, useNodesInitialized, useStore,
  type Connection, type Edge, type Node, type NodeChange, type NodeProps
} from "@xyflow/react";
import { ApiProblem, requestOutcome, type WorkApi } from "../api.js";
import { recordInstrumentation } from "../instrumentation.js";
import type { RoleOption } from "../contracts.js";
import type { Locale, MessageKey } from "../i18n.js";
import type { BlueprintApplication } from "../libraryTypes.js";
import type { TrackerViewState } from "../trackerState.js";
import {
  BoardError, EMPTY_LAYOUT, OWNER_NODE_ID, addFrame, boardFocusRole, buildBoardModel, frameForApplication, layoutBody, moveFrame, moveRole, parseBoardGraph, parseBoardLayout,
  removeFrame, renameFrame, resetPositions, roleCardCentre, roleCardCopy, type BoardFrame, type BoardGraph, type BoardLayout, type BoardRoleNode
} from "../boardState.js";
import { bandHidesNodeActions, workingZoomLevel, zoomBand, type ZoomBand } from "../mapZoomBands.js";
import { minimumPaddedMapZoom, reframeFlowViewport } from "../mapViewport.js";
import { ConversationButton } from "./ConversationPanel.js";
import { AgentGalleryButton, OutputsButton } from "./OutputsPanel.js";
import { BoardTaskPicker } from "./BoardTaskPicker.js";

type Text = (key: MessageKey) => string;

type RoleActionHandlers = {
  onConfigure: (agentId: string, tab: AgentTab) => void; onChild: (agentId: string) => void;
  onOpenTasks: (agentId: string) => void; onGiveTask: (agentId: string) => void;
};
type RoleData = RoleActionHandlers & {
  role: BoardRoleNode; text: Text; writesEnabled: boolean; selected: boolean; onSelect: (agentId: string) => void;
  supervisorName: string | null; cardTitle: string; cardDescription: string;
};
type FrameData = { frame: BoardFrame; text: Text; writesEnabled: boolean; onRename: (frameId: string, title: string) => void; onRemove: (frameId: string) => void };
type RoleFlowNode = Node<RoleData, "role">;
type FrameFlowNode = Node<FrameData, "frame">;
type OwnerFlowNode = Node<{ text: Text; onOpenChat: () => void }, "owner">;
type BoardNode = RoleFlowNode | FrameFlowNode | OwnerFlowNode;

function OwnerNode({ data }: NodeProps<OwnerFlowNode>): ReactElement {
  return <div className="board-owner"><Icon name="supervisor" size={20} /><strong>{data.text("owner_title")}</strong><button type="button" className="button button-secondary nodrag" onClick={data.onOpenChat}><Icon name="chat" />{data.text("owner_chat")}</button><Handle type="source" position={Position.Bottom} isConnectable={false} /></div>;
}

/**
 * One role's actions, used unchanged by the card and by the selected-element
 * strip (UX-13). Three direct controls carry the work; the rest live behind a
 * native `<details>` rather than a hand-rolled ARIA menu, so the keyboard and
 * the screen reader get the platform's own behaviour. Every accessible name
 * carries the agent it acts on, because a wall of icons otherwise says only
 * "Settings" eight times over.
 */
function RoleActions({ role, text, writesEnabled, variant, onConfigure, onChild, onOpenTasks, onGiveTask }: RoleActionHandlers & {
  role: BoardRoleNode; text: Text; writesEnabled: boolean; variant: "card" | "strip";
}): ReactElement {
  const named = (key: MessageKey): string => `${text(key)} — ${role.name}`;
  return <div className={`board-role-actions nodrag${variant === "strip" ? " board-strip-actions" : ""}`}>
    {variant === "strip" ? <>
      <button type="button" className="button button-secondary" aria-label={named("agent_settings")} onClick={() => onConfigure(role.id, "general")}><Icon name="settings" />{text("agent_settings")}</button>
      <button type="button" className="button button-secondary" aria-label={named("board_role_tasks")} onClick={() => onOpenTasks(role.id)}><Icon name="tasks" />{text("board_role_tasks")}</button>
    </> : <>
      <IconButton icon="settings" label={named("agent_settings")} onClick={() => onConfigure(role.id, "general")} />
      <IconButton icon="tasks" label={named("board_role_tasks")} onClick={() => onOpenTasks(role.id)} />
    </>}
    {/* Always available: the task is chosen explicitly in the picker this opens,
        so a missing selection elsewhere is no longer a reason to disable it. */}
    {variant === "strip"
      ? <button type="button" className="button button-secondary" aria-label={named("board_role_give_task")} onClick={() => onGiveTask(role.id)}><Icon name="run" />{text("board_role_give_task")}</button>
      : <IconButton icon="run" label={named("board_role_give_task")} onClick={() => onGiveTask(role.id)} />}
    <details className="board-role-more" onKeyDown={(event) => {
      if (event.key !== "Escape" || !event.currentTarget.open) return;
      event.preventDefault(); event.stopPropagation(); event.currentTarget.open = false;
      event.currentTarget.querySelector("summary")?.focus({ preventScroll: true });
    }}>
      <summary aria-label={named("board_role_more")} title={named("board_role_more")}>{text("board_role_more")}</summary>
      <div className="board-role-more-body">
        <IconButton icon="skills" label={named("agent_tab_skills")} onClick={() => onConfigure(role.id, "skills")} />
        <IconButton icon="permissions" label={named("agent_tab_permissions")} onClick={() => onConfigure(role.id, "permissions")} />
        <IconButton icon="link" label={named("board_connections")} onClick={() => onConfigure(role.id, "connections")} />
        <IconButton icon="add-child" label={named("agent_add_child")} disabled={!writesEnabled} onClick={() => onChild(role.id)} />
        <ConversationButton compact scope={{ kind: "agent", id: role.id }} title={role.name} />
        <OutputsButton compact scope={{ kind: "agent", id: role.id }} title={role.name} />
      </div>
    </details>
  </div>;
}

/** A standing role on the board. Every action is an existing server operation or a navigation. */
function RoleNode({ data }: NodeProps<RoleFlowNode>): ReactElement {
  const { role, text } = data;
  // Click and focus both select: reaching a card with the keyboard has to put
  // it in the strip, or the strip would be a mouse-only affordance.
  return <div className="board-role" data-activity={role.activity} data-agent-id={role.id} data-selected={data.selected || undefined}
    onClick={() => data.onSelect(role.id)} onFocusCapture={() => data.onSelect(role.id)}>
    <Handle type="target" position={Position.Top} isConnectable={data.writesEnabled} />
    <div className="board-role-head">
      <AgentGalleryButton scope={{ kind: "agent", id: role.id }} title={role.name} displayTitle={data.cardTitle} />
      <span className="board-role-activity">{text(`board_activity_${role.activity}` as MessageKey)}</span>
    </div>
    {/* UX-12: the profession stays the heading; the distinguishing instance
        name is the second level, and it is the level that tells two agents of
        the same profession apart, so it is readable rather than a footnote. */}
    <p className="board-role-identity" title={role.name}>{role.name}</p>
    <p className="board-role-meta" title={data.cardDescription}>{data.cardDescription}</p>
    <button type="button" className="board-supervisor nodrag" title={text("board_supervisor_help")} onClick={() => data.onConfigure(role.id, "general")}><Icon name="supervisor" size={14} />{data.supervisorName ? `${text("agent_supervisor")}: ${data.supervisorName}` : role.supervisorId ? text("agent_supervisor_unavailable") : text("agent_assign_supervisor")}</button>
    <p className="board-role-tasks">{role.taskCount === 0 ? text("board_role_no_tasks") : `${text("board_role_tasks")}: ${role.taskCount} · ${role.openTaskCount} ${text("board_role_open_tasks")}`}</p>
    <RoleActions role={role} text={text} writesEnabled={data.writesEnabled} variant="card"
      onConfigure={data.onConfigure} onChild={data.onChild} onOpenTasks={data.onOpenTasks} onGiveTask={data.onGiveTask} />
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

const nodeTypes = { owner: OwnerNode, role: RoleNode, frame: FrameNode };

export type ProjectBoardProps = {
  onOpenProjectChat: () => void;
  session: AgentBoardSession;
  active: boolean; library: LibraryState; onChanged: () => void; onConfigureChild: (agentId: string) => void;
  api: WorkApi; locale: Locale; text: Text; rolesReady: boolean; roles: readonly RoleOption[]; tracker: TrackerViewState; writesEnabled: boolean;
  /** Bumped by the shell after any confirmed write so the graph is re-read. */
  refreshKey: number;
  /** The Tasks-tab selection: a preselection for the picker, never the choice itself. */
  selectedTaskId: string | null;
  onOpenTasks: (agentId: string) => void;
  /** Called only after a task was chosen explicitly in the board's own picker. */
  onGiveTask: (agentId: string, taskId: string) => void;
  onAddDepartment: () => void;
  /** A blueprint application the shell just confirmed; the board frames it once and reports back. */
  pendingApplication: { application: BlueprintApplication; title: string } | null;
  onApplicationPlaced: () => void;
  hirePanel: ReactNode; hireOpen: boolean; onHireOpenChange: (open: boolean) => void;
};

export function ProjectBoard(props: ProjectBoardProps): ReactElement {
  return <ReactFlowProvider><BoardCanvas {...props} /></ReactFlowProvider>;
}

function BoardCanvas({ onOpenProjectChat, session, active, library, onChanged, onConfigureChild, api, text, rolesReady, roles, tracker, writesEnabled, refreshKey, selectedTaskId, onOpenTasks, onGiveTask, onAddDepartment, pendingApplication, onApplicationPlaced, hirePanel, hireOpen, onHireOpenChange }: ProjectBoardProps): ReactElement {
  const flow = useReactFlow();
  const nodesInitialized = useNodesInitialized();
  // Leave room for both Fit controls' padding, even on wide saved layouts.
  // Retain a smaller current scale when the frame grows: resize never zooms in.
  const minimumZoom = useStore((state) => {
    const bounds = getNodesBounds([...state.nodeLookup.values()]);
    return minimumPaddedMapZoom(bounds, { width: state.width, height: state.height }, state.transform[2]);
  });
  const [fitRequested, setFitRequested] = useState(session.viewport === null);
  const fitPending = useRef(fitRequested);
  const frameWidth = useStore((state) => state.width);
  const frameHeight = useStore((state) => state.height);
  const previousFrame = useRef<{ width: number; height: number } | null>(null);
  useEffect(() => {
    if (!active || frameWidth <= 0 || frameHeight <= 0) return;
    const frame = { width: frameWidth, height: frameHeight };
    const before = previousFrame.current;
    previousFrame.current = frame;
    if (before && !fitPending.current && (before.width !== frameWidth || before.height !== frameHeight)) {
      void flow.setViewport(reframeFlowViewport(flow.getViewport(), before, frame));
    }
  }, [active, frameWidth, frameHeight, flow]);
  const requestFit = useCallback(() => { fitPending.current = true; setFitRequested(true); }, []);
  useSyncExternalStore(session.subscribe, session.snapshot);
  const [visited, setVisited] = useState(active || session.visited);
  useEffect(() => { if (active) { session.visited = true; setVisited(true); } }, [active, session]);
  const [showCommunication, setShowCommunication] = useState(false);
  const [showCreators, setShowCreators] = useState(false);
  const [settings, setSettings] = useState<{ id: string; tab: AgentTab } | null>(null);
  const { connection, linkState } = session;
  const setConnection = session.setConnection.bind(session);
  const setLinkState = session.setLinkState.bind(session);
  const [connectionOpen, setConnectionOpen] = useState(false);
  const configure = useCallback((id: string, tab: AgentTab) => { session.editor(api, id); setSettings({ id, tab }); }, [api, session]);

  // UX-01: the live reading band, with hysteresis so a drag across a threshold
  // does not strobe. It is published as one attribute and read by CSS; cards
  // keep their fixed 264×248 geometry at every band, only their content changes.
  const previousBand = useRef<ZoomBand | null>(null);
  const [band, setBand] = useState<ZoomBand>(() => {
    const initial = zoomBand(session.viewport?.zoom ?? 1);
    previousBand.current = initial;
    return initial;
  });
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const rescueFocus = useRef(false);
  const readBand = useCallback((zoom: number) => {
    const next = zoomBand(zoom, previousBand.current);
    if (bandHidesNodeActions(next) && previousBand.current === "work") {
      const active = document.activeElement;
      const hiddenControl = active instanceof HTMLElement ? active.closest(".board-role-actions, .board-supervisor") : null;
      const card = hiddenControl?.closest<HTMLElement>(".board-role");
      if (card?.dataset.agentId) { setSelectedAgentId(card.dataset.agentId); rescueFocus.current = true; }
    }
    previousBand.current = next;
    setBand(next);
  }, []);
  const [picker, setPicker] = useState<{ agentId: string } | null>(null);
  const stripRef = useRef<HTMLDivElement>(null);
  const openPicker = useCallback((agentId: string) => { setSelectedAgentId(agentId); setPicker({ agentId }); }, []);
  useEffect(() => { if (!active) setPicker(null); }, [active]);

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
        setLoadState("failed");
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
      const started = performance.now();
      void (async () => {
        try {
          const saved = parseBoardLayout(await api.writeBoardLayout(layoutBody(layoutRef.current), controller.signal));
          // The server revision is the only revision; positions from the reply are what was stored.
          setLayout((current) => ({ ...current, revision: saved.revision }));
          setStatus("board_saved");
          recordInstrumentation("board_edit", "confirmed", performance.now() - started);
        } catch (error) {
          recordInstrumentation("board_edit", requestOutcome(error), performance.now() - started);
          if (error instanceof ApiProblem && error.apiError?.code === "board_revision_conflict") setStatus("board_layout_conflict");
          else if (error instanceof ApiProblem || error instanceof BoardError) setStatus("board_layout_save_failed");
          else setStatus("board_layout_save_failed");
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

  // UX-12: the profession heading and the distinct instance name come from one
  // place, so a card and the selected strip can never disagree about either.
  const roleCopy = useCallback((role: BoardRoleNode) => roleCardCopy(
    role.name, library.kind === "ready" ? library.catalog.roles.find((item) => item.ref.id === role.specialization) : undefined
  ), [library]);

  const handlers = useMemo(() => ({
    rename: (frameId: string, title: string) => persist(renameFrame(layoutRef.current, frameId, title)),
    remove: (frameId: string) => persist(removeFrame(layoutRef.current, frameId))
  }), [persist]);

  const derivedNodes = useMemo((): BoardNode[] => [
    { id: OWNER_NODE_ID, type: "owner", position: model.ownerPosition, draggable: false, selectable: false, connectable: false, data: { text, onOpenChat: onOpenProjectChat } },
    ...model.frames.map((frame): FrameFlowNode => ({
      id: frame.id, type: "frame", position: { x: frame.x, y: frame.y }, width: frame.width, height: frame.height, zIndex: -1, selectable: true, draggable: writesEnabled,
      data: { frame, text, writesEnabled, onRename: handlers.rename, onRemove: handlers.remove }
    })),
    ...model.nodes.map((role): RoleFlowNode => ({
      id: role.id, type: "role", position: role.position, draggable: writesEnabled,
      data: { role, text, writesEnabled, selected: role.id === selectedAgentId, onSelect: setSelectedAgentId,
        onOpenTasks, onGiveTask: openPicker, onConfigure: configure, onChild: onConfigureChild,
        ...roleCopy(role),
        supervisorName: roles.find((r) => r.id === role.supervisorId)?.name ?? null }

    }))
  ], [model, text, writesEnabled, selectedAgentId, onOpenTasks, openPicker, handlers, configure, onConfigureChild, roleCopy, roles, onOpenProjectChat]);

  const [nodes, setNodes] = useState<BoardNode[]>(derivedNodes);
  useEffect(() => { setNodes((current) => derivedNodes.map((node) => ({ ...node, selected: current.find((old) => old.id === node.id)?.selected ?? false }))); }, [derivedNodes]);

  // Fit only once the async graph, saved positions and measured cards agree.
  // Subsequent refreshes and project/tab restores keep the operator's viewport.
  useEffect(() => {
    if (!fitRequested || !active || loadState !== "ready" || !rolesReady || !nodesInitialized) return;
    if (nodes.length !== derivedNodes.length || nodes.some((node, index) => {
      const expected = derivedNodes[index];
      return node.id !== expected.id || node.position.x !== expected.position.x || node.position.y !== expected.position.y;
    })) return;
    const frame = requestAnimationFrame(() => {
      if (!fitPending.current) return;
      void flow.fitView({ padding: 0.2 }).then((fitted) => {
        if (!fitted || !fitPending.current) return;
        fitPending.current = false; setFitRequested(false);
        const viewport = flow.getViewport();
        session.setViewport(viewport);
        readBand(viewport.zoom);
      });
    });
    return () => cancelAnimationFrame(frame);
  }, [active, loadState, rolesReady, nodesInitialized, nodes, derivedNodes, fitRequested, flow, session, readBand]);

  const edges = useMemo((): Edge[] => [
    ...model.ownerMembers.map((id) => ({ id: `owner|${id}`, source: OWNER_NODE_ID, target: id, className: "board-edge-membership", type: "smoothstep", label: text("owner_membership") })),
    ...model.supervisedBy.map((edge) => ({ id: `supervisor|${edge.from}|${edge.to}`, source: edge.to, target: edge.from, className: "board-edge-supervisor", type: "smoothstep", label: text("agent_supervisor") })),
    ...(showCreators ? model.reportsTo : []).map((edge) => ({ id: `reports|${edge.from}|${edge.to}`, source: edge.to, target: edge.from, className: "board-edge-reports", type: "smoothstep", label: text("agent_creator") })),
    ...(showCommunication ? model.links : []).map((link) => ({ id: link.id, source: link.from, target: link.to, className: "board-edge-link", type: "smoothstep", label: text(link.allowedAction === "handoff_work" ? "agent_link_handoff" : "agent_link_ask") }))
  ], [model, text, showCreators, showCommunication]);

  const onNodesChange = useCallback((changes: NodeChange<BoardNode>[]) => setNodes((current) => applyNodeChanges(changes, current)), []);

  const onNodeDragStop = useCallback((_: unknown, node: BoardNode) => {
    if (!writesEnabled || node.type === "owner") return;
    persist(node.type === "frame" ? moveFrame(layoutRef.current, node.id, node.position) : moveRole(layoutRef.current, node.id, node.position));
  }, [persist, writesEnabled]);

  const onConnect = useCallback((value: Connection) => {
    if (!writesEnabled || !value.source || !value.target || value.source === value.target || value.source === OWNER_NODE_ID || value.target === OWNER_NODE_ID) return;
    setConnectionOpen(true);
    if (connection && ["uncertain", "saving", "saved_refresh_failed"].includes(linkState)) return;
    setConnection({ source: value.source, target: value.target, kind: "ask", key: crypto.randomUUID(), confirmed: false }); setLinkState("idle");
  }, [writesEnabled, connection, linkState]);
  async function saveConnection(): Promise<void> {
    if (!connection || linkState === "saving") return;
    setLinkState("saving");
    const request = connection;
    try {
      if (!request.confirmed) { await api.openAgentLink({ fromAgentId: request.source, toAgentId: request.target, allowedAction: request.kind, reason: text("board_link_reason") }, new AbortController().signal, request.key); request.confirmed = true; }
    } catch (error) { setLinkState(requestOutcome(error) === "uncertain" ? "uncertain" : "failed"); return; }
    try { setGraph(parseBoardGraph(await api.readGraph(new AbortController().signal))); setConnection(null); setLinkState("idle"); setShowCommunication(true); onChanged(); }
    catch { setLinkState("saved_refresh_failed"); }
  }

  const relayout = useCallback(() => { persist(resetPositions(layoutRef.current)); requestFit(); }, [persist, requestFit]);

  const selectedRole = selectedAgentId === null ? null : model.nodes.find((node) => node.id === selectedAgentId) ?? null;

  /**
   * UX-01/UX-10: the explicit working zoom. It lands on a readable 80–100%
   * centred on the selected role, or on the first role when nothing is
   * selected. It changes the camera only — no stored position is reset, and no
   * poll or resize ever performs it on the operator's behalf.
   */
  const workingZoom = useCallback(() => {
    fitPending.current = false;
    setFitRequested(false);
    const target = workingZoomLevel(flow.getZoom());
    const role = boardFocusRole(model.nodes, selectedAgentId);
    if (role) {
      const centre = roleCardCentre(role.position);
      void flow.setCenter(centre.x, centre.y, { zoom: target });
    } else {
      void flow.zoomTo(target);
    }
    readBand(target);
  }, [flow, model.nodes, selectedAgentId, readBand]);

  // A zoom-out hides the per-card microactions with `display:none`, so they
  // leave the tab order. If the focus was sitting on one it would fall to the
  // document; move it to the strip instead, which stays at document scale and
  // carries the same actions. Focus is never left on something invisible.
  const actionsHidden = bandHidesNodeActions(band);
  useEffect(() => {
    if (!rescueFocus.current) return;
    rescueFocus.current = false;
    const strip = stripRef.current;
    (strip?.querySelector<HTMLElement>("button, summary") ?? strip)?.focus({ preventScroll: true });
  }, [actionsHidden, selectedAgentId]);

  // UX-02: opening the hire form puts the focus on its first field, so the
  // person starts at the beginning of the form rather than on its × control.
  // Opening writes nothing; this only moves the focus.
  useEffect(() => {
    if (!hireOpen) return;
    const timer = window.setTimeout(() => {
      const form = document.getElementById("hire-role-form");
      form?.querySelector<HTMLElement>("input:not([disabled]), select:not([disabled]), textarea:not([disabled]), button:not([disabled])")
        ?.focus({ preventScroll: true });
    }, 0);
    return () => window.clearTimeout(timer);
  }, [hireOpen]);

  return <div className="board-section">
    <div className="board-toolbar">

      <div className="board-toolbar-actions">
        {connection && !connectionOpen ? <button type="button" className="button button-secondary" onClick={() => setConnectionOpen(true)}>{text("agent_pending_connection")}</button> : null}
        <button type="button" className="button button-primary button-inline" disabled={!writesEnabled} title={text("board_hire_help")} onClick={() => onHireOpenChange(!hireOpen)} aria-expanded={hireOpen}>{text("board_hire")}</button>
        <button type="button" className="button button-secondary button-inline" disabled={!writesEnabled} title={text("board_department_help")} onClick={onAddDepartment}>{text("board_add_department")}</button>
        <button type="button" className="button button-secondary button-inline" title={text("board_fit_help")} onClick={requestFit}>{text("board_fit")}</button>
        <button type="button" className="button button-secondary button-inline" title={text("board_working_zoom_help")} onClick={workingZoom}>{text("board_working_zoom")}</button>
        <button type="button" className="button button-secondary button-inline" disabled={!writesEnabled} title={text("board_relayout_help")} onClick={relayout}>{text("board_relayout")}</button>
      </div>
    <details className="board-accessible-list"><summary title={text("board_list_help")}>{text("agent_list")}</summary><ul>{model.nodes.map((role) => <li key={role.id}><button className="notice-link" type="button" onClick={() => configure(role.id, "general")}>{role.name}</button> · {text(`board_activity_${role.activity}` as MessageKey)}</li>)}</ul></details>
    <details className="board-layers"><summary title={text("board_layers_help")}>{text("board_relations")}</summary><div><p>{text("board_hierarchy_help")}</p><label><input type="checkbox" checked={showCommunication} onChange={(event) => setShowCommunication(event.target.checked)} />{text("board_show_communication")}</label><label><input type="checkbox" checked={showCreators} onChange={(event) => setShowCreators(event.target.checked)} />{text("board_show_creators")}</label></div></details>
    </div>
    {loadState !== "ready" || status ? <p className="board-status" role="status">{loadState === "loading" ? text("board_loading") : loadState === "failed" ? text("board_unavailable") : status ? text(status) : null}</p> : null}

    {/* UX-01: the selected role's own strip, at normal document scale and
        outside the canvas transform, so it stays readable at any reading band.
        One row — not a sidebar and not a permanent inspector. */}
    <div className="board-selected-strip" ref={stripRef} tabIndex={-1} role="group" aria-label={text("board_selected_agent")}>
      {selectedRole === null
        ? <p className="board-selected-empty">{text("board_selected_none")}</p>
        : <>
          <span className="board-selected-title">{roleCopy(selectedRole).cardTitle}</span>
          <span className="board-selected-identity" title={selectedRole.name}>{selectedRole.name}</span>
          <span className="board-selected-state">{text(`board_activity_${selectedRole.activity}` as MessageKey)}</span>
          <span className="board-selected-tasks">{selectedRole.taskCount === 0 ? text("board_role_no_tasks") : `${text("board_role_tasks")}: ${selectedRole.taskCount} · ${selectedRole.openTaskCount} ${text("board_role_open_tasks")}`}</span>
          <RoleActions role={selectedRole} text={text} writesEnabled={writesEnabled} variant="strip"
            onConfigure={configure} onChild={onConfigureChild} onOpenTasks={onOpenTasks} onGiveTask={openPicker} />
        </>}
    </div>

    <div className="board-body">
      <div className="board-canvas" data-zoom-band={band} aria-label={text("shell_nav_board")}>
        {active || visited ? <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} onSelectionChange={({ nodes: selection }) => {
          const role = selection.find((node) => node.type === "role");
          if (role) setSelectedAgentId(role.id);
        }} onNodesChange={onNodesChange} onNodeDragStop={onNodeDragStop} onConnect={onConnect} onEdgeClick={(_, edge) => { if (edge.source === OWNER_NODE_ID) return; configure(edge.className === "board-edge-link" ? edge.source : edge.target, edge.className === "board-edge-link" ? "connections" : "general"); }}
          ariaLabelConfig={{ "controls.ariaLabel": text("task_graph.zoom"), "controls.zoomIn.ariaLabel": text("task_graph.zoomIn"), "controls.zoomOut.ariaLabel": text("task_graph.zoomOut"), "controls.fitView.ariaLabel": text("board_fit") }}
          defaultViewport={session.viewport ?? undefined} fitView={false} onMove={(event, viewport) => {
            if (event) { fitPending.current = false; setFitRequested(false); }
            if (!fitPending.current) session.setViewport(viewport);
            readBand(viewport.zoom);
          }}
          panOnScroll zoomOnScroll={false} zoomOnPinch minZoom={minimumZoom} maxZoom={1.6} nodesConnectable={writesEnabled} elementsSelectable proOptions={{ hideAttribution: true }} deleteKeyCode={null}>
          <Background gap={24} />
          <Controls showInteractive={false} />

        </ReactFlow> : null}
        {loadState === "ready" && model.nodes.length === 0 ? <div className="board-empty"><p>{text("board_empty")}</p></div> : null}
      </div>
      {hireOpen ? <Modal title={text("shell_board_hire_title")} closeLabel={text("shell_close")} onClose={() => onHireOpenChange(false)}>{hirePanel}</Modal> : null}
      {/* UX-03: the picker is the only path from a card to Prepare run, and it
          issues nothing itself. The Tasks-tab selection is handed in as a
          preselection, so an old selection on another tab can never direct the
          action to an unexpected task. */}
      {active && picker ? <BoardTaskPicker key={picker.agentId} agentName={model.nodes.find((node) => node.id === picker.agentId)?.name ?? picker.agentId}
        preselectedTaskId={selectedTaskId} tracker={tracker} text={text} writesEnabled={writesEnabled}
        onCancel={() => setPicker(null)}
        onChoose={(taskId) => { setPicker(null); onGiveTask(picker.agentId, taskId); }} /> : null}
      {active && settings ? <AgentSettings key={settings.id} session={session.editor(api, settings.id)} initialTab={settings.tab} text={text} roles={roles} writesEnabled={writesEnabled} onClose={() => setSettings(null)} onChanged={onChanged} /> : null}
      {active && connection && connectionOpen ? <Modal size="small" title={text("agent_open_link")} closeLabel={text("shell_close")} onClose={() => setConnectionOpen(false)}>
        <p>{roles.find((r) => r.id === connection.source)?.name} → {roles.find((r) => r.id === connection.target)?.name}</p>
        <label>{text("agent_link_type")}<select disabled={linkState !== "idle"} value={connection.kind} onChange={(e) => setConnection({ ...connection, kind: e.target.value as "ask" | "handoff_work" })}><option value="ask">{text("agent_link_ask")}</option><option value="handoff_work">{text("agent_link_handoff")}</option></select></label>
        <p className="small-copy">{text(connection.kind === "handoff_work" ? "agent_link_handoff_warning" : "agent_link_help")}</p>
        <p role="status">{linkState === "uncertain" ? text("agent_uncertain") : linkState === "failed" ? text("agent_failed") : linkState === "saved_refresh_failed" ? text("agent_saved_refresh_failed") : ""}</p>
        <button type="button" className="button button-primary" disabled={linkState === "saving" || !writesEnabled} onClick={() => void saveConnection()}>{text(connection.confirmed ? "refresh_status" : linkState === "idle" ? "agent_open_link" : "agent_retry_same")}</button>
      </Modal> : null}
    </div>
  </div>;
}
