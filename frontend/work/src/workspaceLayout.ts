/**
 * The operator's arrangement of the three work-area panes: the narrow project
 * sidebar, the dominant task map and the compact main project chat.
 *
 * This is private operational state, like the board arrangement: it lives on
 * the server under the state root behind `GET/POST /api/workspace/preferences`
 * and never in browser storage. A fresh backend, an unreadable answer or a
 * refused write all leave the in-memory default in place, so a lost
 * arrangement never hides the map behind a setup screen.
 */
export const SIDEBAR_MIN_WIDTH = 160;
export const SIDEBAR_MAX_WIDTH = 320;
export const SIDEBAR_DEFAULT_WIDTH = 208;
export const CHAT_MIN_WIDTH = 280;
export const CHAT_MAX_WIDTH = 560;
export const CHAT_DEFAULT_WIDTH = 320;
/** Widths move in 8px steps so a bounded set of CSS classes can express them. */
export const WIDTH_STEP = 8;

export type WorkspaceLayout = Readonly<{
  sidebarWidth: number;
  chatWidth: number;
  sidebarCollapsed: boolean;
  chatCollapsed: boolean;
  panesSwapped: boolean;
}>;

export const DEFAULT_WORKSPACE_LAYOUT: WorkspaceLayout = Object.freeze({
  sidebarWidth: SIDEBAR_DEFAULT_WIDTH,
  chatWidth: CHAT_DEFAULT_WIDTH,
  sidebarCollapsed: false,
  chatCollapsed: false,
  panesSwapped: false
});

export const WORKSPACE_PREFERENCES_SCHEMA = "agent_commons.workspace-preferences.v1";
export const WORKSPACE_PREFERENCES_CONFLICT = "workspace_preferences_conflict";

/** Clamp to the pane's bounds and snap to the nearest 8px step. */
export function snapPaneWidth(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min;
  const stepped = Math.round(value / WIDTH_STEP) * WIDTH_STEP;
  return Math.min(max, Math.max(min, stepped));
}

export function snapSidebarWidth(value: number): number {
  return snapPaneWidth(value, SIDEBAR_MIN_WIDTH, SIDEBAR_MAX_WIDTH);
}

export function snapChatWidth(value: number): number {
  return snapPaneWidth(value, CHAT_MIN_WIDTH, CHAT_MAX_WIDTH);
}

/** Every width step a pane may take, so the stylesheet can enumerate them. */
export function paneWidthSteps(min: number, max: number): readonly number[] {
  const steps: number[] = [];
  for (let width = min; width <= max; width += WIDTH_STEP) steps.push(width);
  return steps;
}

/**
 * The class that carries a pane width. The width itself is a custom property
 * set by the stylesheet, never an inline `style` attribute: the CSP carries no
 * `style-src-attr`.
 */
export function paneWidthClass(pane: "sidebar" | "chat", width: number): string {
  const snapped = pane === "sidebar" ? snapSidebarWidth(width) : snapChatWidth(width);
  return `pane-${pane}-${snapped}`;
}

export function workspaceShellClasses(layout: WorkspaceLayout): string {
  return ["work-shell", paneWidthClass("sidebar", layout.sidebarWidth), paneWidthClass("chat", layout.chatWidth)].join(" ");
}

export function sameWorkspaceLayout(a: WorkspaceLayout, b: WorkspaceLayout): boolean {
  return a.sidebarWidth === b.sidebarWidth && a.chatWidth === b.chatWidth
    && a.sidebarCollapsed === b.sidebarCollapsed && a.chatCollapsed === b.chatCollapsed
    && a.panesSwapped === b.panesSwapped;
}

export function normalizeWorkspaceLayout(layout: WorkspaceLayout): WorkspaceLayout {
  return {
    sidebarWidth: snapSidebarWidth(layout.sidebarWidth),
    chatWidth: snapChatWidth(layout.chatWidth),
    sidebarCollapsed: layout.sidebarCollapsed === true,
    chatCollapsed: layout.chatCollapsed === true,
    panesSwapped: layout.panesSwapped === true
  };
}

export type WorkspacePreferences = Readonly<{ revision: number; layout: WorkspaceLayout }>;

function integerAt(row: Record<string, unknown>, key: string, min: number, max: number): number {
  const value = row[key];
  if (!Number.isSafeInteger(value) || Number(value) < min || Number(value) > max) throw new Error("workspace_preferences");
  return Number(value);
}

function flagAt(row: Record<string, unknown>, key: string): boolean {
  const value = row[key];
  if (typeof value !== "boolean") throw new Error("workspace_preferences");
  return value;
}

/**
 * A closed DTO: every one of the five layout values and the revision must be
 * present and in range. A width the server reports off-step is snapped for
 * presentation, never rejected, so an older arrangement still opens.
 */
