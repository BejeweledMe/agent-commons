import type { WorkApi } from "./api.js";
import { OutputsError, canViewImage, parseOutputList, parseOutputSummary, scopeKey, type ImageOutput, type LiveOutput, type OutputList, type OutputScope, type OutputSummary, type OutputVersions, type ReviewState } from "./outputsTypes.js";
import type { OutputMessage } from "./outputsStrings.js";

export type OutputStatus = Extract<OutputMessage, "latestResult" | "earlierViewable" | "earlierVersion" | "previewExpired" | "addressUnavailable" | "previewNotVerified" | "previewUnavailable" | "starting" | "reported_ready">;
/** Presentation only: a display label for facts the server already decided.
 *  A historical result stays historical; nothing here promotes it to current. */
export function outputStatus(item: ImageOutput | LiveOutput, now: number): OutputStatus {
  if (item.kind === "live_preview") {
    if (item.state === "stale") return "earlierVersion";
    if (item.state === "expired") return "previewExpired";
    if (now >= item.expiresAt) return "previewExpired";
    if (item.state === "unavailable") return "addressUnavailable";
    return item.state;
  }
  if (canViewImage(item)) return item.state === "ready" && item.latest ? "latestResult" : "earlierViewable";
  if (item.state === "unchecked") return "previewNotVerified";
  if (item.state === "unavailable") return "previewUnavailable";
  return "earlierVersion";
}
const REVIEW_LABELS = { awaiting: "reviewAwaiting", approved: "reviewApproved", returned: "reviewReturned" } as const;
/** Only a server-supplied review state produces a review label; freshness never does. */
export function reviewLabel(state: ReviewState | null): OutputMessage | null { return state === null ? null : REVIEW_LABELS[state]; }

type Transport = Pick<WorkApi, "readOutputs" | "readOutputImage">;
export class OutputsApi {
  private requests = new Set<AbortController>();
  private summaries = new Map<string, Promise<OutputSummary>>();
  private revision = "";
  private summaryExpiry = new Map<string, number>();
  constructor(private readonly transport: Transport) {}
  dispose(): void { for (const controller of this.requests) controller.abort(); this.requests.clear(); this.summaries.clear(); this.summaryExpiry.clear(); }
  summary(scope: OutputScope, revision = ""): Promise<OutputSummary> {
    if (this.revision !== revision) { this.dispose(); this.revision = revision; }
    const key = scopeKey(scope), cached = this.summaries.get(key); if (cached && (this.summaryExpiry.get(key) ?? 0) > Date.now()) return cached;
    const controller = new AbortController(); this.requests.add(controller);
    const request = this.transport.readOutputs(scope.kind, scope.id, "latest", true, controller.signal)
      .then((value) => parseOutputSummary(value, scope)).catch((error: unknown) => {
        if (this.summaries.get(key) === request) this.summaries.delete(key);
        throw error;
      }).finally(() => this.requests.delete(controller));
    this.summaries.set(key, request); this.summaryExpiry.set(key, Date.now() + 14_000); return request;
  }
  async list(scope: OutputScope, versions: OutputVersions, signal: AbortSignal): Promise<OutputList> {
    scopeKey(scope); return parseOutputList(await this.transport.readOutputs(scope.kind, scope.id, versions, false, signal), scope, versions);
  }
  async preview(scope: OutputScope, item: ImageOutput, versions: OutputVersions, signal: AbortSignal): Promise<Blob> {
    if (!canViewImage(item)) throw new OutputsError();
    const blob = await this.transport.readOutputImage(item.artifactId, signal);
    if (signal.aborted || blob.type !== item.mediaType || blob.size === 0 || blob.size > 10 * 1024 * 1024) throw new OutputsError();
    const digest = await crypto.subtle.digest("SHA-256", await blob.arrayBuffer());
    const hash = `sha256:${Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("")}`;
    if (hash !== item.contentRevision || signal.aborted) throw new OutputsError();
    const fresh = await this.list(scope, versions, signal);
    if (!fresh.items.some((current) => current.kind === item.kind && current.outputId === item.outputId && canViewImage(current) && current.contentRevision === item.contentRevision) || signal.aborted) throw new OutputsError();
    return blob;
  }
}
/** Abort suppresses both late success and late failure, even when transport ignores it. */
export async function presentCurrent<T>(request: Promise<T>, signal: AbortSignal, ready: (value: T) => void, failed: () => void): Promise<void> {
  try { const value = await request; if (!signal.aborted) ready(value); }
  catch { if (!signal.aborted) failed(); }
}
