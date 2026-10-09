import { ApiProblem, type WorkApi } from "./api.js";
import { OutputsError, reportChecks, reportProse } from "./outputsTypes.js";

export type ResultEvidence = Readonly<{ ref: Readonly<{ kind: "artifact"; id: string }>; revision: string; kind: "artifact" | "text_result"; title: string; summary: string | null; checks: readonly string[]; mediaType: string | null; contentRevision: string | null; retained: boolean; stale: boolean }>;
export type TaskResultsPage = Readonly<{ taskId: string; revision: string; title: string; description: string | null; acceptanceCriteria: readonly string[] | null; summary: string | null; evidence: readonly ResultEvidence[]; total: number; offset: number; limit: number; nextOffset: number | null }>;
const typed = (value: unknown, kind: string): value is string => typeof value === "string" && new RegExp(`^${kind}\\.[0-7][0-9A-HJKMNP-TV-Z]{25}$`).test(value);
const fail = (): never => { throw new OutputsError(); };
const object = (value: unknown): Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : fail();
const nonnegative = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
export function parseTaskResultsPage(value: unknown, taskId: string, revision: string, offset: number, limit: number): TaskResultsPage {
  const row = object(value);
  if (!typed(taskId, "task") || !typed(revision, "evt") || row.schema !== "agent_commons.task-results.v1" || row.task_id !== taskId || row.task_revision !== revision
    || row.offset !== offset || row.limit !== limit || !nonnegative(row.total) || !nonnegative(offset) || !Number.isSafeInteger(limit) || limit < 1 || limit > 64
    || !Array.isArray(row.evidence) || row.evidence.length !== Math.min(limit, Math.max(0, row.total - offset)) || offset > row.total
    || row.next_offset !== (offset + row.evidence.length < row.total ? offset + row.evidence.length : null)) return fail();
  const seen = new Set<string>();
  const evidence = row.evidence.map((value): ResultEvidence => {
    const item = object(value), ref = object(item.ref);
    if (ref.kind !== "artifact" || !typed(ref.id, "artifact") || !typed(item.revision, "evt") || !["artifact", "text_result"].includes(String(item.kind))
      || typeof item.retained !== "boolean" || typeof item.stale !== "boolean" || (item.media_type !== null && typeof item.media_type !== "string")
      || (item.content_revision !== null && (typeof item.content_revision !== "string" || !/^sha256:[a-f0-9]{64}$/.test(item.content_revision)))) return fail();
    const key = `${ref.id}@${item.revision}`; if (seen.has(key)) return fail(); seen.add(key);
    return { ref: { kind: "artifact", id: ref.id }, revision: item.revision, kind: item.kind as ResultEvidence["kind"], title: reportProse(item.title, 4096), summary: item.summary === null ? null : reportProse(item.summary, 4096), checks: reportChecks(item.checks), mediaType: item.media_type as string | null, contentRevision: item.content_revision as string | null, retained: item.retained, stale: item.stale };
  });
  return { taskId, revision, title: reportProse(row.title, 4096), description: row.description === undefined ? null : reportProse(row.description, 262144), acceptanceCriteria: row.acceptance_criteria === undefined ? null : Array.isArray(row.acceptance_criteria) && row.acceptance_criteria.length <= 256 ? row.acceptance_criteria.map((item) => reportProse(item, 262144)) : fail(), summary: row.summary === null ? null : reportProse(row.summary, 262144), evidence, total: row.total, offset, limit, nextOffset: row.next_offset as number | null };
}
export type TaskResultsState = Readonly<{ page: TaskResultsPage | null; loading: boolean; error: "changed" | "unavailable" | null }>;
/** One immutable task revision owns all pages. Disposal suppresses ignored aborts. */
export class TaskResultsReader {
  private value: TaskResultsState = { page: null, loading: false, error: null };
  private listeners = new Set<() => void>();
  private request: AbortController | null = null;
  private generation = 0;
  constructor(private api: Pick<WorkApi, "readTaskResults">, readonly taskId: string, readonly revision: string) {}
  setApi(api: Pick<WorkApi, "readTaskResults">): void { this.api = api; }
  snapshot = (): TaskResultsState => this.value;
  subscribe = (listener: () => void): (() => void) => { this.listeners.add(listener); return () => this.listeners.delete(listener); };
  private set(value: TaskResultsState): void { this.value = value; for (const listener of this.listeners) listener(); }
  dispose(): void {
    this.value = { ...this.value, loading: false }; this.generation++; this.request?.abort(); this.request = null; }
  async loadMore(): Promise<void> {
    if (this.value.loading || this.value.error === "changed" || (this.value.page && this.value.page.nextOffset === null)) return;
    const previous = this.value.page, offset = previous?.nextOffset ?? 0, limit = 32, generation = ++this.generation;
    const controller = new AbortController(); this.request = controller; this.set({ ...this.value, loading: true, error: null });
    try {
      const next = parseTaskResultsPage(await this.api.readTaskResults(this.taskId, this.revision, offset, limit, controller.signal), this.taskId, this.revision, offset, limit);
      if (generation !== this.generation || controller.signal.aborted) return;
      if (previous && (previous.total !== next.total || previous.title !== next.title || previous.summary !== next.summary || previous.description !== next.description || JSON.stringify(previous.acceptanceCriteria) !== JSON.stringify(next.acceptanceCriteria)
        || next.evidence.some((item) => previous.evidence.some((old) => old.ref.id === item.ref.id)))) return fail();
      this.set({ page: { ...next, offset: 0, evidence: [...(previous?.evidence ?? []), ...next.evidence] }, loading: false, error: null });
    } catch (error) {
      if (generation !== this.generation || controller.signal.aborted) return;
      this.set({ page: previous, loading: false, error: error instanceof ApiProblem && error.apiError?.code === "results_task_revision_changed" ? "changed" : "unavailable" });
    }
  }
}
