/**
 * Local, opt-in, content-free usage counters for this panel.
 *
 * Everything here is deliberately closed. An event has exactly three fields and
 * each one is a small union, so an identifier, a title, a path, an attachment
 * name or a raw timestamp cannot be represented at all, let alone transmitted.
 * There is no storage access and no destination other than this panel's own
 * local API.
 */

export const INSTRUMENTATION_KINDS = [
  "task_create",
  "task_action",
  "run_prepare",
  "run_start",
  "library_edit",
  "board_edit",
  "navigation"
] as const;
export const INSTRUMENTATION_OUTCOMES = ["confirmed", "refused", "uncertain", "cancelled"] as const;
export const INSTRUMENTATION_BUCKETS = ["lt_1s", "lt_2s", "lt_8s", "ge_8s"] as const;

export type InstrumentationKind = (typeof INSTRUMENTATION_KINDS)[number];
export type InstrumentationOutcome = (typeof INSTRUMENTATION_OUTCOMES)[number];
export type InstrumentationBucket = (typeof INSTRUMENTATION_BUCKETS)[number];

/** The whole payload vocabulary: three fields, each a closed union. */
export type InstrumentationEvent = {
  kind: InstrumentationKind;
  outcome: InstrumentationOutcome;
  duration_bucket: InstrumentationBucket;
};

/** The server accepts at most this many entries per call, so the batcher never sends more. */
export const MAX_INSTRUMENTATION_BATCH = 64;
const DEFAULT_FLUSH_DELAY_MS = 2000;

function has(values: readonly string[], candidate: unknown): boolean {
  return typeof candidate === "string" && values.includes(candidate);
}

export function isInstrumentationKind(value: unknown): value is InstrumentationKind {
  return has(INSTRUMENTATION_KINDS, value);
}

export function isInstrumentationOutcome(value: unknown): value is InstrumentationOutcome {
  return has(INSTRUMENTATION_OUTCOMES, value);
}

export function isInstrumentationBucket(value: unknown): value is InstrumentationBucket {
  return has(INSTRUMENTATION_BUCKETS, value);
}

/**
 * The measured duration of one completed action as a coarse bucket. A value that
 * is not a finite, non-negative number has no bucket, so it produces no event.
 */
export function durationBucket(durationMs: number): InstrumentationBucket | null {
  if (typeof durationMs !== "number" || !Number.isFinite(durationMs) || durationMs < 0) return null;
  if (durationMs < 1000) return "lt_1s";
  if (durationMs < 2000) return "lt_2s";
  if (durationMs < 8000) return "lt_8s";
  return "ge_8s";
}

/**
 * The one way an event is ever constructed. Anything outside the closed
 * vocabulary returns `null`, which the batcher drops rather than repairs.
 */
export function instrumentationEvent(
  kind: string,
  outcome: string,
  durationMs: number
): InstrumentationEvent | null {
  const bucket = durationBucket(durationMs);
  if (bucket === null || !isInstrumentationKind(kind) || !isInstrumentationOutcome(outcome)) {
    return null;
  }
  return { kind, outcome, duration_bucket: bucket };
}

/**
 * The coarse outcome of an action that did not finish, from what the panel
 * already knows: the person aborted it, the server refused it with a typed
 * 4xx, or it ended without an answer either way.
 */
export function failureOutcome(aborted: boolean, status: number | null): InstrumentationOutcome {
  if (aborted) return "cancelled";
  if (status !== null && status >= 400 && status < 500) return "refused";
  return "uncertain";
}

/** A payload the panel is willing to post: exact keys, every value allowlisted. */
export function isInstrumentationEvent(value: unknown): value is InstrumentationEvent {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const keys = Object.keys(value).sort();
  if (keys.length !== 3 || keys[0] !== "duration_bucket" || keys[1] !== "kind" || keys[2] !== "outcome") {
    return false;
  }
  const event = value as Record<string, unknown>;
  return isInstrumentationKind(event.kind)
    && isInstrumentationOutcome(event.outcome)
    && isInstrumentationBucket(event.duration_bucket);
}

/** What the panel's own API said about one posted batch. */
export type InstrumentationPostOutcome =
  | { kind: "accepted"; revision: string }
  /** 204: the server is not recording, so the batch is simply forgotten. */
  | { kind: "ignored" }
  /** 409: the revision moved; re-read and hint, never loop-retry. */
  | { kind: "stale" }
  /** 422: the server refused the shape; hint and drop the batch. */
  | { kind: "rejected" }
  /** Anything else, including an aborted request: drop the batch silently. */
  | { kind: "unavailable" };

/** The one-line hint shown next to the toggle. `null` clears it. */
export type InstrumentationHint = "stale" | "rejected";

