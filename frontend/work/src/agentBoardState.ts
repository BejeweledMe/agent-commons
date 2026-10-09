import type { WorkApi } from "./api.js";
import { AgentEditorSession } from "./agentSettings.js";

export type AgentBoardViewport = { x: number; y: number; zoom: number };
export type BoardConnection = { source: string; target: string; kind: "ask" | "handoff_work"; key: string; confirmed: boolean };
export type BoardLinkState = "idle" | "saving" | "uncertain" | "failed" | "saved_refresh_failed";

/** Project-owned RAM state. Component unmounts must not discard retry identity. */
export class AgentBoardSession {
  viewport: AgentBoardViewport | null = null;
  visited = false;
  connection: BoardConnection | null = null;
  linkState: BoardLinkState = "idle";
  private editors = new Map<string, AgentEditorSession>();
  private listeners = new Set<() => void>();
  private version = 0;
  subscribe = (listener: () => void): (() => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  snapshot = (): number => this.version;
  private publish(): void { this.version++; for (const listener of this.listeners) listener(); }
  editor(api: WorkApi, id: string): AgentEditorSession {
    let editor = this.editors.get(id);
    if (!editor) { editor = new AgentEditorSession(api, id); this.editors.set(id, editor); }
    return editor;
  }
  setConnection(value: BoardConnection | null): void { this.connection = value; this.publish(); }
  setLinkState(value: BoardLinkState): void { this.linkState = value; this.publish(); }
  setViewport(value: AgentBoardViewport): void { this.viewport = { ...value }; }
}

export class AgentBoardSessions {
  private projects = new Map<string | null, AgentBoardSession>();
  forProject(id: string | null): AgentBoardSession {
    let session = this.projects.get(id);
    if (!session) { session = new AgentBoardSession(); this.projects.set(id, session); }
    return session;
  }
}