export function parseWorkspacePreferences(value: unknown): WorkspacePreferences {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("workspace_preferences");
  const row = value as Record<string, unknown>;
  if (row.schema !== WORKSPACE_PREFERENCES_SCHEMA) throw new Error("workspace_preferences");
  return {
    revision: integerAt(row, "revision", 0, Number.MAX_SAFE_INTEGER),
    layout: normalizeWorkspaceLayout({
      sidebarWidth: integerAt(row, "sidebar_width", SIDEBAR_MIN_WIDTH, SIDEBAR_MAX_WIDTH),
      chatWidth: integerAt(row, "chat_width", CHAT_MIN_WIDTH, CHAT_MAX_WIDTH),
      sidebarCollapsed: flagAt(row, "sidebar_collapsed"),
      chatCollapsed: flagAt(row, "chat_collapsed"),
      panesSwapped: flagAt(row, "panels_swapped")
    })
  };
}

/** The exact body the route expects: the revision plus all five values. */
export function workspacePreferencesBody(expectedRevision: number, layout: WorkspaceLayout): Readonly<Record<string, unknown>> {
  const next = normalizeWorkspaceLayout(layout);
  return {
    expected_revision: expectedRevision,
    sidebar_width: next.sidebarWidth,
    chat_width: next.chatWidth,
    sidebar_collapsed: next.sidebarCollapsed,
    chat_collapsed: next.chatCollapsed,
    panels_swapped: next.panesSwapped
  };
}

/**
 * What the person is told about their arrangement. `saving` and `saved` are
 * separate outcomes; `unavailable` means the arrangement is in memory only;
 * `conflict` keeps the layout they wanted and offers an explicit reload of the
 * other window's arrangement, which is never applied behind their back.
 */
export type LayoutSaveState =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved" }
  | { kind: "unavailable" }
  | { kind: "conflict"; serverLayout: WorkspaceLayout | null; serverRevision: number | null };

export type LayoutPane = "sidebar" | "chat";

/**
 * In-memory owner of the arrangement. It publishes to subscribers on every
 * change, but a save is only requested when a gesture ends or a toggle is
 * pressed — never once per mouse pixel.
 */
export class WorkspaceLayoutStore {
  private layout: WorkspaceLayout = DEFAULT_WORKSPACE_LAYOUT;
  private revision: number | null = null;
  private save: LayoutSaveState = { kind: "idle" };
  private listeners = new Set<() => void>();
  private dragging: LayoutPane | null = null;
  private edited = false;
  get hasLocalChanges(): boolean { return this.edited; }

  snapshot = (): Readonly<{ layout: WorkspaceLayout; revision: number | null; save: LayoutSaveState; dragging: LayoutPane | null }> =>
    ({ layout: this.layout, revision: this.revision, save: this.save, dragging: this.dragging });

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  private publish(): void {
    for (const listener of this.listeners) listener();
  }

  /** The server's answer replaces the arrangement only while nothing is in hand. */
  adopt(preferences: WorkspacePreferences, preserveLocal = false): void {
    this.revision = preferences.revision;
    if (this.dragging === null && !preserveLocal) { this.layout = preferences.layout; this.edited = false; }
    this.save = { kind: "idle" };
    this.publish();
  }

  /** No readable arrangement: the safe default stays, and the person is told. */
  unavailable(): void {
    this.save = { kind: "unavailable" };
    this.publish();
  }

  beginDrag(pane: LayoutPane): void {
    this.dragging = pane;
    this.publish();
  }

  /** Live feedback during a drag: local only, never a request. */
  resize(pane: LayoutPane, width: number): void {
    const next = pane === "sidebar"
      ? { ...this.layout, sidebarWidth: snapSidebarWidth(width) }
      : { ...this.layout, chatWidth: snapChatWidth(width) };
    if (sameWorkspaceLayout(next, this.layout)) return;
    this.layout = next;
    this.edited = true;
    this.publish();
  }

  endDrag(): WorkspaceLayout {
    this.dragging = null;
    this.publish();
    return this.layout;
  }

  /** A toggle or a reset: one discrete change, saved once. */
  apply(changes: Partial<WorkspaceLayout>): WorkspaceLayout {
    this.layout = normalizeWorkspaceLayout({ ...this.layout, ...changes });
    this.edited = true;
    this.publish();
    return this.layout;
  }

  restoreDefault(): WorkspaceLayout {
    this.layout = DEFAULT_WORKSPACE_LAYOUT;
    this.edited = true;
    this.publish();
    return this.layout;
  }

  saving(): void {
    this.save = { kind: "saving" };
    this.publish();
  }

