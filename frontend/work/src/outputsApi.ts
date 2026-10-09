import type { WorkApi } from "./api.js";
import { OutputsError, canReadReport, parseTextResultContent, type TextResultContent, canViewImage, canDownloadBuild, parseOutputList, parseOutputSummary, scopeKey, type ImageOutput, type LiveOutput, type OutputList, type OutputScope, type OutputSummary, type OutputVersions, type ReviewState } from "./outputsTypes.js";
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
  if (canViewImage(item) || canDownloadBuild(item) || canReadReport(item)) return item.state === "ready" && item.latest ? "latestResult" : "earlierViewable";
  if (item.state === "unchecked") return "previewNotVerified";
  if (item.state === "unavailable") return "previewUnavailable";
  return "earlierVersion";
}
const REVIEW_LABELS = { awaiting: "reviewAwaiting", approved: "reviewApproved", returned: "reviewReturned" } as const;
/** Only a server-supplied review state produces a review label; freshness never does. */
export function reviewLabel(state: ReviewState | null): OutputMessage | null { return state === null ? null : REVIEW_LABELS[state]; }

type Transport = Pick<WorkApi, "readOutputs" | "readOutputImage"> & Partial<Pick<WorkApi, "readOutputBuild" | "readOutputText">>;
export class OutputsApi {
  private requests = new Set<AbortController>();
  private summaries = new Map<string, Promise<OutputSummary>>();
  private revision = "";
  private summaryExpiry = new Map<string, number>();
  constructor(private transport: Transport) {}
  /** The owning workspace replaces authentication for this same project only. */
  setTransport(transport: Transport): void { this.transport = transport; }
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
  async text(scope: OutputScope, item: ImageOutput, signal: AbortSignal): Promise<TextResultContent> {
    if (!canReadReport(item) || !this.transport.readOutputText) throw new OutputsError();
    const content = parseTextResultContent(await this.transport.readOutputText(item.artifactId, item.artifactRevision, scope.kind, scope.id, signal), scope, item);
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(content.content));
    if (signal.aborted || `sha256:${Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("")}` !== item.contentRevision) throw new OutputsError();
    return content;
  }
  async build(scope: OutputScope, item: ImageOutput, signal: AbortSignal): Promise<Blob> {
    if (!canDownloadBuild(item) || !this.transport.readOutputBuild) throw new OutputsError();
    const blob = await this.transport.readOutputBuild(item.artifactId, item.artifactRevision, scope.kind, scope.id, signal);
    if (blob.type !== "application/zip" || blob.size < 1 || blob.size > 20 * 1024 * 1024 || signal.aborted) throw new OutputsError();
    const digest = await crypto.subtle.digest("SHA-256", await blob.arrayBuffer());
    if (`sha256:${Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("")}` !== item.contentRevision || signal.aborted) throw new OutputsError();
    const fresh = await this.list(scope, "all", signal);
    if (!fresh.items.some((current) => current.kind === "static_build" && current.outputId === item.outputId && canDownloadBuild(current) && current.contentRevision === item.contentRevision) || signal.aborted) throw new OutputsError();
    return blob;
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

/** Bound automatic gallery byte checks, including their follow-up scoped reads.
 * Each caller owns its abort signal; a cancelled queued card never starts work. */
export class OutputPreviewQueue {
  private active = 0;
  private pending: (() => void)[] = [];
  constructor(private readonly limit = 2) {}
  run<T>(work: () => Promise<T>, signal: AbortSignal): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      let started = false, settled = false;
      const cancel = (): void => {
        if (settled) return;
        settled = true;
        if (!started) this.pending = this.pending.filter((entry) => entry !== start);
        signal.removeEventListener("abort", cancel);
        reject(new OutputsError());
      };
      const start = (): void => {
        if (settled || signal.aborted) { cancel(); return; }
        started = true; this.active++;
        void Promise.resolve().then(() => { if (signal.aborted) throw new OutputsError(); return work(); }).then(
          (value) => { if (!settled) { settled = true; resolve(value); } },
          (error: unknown) => { if (!settled) { settled = true; reject(error); } },
        ).finally(() => {
          signal.removeEventListener("abort", cancel);
          this.active--;
          while (this.active < this.limit && this.pending.length) this.pending.shift()?.();
        });
      };
      signal.addEventListener("abort", cancel, { once: true });
      if (signal.aborted) cancel();
      else if (this.active < this.limit) start();
      else this.pending.push(start);
    });
  }
}
