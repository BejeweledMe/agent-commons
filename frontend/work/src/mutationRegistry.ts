/**
 * In-flight writes of the Work shell, keyed by {projectId, surface, operation}.
 *
 * The shell used to hold one `activeAction` string, so any write disabled every
 * button in the application — navigation, task selection, search, reading other
 * tasks, refresh and the Library included. This registry replaces that flag: it
 * records which write is running, on which surface, in which project, and since
 * when, and it says nothing about anything else.
 *
 * Two boundaries are deliberate:
 *
 * - The server holds one write lock, so a write on one surface still blocks a
 *   write on another. What changes is the reason a control can give, not the
 *   pretence that parallel writes would succeed. Reads never consult this.
 * - `startedAt` and the two thresholds below are presentation hints. They are
 *   not a latency target, not a timeout, and never decide an outcome: nothing
 *   here retries, cancels, or reinterprets a request. An outcome is still only
 *   what the server answered (owner decision 8.7 of the UX closure plan).
 *
 * The module is pure in the sense that matters for testing: no DOM, no global
 * clock. The clock is injected, so elapsed thresholds are deterministic.
 */

export const MUTATION_SURFACES = ["tasks", "roles", "launch", "setup", "provider-auth", "shell"] as const;

export type MutationSurface = (typeof MUTATION_SURFACES)[number];

export type MutationKey = {
  readonly projectId: string | null;
  readonly surface: MutationSurface;
  readonly operation: string;
};

export type MutationHandle = MutationKey & {
  readonly startedAt: number;
  /** Opaque token for the control that started the write (the shell passes the focused element); never inspected here. */
  readonly initiator: unknown;
};

/** After this much elapsed time the shell shows a counter and the large-project hint. */
export const MUTATION_ELAPSED_HINT_MS = 2_000;
/** After this much elapsed time the shell says the same key is still in flight. */
export const MUTATION_STILL_SAVING_HINT_MS = 8_000;

export type MutationHint = "none" | "elapsed" | "still_saving";

/**
 * What a write control on `surface` may say while `entries` are in flight:
 * either this surface's own write, or another surface's write holding the one
 * server lock, or nothing at all.
 */
export type MutationState =
  | { readonly kind: "idle" }
  | { readonly kind: "saving"; readonly handle: MutationHandle }
  | { readonly kind: "blocked"; readonly handle: MutationHandle };

const SURFACE_BY_OPERATION: Readonly<Record<string, MutationSurface>> = {
  "create-task": "tasks",
  "create-role": "roles",
  "start-run": "launch",
  initialize: "setup",
  runtime: "setup",
  "provider-auth-login": "provider-auth",
  "provider-auth-cancel": "provider-auth",
  "provider-auth-check": "provider-auth"
};

/**
 * The surface a named operation belongs to. An operation this table does not
 * name is attributed to the shell rather than to a surface it might not own:
 * that still blocks writes, and simply gives no surface-local reason.
 */
export function mutationSurface(operation: string): MutationSurface {
  return SURFACE_BY_OPERATION[operation] ?? "shell";
}

export function sameMutationKey(left: MutationKey, right: MutationKey): boolean {
  return left.projectId === right.projectId
    && left.surface === right.surface
    && left.operation === right.operation;
}

export function mutationHint(elapsedMs: number | null): MutationHint {
  if (elapsedMs === null || elapsedMs < MUTATION_ELAPSED_HINT_MS) return "none";
  if (elapsedMs < MUTATION_STILL_SAVING_HINT_MS) return "elapsed";
  return "still_saving";
}

export function mutationElapsedSeconds(elapsedMs: number): number {
  return Math.max(0, Math.floor(elapsedMs / 1_000));
}

export function mutationState(entries: readonly MutationHandle[], surface: MutationSurface): MutationState {
  const own = entries.find((entry) => entry.surface === surface);
  if (own !== undefined) return { kind: "saving", handle: own };
  const other = entries.at(0);
  return other === undefined ? { kind: "idle" } : { kind: "blocked", handle: other };
}

export class MutationRegistry {
  private readonly clock: () => number;
  private entries: readonly MutationHandle[] = [];
  private readonly listeners = new Set<() => void>();

  constructor(clock: () => number = () => Date.now()) {
    this.clock = clock;
  }

  /** Record a write as in flight. The returned handle is its only identity. */
  begin(key: MutationKey, initiator: unknown = null): MutationHandle {
    const handle: MutationHandle = {
      projectId: key.projectId,
      surface: key.surface,
      operation: key.operation,
      startedAt: this.clock(),
      initiator
    };
    this.entries = [...this.entries, handle];
    this.publish();
    return handle;
  }

  /**
   * Retire exactly the write this handle identifies. Ending an already-ended
   * write is a no-op, so a confirmed write may end its busy phase before its
   * follow-up read and still be ended again by its own `finally`.
   */
  end(handle: MutationHandle): void {
    const remaining = this.entries.filter((entry) => entry !== handle);
    if (remaining.length === this.entries.length) return;
    this.entries = remaining;
    this.publish();
  }

  /**
   * Writes in flight for one project, oldest first. A write in another project
   * is never reported here: it belongs to that project and gates only it.
   */
  inFlight(projectId: string | null, surface?: MutationSurface): readonly MutationHandle[] {
    return this.entries.filter((entry) => entry.projectId === projectId
      && (surface === undefined || entry.surface === surface));
  }

  /** Elapsed time of the oldest write matching this key, or null if none is in flight. */
  elapsedMs(key: MutationKey, now: number): number | null {
    let oldest: MutationHandle | null = null;
    for (const entry of this.entries) {
      if (!sameMutationKey(entry, key)) continue;
      if (oldest === null || entry.startedAt < oldest.startedAt) oldest = entry;
    }
    return oldest === null ? null : Math.max(0, now - oldest.startedAt);
  }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  }

  private publish(): void {
    for (const listener of [...this.listeners]) listener();
  }
}