  saved(revision: number, written: WorkspaceLayout = this.layout): void {
    this.revision = revision;
    this.edited = !sameWorkspaceLayout(this.layout, written);
    this.save = { kind: this.edited ? "saving" : "saved" };
    this.publish();
  }

  /**
   * Another window wrote a different arrangement. The person keeps the layout
   * they just asked for; the offered reload is explicit.
   */
  conflicted(serverLayout: WorkspaceLayout | null, serverRevision: number | null): void {
    this.save = { kind: "conflict", serverLayout, serverRevision };
    this.publish();
  }

  /** Take the other window's arrangement, only when the person asks for it. */
  acceptConflict(): WorkspaceLayout | null {
    if (this.save.kind !== "conflict" || this.save.serverLayout === null) return null;
    this.layout = this.save.serverLayout;
    this.edited = false;
    this.revision = this.save.serverRevision;
    this.save = { kind: "idle" };
    this.publish();
    return this.layout;
  }
}


type PreferenceTransport = {
  readWorkspacePreferences(signal: AbortSignal): Promise<unknown>;
  writeWorkspacePreferences(body: Record<string, unknown>, signal: AbortSignal): Promise<unknown>;
};
/** A project-owned write queue. Uncertain requests retain their exact CAS body;
 * a newer gesture waits behind it and can never race our own pending request. */
export class WorkspacePreferenceSync {
  private api: PreferenceTransport | null = null;
  private reading: Promise<void> | null = null;
  private writing: Promise<void> | null = null;
  private desired: WorkspaceLayout | null = null;
  private unresolved: { body: Record<string, unknown>; layout: WorkspaceLayout } | null = null;
  private stopped = false;
  constructor(readonly store: WorkspaceLayoutStore) {}
  connect(api: PreferenceTransport): Promise<void> {
    this.api = api;
    return this.store.snapshot().revision === null ? this.read() : Promise.resolve();
  }
  request(layout: WorkspaceLayout): Promise<void> {
    this.desired = layout;
    return this.flush();
  }
  async read(): Promise<void> {
    if (!this.api || this.reading || this.writing || this.unresolved) return this.reading ?? Promise.resolve();
    const api = this.api;
    this.reading = (async () => {
      try {
        const value = parseWorkspacePreferences(await api.readWorkspacePreferences(new AbortController().signal));
        // An initial read may arrive after any number of local gestures.
        this.store.adopt(value, this.store.hasLocalChanges);
        if (this.store.hasLocalChanges) this.desired = this.store.snapshot().layout;
      } catch { this.store.unavailable(); }
    })();
    await this.reading; this.reading = null;
    if (this.store.snapshot().revision !== null) await this.flush();
  }
  retry(): Promise<void> {
    this.stopped = false;
    return this.store.snapshot().revision === null ? this.read() : this.flush();
  }
  async acceptConflict(): Promise<void> {
    if (this.store.acceptConflict() === null) {
      if (!this.api) return;
      try {
        const value = parseWorkspacePreferences(await this.api.readWorkspacePreferences(new AbortController().signal));
        this.store.adopt(value);
      } catch { this.store.unavailable(); return; }
    }
    this.desired = null; this.unresolved = null; this.stopped = false;
  }
  private async flush(): Promise<void> {
    if (!this.api || this.reading || this.stopped) return;
    if (this.writing) return this.writing;
    const revision = this.store.snapshot().revision;
    if (revision === null) return;
    const intent = this.unresolved ?? (this.desired ? { body: workspacePreferencesBody(revision, this.desired) as Record<string, unknown>, layout: this.desired } : null);
    if (!intent) return;
    this.unresolved = intent;
    const api = this.api;
    this.store.saving();
    this.writing = (async () => {
      try {
        const confirmed = parseWorkspacePreferences(await api.writeWorkspacePreferences(intent.body, new AbortController().signal));
        if (confirmed.revision <= Number(intent.body.expected_revision) || !sameWorkspaceLayout(confirmed.layout, intent.layout)) throw new Error("workspace_preferences_response_mismatch");
        this.unresolved = null;
        if (this.desired && sameWorkspaceLayout(this.desired, intent.layout)) this.desired = null;
        this.store.saved(confirmed.revision, intent.layout);
      } catch (error) {
        this.stopped = true;
        if ((error as { apiError?: { code?: string } })?.apiError?.code === WORKSPACE_PREFERENCES_CONFLICT) {
          this.unresolved = null;
          try { const value = parseWorkspacePreferences(await api.readWorkspacePreferences(new AbortController().signal)); this.store.conflicted(value.layout, value.revision); }
          catch { this.store.conflicted(null, null); }
        } else this.store.unavailable();
      }
    })();
    await this.writing; this.writing = null;
    if (!this.stopped && this.desired) await this.flush();
  }
}
