import { ApiProblem, type WorkApi } from "./api.js";
export type ConversationScope = Readonly<{ kind: "project" } | { kind: "task" | "agent"; id: string }>;
export type Attachment = Readonly<{ attachment_id: string; display_name: string; media_type: string; size_bytes: number; digest: string; category: "image" | "file" }>;
export type Conversation = Readonly<{ thread_id: string; revision: string; scope: ConversationScope; subject: string; state: "open" | "resolved"; audience: "all_project_agents" | "task" | "agent"; message_count: number }>;
export type DeliveryState = "recorded" | "queued" | "fetched" | "acknowledged" | "answered";
export type Delivery = Readonly<{ state: DeliveryState; read_confirmed: boolean; recipients: readonly { agent_id: string | null; state: "queued" | "fetched" | "acknowledged"; acknowledged_at: string | null; fetched_at: string | null }[] }>;
export type Message = Readonly<{ message_id: string; body: string; attachments: readonly Attachment[]; recorded_at: string; reply_to_message_id: string | null; author: { kind: "operator" | "agent" | "unknown"; agent_id: string | null; name: string | null }; delivery: Delivery }>;
export type Draft = Readonly<{ draft_id: string; state: "ready" | "binding" | "bound"; attachments: readonly Attachment[]; expires_at: number; message_id: string | null }>;
export type SendIntent = Readonly<{ body: string; expected_revision: string; idempotency_key: string; draft_id?: string; reply_to_message_id?: string }>;
export type SendResult = Readonly<{ message_id: string; revision: string; state: "recorded" }>;
const invalid = (): never => { throw new ApiProblem(502, null); };
const object = (value: unknown): Record<string, unknown> => { if (!value || typeof value !== "object" || Array.isArray(value)) return invalid(); return value as Record<string, unknown>; };
const text = (value: unknown, max: number, empty = false): string => { if (typeof value !== "string" || new TextEncoder().encode(value).length > max || (!empty && !value.trim())) return invalid(); return value; };
const integer = (value: unknown, min: number, max: number): number => { if (!Number.isSafeInteger(value) || Number(value) < min || Number(value) > max) return invalid(); return Number(value); };
const array = (value: unknown, max: number): unknown[] => { if (!Array.isArray(value) || value.length > max) return invalid(); return value; };
export function conversationId(value: unknown, kind: string): string { const result = text(value, 80); if (!new RegExp(`^${kind}\\.[0-9A-HJKMNP-TV-Z]{26}$`).test(result)) return invalid(); return result; }
function opaque(value: unknown, kind: "draft" | "attachment"): string { const result = text(value, 80); if (!new RegExp(`^${kind}\\.[0-9a-f]{32}$`).test(result)) return invalid(); return result; }
const timestamp = (value: unknown): string => { const result = text(value, 64); if (!/^\d{4}-\d{2}-\d{2}T/.test(result) || !Number.isFinite(Date.parse(result))) return invalid(); return result; };
export function scopeKey(scope: ConversationScope): string { return scope.kind === "project" ? "project" : `${scope.kind}:${scope.id}`; }
export function parseScope(value: unknown): ConversationScope { const scope = object(value); if (scope.kind === "project") return { kind: "project" }; if (scope.kind !== "task" && scope.kind !== "agent") return invalid(); return { kind: scope.kind, id: conversationId(scope.id, scope.kind) }; }
export function parseAttachment(value: unknown): Attachment {
  const item = object(value), name = text(item.display_name, 180), media = text(item.media_type, 127), digest = text(item.digest, 71);
  if (name !== name.trim() || name === "." || name === ".." || /[\\/:<>"|?*\p{C}]/u.test(name) || !/^[a-z0-9][a-z0-9.+-]*\/[a-z0-9][a-z0-9.+-]*$/.test(media) || !/^sha256:[0-9a-f]{64}$/.test(digest) || (item.category !== "image" && item.category !== "file") || (media.startsWith("image/") !== (item.category === "image")) || (item.category === "image" && !["image/png", "image/jpeg"].includes(media))) return invalid();
  return { attachment_id: opaque(item.attachment_id, "attachment"), display_name: name, media_type: media, digest, size_bytes: integer(item.size_bytes, 1, 15_000_000), category: item.category };
}
function attachments(value: unknown): Attachment[] {
  const result = array(value, 20).map(parseAttachment);
  if (new Set(result.map((item) => item.attachment_id)).size !== result.length || result.filter((item) => item.category === "image").length > 10 || result.filter((item) => item.category === "file").length > 10) return invalid();
  return result;
}
export function parseConversation(value: unknown, expectedScope?: ConversationScope): Conversation {
  const row = object(value), scope = parseScope(row.scope);
  if ((expectedScope && scopeKey(scope) !== scopeKey(expectedScope)) || (row.state !== "open" && row.state !== "resolved") || row.audience !== (scope.kind === "project" ? "all_project_agents" : scope.kind)) return invalid();
  return { thread_id: conversationId(row.thread_id, "thread"), revision: conversationId(row.revision, "evt"), scope, subject: text(row.subject, 2048), state: row.state, audience: row.audience as Conversation["audience"], message_count: integer(row.message_count, 0, 1_000_000) };
}
export function parseDelivery(value: unknown): Delivery {
  const row = object(value);
  if (!["recorded", "queued", "fetched", "acknowledged", "answered"].includes(String(row.state)) || typeof row.read_confirmed !== "boolean") return invalid();
  const recipients = array(row.recipients ?? [], 128).map((value) => { const recipient = object(value); if (recipient.state !== "queued" && recipient.state !== "fetched" && recipient.state !== "acknowledged") return invalid(); const fetched = recipient.fetched_at === null ? null : timestamp(recipient.fetched_at), acknowledged = recipient.acknowledged_at === null ? null : timestamp(recipient.acknowledged_at); if ((recipient.state === "queued" && (fetched || acknowledged)) || (recipient.state === "fetched" && (!fetched || acknowledged)) || (recipient.state === "acknowledged" && (!fetched || !acknowledged))) return invalid(); return { agent_id: recipient.agent_id === null ? null : conversationId(recipient.agent_id, "agent"), state: recipient.state as "queued" | "fetched" | "acknowledged", fetched_at: fetched, acknowledged_at: acknowledged }; });
  if ((row.read_confirmed && row.state !== "acknowledged" && row.state !== "answered") || (row.state === "acknowledged" && !row.read_confirmed)) return invalid();
  return { state: row.state as DeliveryState, read_confirmed: row.read_confirmed, recipients };
}
export function parseMessage(value: unknown): Message {
  const row = object(value), author = object(row.author), items = attachments(row.attachments), body = text(row.body, 64_000, true);
  if ((!body.trim() && !items.length) || !["operator", "agent", "unknown"].includes(String(author.kind))) return invalid();
  const agentId = author.agent_id == null ? null : conversationId(author.agent_id, "agent");
  if ((author.kind === "agent") !== (agentId !== null)) return invalid();
  return { message_id: conversationId(row.message_id, "message"), body, attachments: items, recorded_at: timestamp(row.recorded_at), reply_to_message_id: row.reply_to_message_id == null ? null : conversationId(row.reply_to_message_id, "message"), author: { kind: author.kind as Message["author"]["kind"], agent_id: agentId, name: author.name === null ? null : text(author.name, 1024) }, delivery: parseDelivery(row.delivery) };
}
export function parseDraft(value: unknown, expectedId?: string): Draft {
  const row = object(value), id = opaque(row.draft_id, "draft");
  if ((expectedId && id !== expectedId) || !["ready", "binding", "bound"].includes(String(row.state)) || typeof row.expires_at !== "number" || !Number.isFinite(row.expires_at)) return invalid();
  const messageId = row.message_id === null ? null : conversationId(row.message_id, "message");
  if ((row.state === "bound") !== (messageId !== null)) return invalid();
  return { draft_id: id, state: row.state as Draft["state"], attachments: attachments(row.attachments), expires_at: row.expires_at, message_id: messageId };
}
export class ConversationApi {
  constructor(private readonly api: WorkApi) {}
  private request(path: string, signal: AbortSignal, body?: unknown): Promise<unknown> { return this.api.requestConversation(path, { signal, method: body === undefined ? "GET" : "POST", body }); }
  async list(scope: ConversationScope, signal: AbortSignal): Promise<Conversation[]> { const parsed = parseScope(scope), query = new URLSearchParams({ scope_kind: parsed.kind, ...(parsed.kind === "project" ? {} : { scope_id: parsed.id }) }); const row = object(await this.request(`/conversations?${query}`, signal)); if (row.schema !== "agent_commons.conversations.v1" || scopeKey(parseScope(row.scope)) !== scopeKey(parsed)) return invalid(); const result = array(row.conversations, 32).map((item) => parseConversation(item, parsed)); if (new Set(result.map((item) => item.thread_id)).size !== result.length) return invalid(); return result; }
  async ensure(scope: ConversationScope, key: string, signal: AbortSignal): Promise<Conversation> { return parseConversation(await this.request("/conversations", signal, { scope: parseScope(scope), idempotency_key: key }), scope); }
  async messages(thread: string, signal: AbortSignal, before?: string): Promise<{ conversation: Conversation; messages: Message[]; previousCursor: string | null }> {
    conversationId(thread, "thread"); if (before) conversationId(before, "message");
    const row = object(await this.request(`/conversations/${thread}/messages?${before ? `before=${before}` : "tail=true"}`, signal));
    if (row.schema !== "agent_commons.conversation-messages.v1") return invalid();
    const conversation = parseConversation(row.conversation), messages = array(row.messages, 50).map(parseMessage);
    if (conversation.thread_id !== thread || new Set(messages.map((item) => item.message_id)).size !== messages.length || messages.some((item) => item.message_id === before)) return invalid();
    const previousCursor = row.previous_cursor === null ? null : conversationId(row.previous_cursor, "message");
    const nextCursor = row.next_cursor === null ? null : conversationId(row.next_cursor, "message");
    if ((previousCursor && messages[0]?.message_id !== previousCursor) || (nextCursor && messages.at(-1)?.message_id !== nextCursor) || (!before && nextCursor) || messages.length > conversation.message_count) return invalid();
    return { conversation, messages, previousCursor };
  }
  async draft(thread: string, signal: AbortSignal): Promise<Draft> { conversationId(thread, "thread"); return parseDraft(await this.request(`/conversations/${thread}/drafts`, signal, {})); }
  async showDraft(thread: string, draft: string, signal: AbortSignal): Promise<Draft> { conversationId(thread, "thread"); opaque(draft, "draft"); return parseDraft(await this.request(`/conversations/${thread}/drafts/${draft}`, signal), draft); }
  async upload(thread: string, draft: string, file: File, key: string, signal: AbortSignal): Promise<Attachment> { conversationId(thread, "thread"); opaque(draft, "draft"); return parseAttachment(await this.api.requestConversation(`/conversations/${thread}/drafts/${draft}/attachments?filename=${encodeURIComponent(file.name)}`, { method: "POST", body: file, operationId: key, signal })); }
  async remove(thread: string, draft: string, item: string, signal: AbortSignal): Promise<Draft> { conversationId(thread, "thread"); opaque(draft, "draft"); opaque(item, "attachment"); return parseDraft(await this.request(`/conversations/${thread}/drafts/${draft}/attachments/${item}/remove`, signal, {}), draft); }
  async send(thread: string, intent: SendIntent, signal: AbortSignal): Promise<SendResult> { conversationId(thread, "thread"); const row = object(await this.request(`/conversations/${thread}/messages`, signal, intent)); if (row.state !== "recorded") return invalid(); return { state: "recorded", message_id: conversationId(row.message_id, "message"), revision: conversationId(row.revision, "evt") }; }
  async download(thread: string, message: string, item: Attachment, signal: AbortSignal): Promise<Blob> { const blob = await this.api.conversationAttachment(thread, message, item.attachment_id, signal); if (blob.size !== item.size_bytes || (item.category === "image" && blob.type !== item.media_type)) return invalid(); const digest = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", await blob.arrayBuffer())), (byte) => byte.toString(16).padStart(2, "0")).join(""); if (`sha256:${digest}` !== item.digest) return invalid(); return blob; }
}
export type FileProblem = "size" | "format" | "count" | "filename";
export function fileCategory(file: File): "image" | "file" { return file.type.startsWith("image/") || /\.(png|jpe?g)$/i.test(file.name) ? "image" : "file"; }
export function validateFiles(existing: readonly { category: "image" | "file" }[], files: readonly File[]): FileProblem | null {
  let images = existing.filter((item) => item.category === "image").length, other = existing.length - images;
  for (const file of files) {
    if (!Number.isSafeInteger(file.size) || file.size < 1 || file.size > 15_000_000) return "size";
    if (!file.name || file.name !== file.name.trim() || file.name === "." || file.name === ".." || new TextEncoder().encode(file.name).length > 180 || /[\\/:<>"|?*\p{C}]/u.test(file.name)) return "filename";
    if (fileCategory(file) === "image") { if (file.type && !["image/png", "image/jpeg", "application/octet-stream"].includes(file.type)) return "format"; images++; } else other++;
  }
  return images > 10 || other > 10 ? "count" : null;
}