export type InstrumentationBatcherOptions = {
  post: (events: readonly InstrumentationEvent[], expectedRevision: string) => Promise<InstrumentationPostOutcome>;
  /** The revision the panel must adopt after an accepted batch. */
  onRevision?: (revision: string) => void;
  /** A refusal to show beside the toggle, or `null` when there is nothing to say. */
  onHint?: (hint: InstrumentationHint | null) => void;
  /** Ask the panel to re-read GET /api/instrumentation after a stale revision. */
  onReread?: () => void;
  flushDelayMs?: number;
};

/**
 * Holds at most one batch, flushes on a short timer or as soon as it is full,
 * and is inert while the toggle is off. Nothing is written to browser storage:
 * a queue that is never flushed is simply lost, which is the intended behaviour
 * for counters that are not worth keeping.
 */
export class InstrumentationBatcher {
  private queue: InstrumentationEvent[] = [];
  private enabled = false;
  private revision: string | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private sending = false;
  private readonly options: InstrumentationBatcherOptions;

  constructor(options: InstrumentationBatcherOptions) {
    this.options = options;
  }

  /** Turning the toggle off stops emission immediately and drops what was queued. */
  setEnabled(enabled: boolean): void {
    this.enabled = enabled;
    if (!enabled) {
      this.queue = [];
      this.cancelTimer();
    }
  }

  /** The revision the last GET returned. Without one, nothing can be posted. */
  setRevision(revision: string | null): void {
    this.revision = revision;
  }

  /** Visible for the Settings panel and for tests; never a reason to act. */
  pending(): number {
    return this.queue.length;
  }

  record(kind: string, outcome: string, durationMs: number): void {
    if (!this.enabled) return;
    const event = instrumentationEvent(kind, outcome, durationMs);
    if (event === null) return;
    this.queue.push(event);
    // Bounded by construction: a queue that cannot be flushed yet drops its
    // oldest counts instead of growing. Counters are not worth memory pressure.
    if (this.queue.length > MAX_INSTRUMENTATION_BATCH) {
      this.queue = this.queue.slice(this.queue.length - MAX_INSTRUMENTATION_BATCH);
    }
    if (this.queue.length >= MAX_INSTRUMENTATION_BATCH && !this.sending) {
      this.cancelTimer();
      void this.flush();
      return;
    }
    this.scheduleFlush();
  }

  private scheduleFlush(): void {
    if (this.timer !== null) return;
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.flush();
    }, this.options.flushDelayMs ?? DEFAULT_FLUSH_DELAY_MS);
  }

  private cancelTimer(): void {
    if (this.timer === null) return;
    clearTimeout(this.timer);
    this.timer = null;
  }

  async flush(): Promise<void> {
    const revision = this.revision;
    if (!this.enabled || this.sending || revision === null || this.queue.length === 0) return;
    // The batch leaves the queue before the request: a refusal drops it rather
    // than replaying it, so one bad batch can never become a retry loop.
    const batch = this.queue.slice(0, MAX_INSTRUMENTATION_BATCH);
    this.queue = this.queue.slice(batch.length);
    this.sending = true;
    let outcome: InstrumentationPostOutcome;
    try {
      outcome = await this.options.post(batch, revision);
    } catch {
      outcome = { kind: "unavailable" };
    } finally {
      this.sending = false;
    }
    if (outcome.kind === "accepted") {
      this.revision = outcome.revision;
      this.options.onRevision?.(outcome.revision);
      this.options.onHint?.(null);
    } else if (outcome.kind === "stale") {
      this.options.onHint?.("stale");
      this.options.onReread?.();
    } else if (outcome.kind === "rejected") {
      this.options.onHint?.("rejected");
    }
    // Whatever was recorded while this batch was in flight still gets its own
    // timer. The batch that just left is gone either way, so this is no retry.
    if (this.enabled && this.queue.length > 0) this.scheduleFlush();
  }

  /** Drop everything queued and stop the timer, without changing the toggle. */
  reset(): void {
    this.queue = [];
    this.cancelTimer();
  }
}

/**
 * The shell owns exactly one batcher and installs it here, so a completion point
 * anywhere in the panel can report its own outcome without carrying a client, a
 * revision or the toggle through its props. With no batcher installed -- before
 * the first read, and in every test that does not ask for one -- every call is a
 * no-op.
 */
let sink: InstrumentationBatcher | null = null;

export function setInstrumentationSink(batcher: InstrumentationBatcher | null): void {
  sink = batcher;
}

export function recordInstrumentation(kind: string, outcome: string, durationMs: number): void {
  sink?.record(kind, outcome, durationMs);
}
