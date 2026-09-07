import { ApiProblem } from "./api.js";

export type TaskEditDetail = {
  taskId: string; revision: string; title: string; description: string;
  criteria: readonly string[]; dependencyIds: readonly string[]; state: string;
  editable: boolean; cancellable: boolean; refusalCode: string | null;
  suggestedAgentId?: string;
};
export type TaskEditInput = Pick<TaskEditDetail, "title" | "description" | "criteria" | "dependencyIds">;
export type TaskEditResult = { taskId: string; revision: string; action: "revised" | "cancelled" };
export type TaskGraphTransport = {
  requestData(path: string, options: { method?: "GET" | "POST"; body?: unknown; signal: AbortSignal }): Promise<unknown>;
};
const TASK = /^task\.[0-9A-HJKMNP-TV-Z]{26}$/;
const REVISION = /^evt\.[0-9A-HJKMNP-TV-Z]{26}$/;
const STATES = new Set(["ready", "assigned", "active", "blocked", "completed", "review", "accepted", "cancelled"]);
function malformed(): never { throw new ApiProblem(502, null); }
function object(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return malformed();
  return value as Record<string, unknown>;
}
function boundedText(value: unknown, max: number): string {
  return typeof value === "string" && value.trim().length > 0 && Array.from(value).length <= max ? value : malformed();
}
function strings(value: unknown, max: number, validate: (value: unknown) => string): readonly string[] {
  return Array.isArray(value) && value.length <= max ? value.map(validate) : malformed();
}
function taskId(value: unknown): string { return typeof value === "string" && TASK.test(value) ? value : malformed(); }
function revision(value: unknown): string { return typeof value === "string" && REVISION.test(value) ? value : malformed(); }

export function parseTaskEditDetail(raw: unknown, expectedTaskId: string): TaskEditDetail {
  const value = object(raw);
  if (value.schema !== "agent-commons.ui.task-edit.v1" || value.task_id !== expectedTaskId
    || typeof value.state !== "string" || !STATES.has(value.state)
    || typeof value.editable !== "boolean" || typeof value.cancellable !== "boolean"
    || ![null, "task_live_work", "task_edit_state"].includes(value.refusal_code as string | null)) return malformed();
  const criteria = strings(value.acceptance_criteria, 128, (item) => boundedText(item, 2048));
  const dependencyIds = strings(value.dependencies, 128, taskId);
  if (criteria.length === 0 || new Set(dependencyIds).size !== dependencyIds.length) return malformed();
  if ((value.state === "accepted" || value.state === "cancelled" || value.refusal_code !== null) && (value.editable || value.cancellable)) return malformed();
  if ((value.state === "completed" || value.state === "review") && value.cancellable) return malformed();
  const result: TaskEditDetail = {
    taskId: taskId(value.task_id), revision: revision(value.revision), title: boundedText(value.title, 512),
    description: boundedText(value.description, 16000), criteria, dependencyIds, state: value.state,
    editable: value.editable, cancellable: value.cancellable, refusalCode: value.refusal_code as string | null
  };
  if (value.suggested_agent_id !== undefined) {
    if (typeof value.suggested_agent_id !== "string" || !/^agent\.[0-9A-HJKMNP-TV-Z]{26}$/.test(value.suggested_agent_id)) return malformed();
    result.suggestedAgentId = value.suggested_agent_id;
  }
  if (new TextEncoder().encode(JSON.stringify(result)).byteLength > 65536) return malformed();
  return result;
}

export function parseTaskEditResult(raw: unknown, expectedTaskId: string, action: "revised" | "cancelled"): TaskEditResult {
  const value = object(raw);
  if (value.schema !== "agent-commons.ui.task-edit-result.v1" || value.task_id !== expectedTaskId || value.action !== action) return malformed();
  return { taskId: taskId(value.task_id), revision: revision(value.revision), action };
}

export class TaskGraphApi {
  private writes = new Map<string, { signature: string; body: unknown }>();
  constructor(private transport: TaskGraphTransport) {}
  async detail(id: string, signal: AbortSignal): Promise<TaskEditDetail> {
    taskId(id);
    return parseTaskEditDetail(await this.transport.requestData(`/work/tasks/${encodeURIComponent(id)}/edit-detail`, { signal }), id);
  }
  forget(key: string): void { this.writes.delete(key); }
  private async write(id: string, action: "edit" | "cancel", input: unknown, key: string, signal: AbortSignal): Promise<TaskEditResult> {
    taskId(id);
    const signature = JSON.stringify([id, action, input]);
    const prior = this.writes.get(key);
    if (prior && prior.signature !== signature) throw new ApiProblem(409, { code: "task_retry_intent_changed", message: "", safeNextActions: [] });
    const saved = prior ?? { signature, body: JSON.parse(JSON.stringify({ ...object(input), idempotency_key: key })) };
    this.writes.set(key, saved);
    return parseTaskEditResult(await this.transport.requestData(`/work/tasks/${encodeURIComponent(id)}/${action}`, {
      method: "POST", body: saved.body, signal
    }), id, action === "edit" ? "revised" : "cancelled");
  }
  save(id: string, expectedRevision: string, input: TaskEditInput, key: string, signal: AbortSignal): Promise<TaskEditResult> {
    revision(expectedRevision);
    return this.write(id, "edit", { expected_revision: expectedRevision, changes: {
      title: input.title, description: input.description, acceptance_criteria: [...input.criteria], dependencies: [...input.dependencyIds]
    } }, key, signal);
  }
  cancel(id: string, expectedRevision: string, reason: string, key: string, signal: AbortSignal): Promise<TaskEditResult> {
    revision(expectedRevision);
    return this.write(id, "cancel", { expected_revision: expectedRevision, reason }, key, signal);
  }
}
