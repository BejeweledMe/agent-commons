import { ApiProblem } from "./api.js";
import { conversationId, parseScope, scopeKey, type ConversationScope } from "./conversationApi.js";

/**
 * A deliberately saved message draft: prose and an optional reply target, kept
 * by the server under the state root for this principal and project.
 *
 * It is not the attachment reservation draft and holds no attachments. Nothing
 * here autosaves and nothing here sends: the operator presses Save draft, and
 * the status then says plainly what is stored.
 */
export const TEXT_DRAFT_SCHEMA = "agent_commons.conversation-text-draft.v1";
export const TEXT_DRAFT_CONFLICT = "conversation_text_draft_conflict";
export const MAX_TEXT_DRAFT_BYTES = 64_000;

export type SavedTextDraft = Readonly<{
  revision: number;
  scope: ConversationScope;
  text: string;
  replyToMessageId: string | null;
  updatedAt: string | null;
}>;

const invalid = (): never => { throw new ApiProblem(502, null); };

export function textDraftBytes(text: string): number {
  return new TextEncoder().encode(text).length;
}

export function parseSavedTextDraft(value: unknown, expected: ConversationScope): SavedTextDraft {
  if (!value || typeof value !== "object" || Array.isArray(value)) return invalid();
  const row = value as Record<string, unknown>;
  if (row.schema !== TEXT_DRAFT_SCHEMA) return invalid();
  const scope = parseScope(row.scope);
  if (scopeKey(scope) !== scopeKey(expected)) return invalid();
  if (!Number.isSafeInteger(row.revision) || Number(row.revision) < 0) return invalid();
  if (typeof row.text !== "string" || textDraftBytes(row.text) > MAX_TEXT_DRAFT_BYTES) return invalid();
  const updatedAt = row.updated_at === null || row.updated_at === undefined ? null
    : typeof row.updated_at === "string" && /^\d{4}-\d{2}-\d{2}T/.test(row.updated_at) && Number.isFinite(Date.parse(row.updated_at))
      ? row.updated_at : invalid();
  return {
    revision: Number(row.revision),
    scope,
    text: row.text,
    replyToMessageId: row.reply_to_message_id === null || row.reply_to_message_id === undefined
      ? null : conversationId(row.reply_to_message_id, "message"),
    updatedAt
  };
}

export function textDraftPath(scope: ConversationScope): string {
  const parsed = parseScope(scope);
  const query = new URLSearchParams({ scope_kind: parsed.kind, ...(parsed.kind === "project" ? {} : { scope_id: parsed.id }) });
  return `/conversations/text-draft?${query}`;
}

export function textDraftBody(
  scope: ConversationScope,
  expectedRevision: number,
  text: string,
  replyToMessageId: string | null
): Readonly<Record<string, unknown>> {
  return {
    scope: parseScope(scope),
    expected_revision: expectedRevision,
    text,
    reply_to_message_id: replyToMessageId
  };
}

type Transport = { requestConversation(path: string, options: { method: "GET" | "POST"; body?: unknown; signal: AbortSignal }): Promise<unknown> };

export class ConversationTextDraftApi {
  constructor(private readonly api: Transport) {}

  async read(scope: ConversationScope, signal: AbortSignal): Promise<SavedTextDraft> {
    return parseSavedTextDraft(await this.api.requestConversation(textDraftPath(scope), { method: "GET", signal }), scope);
  }

  async write(
    scope: ConversationScope,
    expectedRevision: number,
    text: string,
    replyToMessageId: string | null,
    signal: AbortSignal
  ): Promise<SavedTextDraft> {
    if (textDraftBytes(text) > MAX_TEXT_DRAFT_BYTES) throw new ApiProblem(422, { code: "invalid_request", message: "", safeNextActions: [] });
    return parseSavedTextDraft(
      await this.api.requestConversation("/conversations/text-draft", { method: "POST", body: textDraftBody(scope, expectedRevision, text, replyToMessageId), signal }),
      scope
    );
  }

  /** Deleting a saved draft is the same write with empty text and no reply. */
  async remove(scope: ConversationScope, expectedRevision: number, signal: AbortSignal): Promise<SavedTextDraft> {
    return this.write(scope, expectedRevision, "", null, signal);
  }
}

/**
 * What the composer says about the saved draft.
 *
 * `unknown` is the state before any read; `unavailable` means the store could
 * not be read or written and nothing may be claimed about it; `stale_saved`
 * means a sent message could not clear its saved copy, which is stated rather
 * than silently resent.
 */
export type TextDraftStatus =
  | { kind: "unknown" }
  | { kind: "none" }
  | { kind: "saved"; draft: SavedTextDraft }
  | { kind: "saving" }
  | { kind: "conflict"; draft: SavedTextDraft | null }
  | { kind: "unavailable" }
  | { kind: "stale_saved"; draft: SavedTextDraft };

export function savedDraftOf(status: TextDraftStatus): SavedTextDraft | null {
  if (status.kind === "saved" || status.kind === "stale_saved") return status.draft;
  if (status.kind === "conflict") return status.draft;
  return null;
}

/** An empty saved text is no saved draft: the server records the deletion. */
export function hasSavedText(status: TextDraftStatus): boolean {
  const draft = savedDraftOf(status);
  return draft !== null && draft.text.length > 0;
}

/**
 * Whether the saved draft differs from what is being typed now. Only then is
 * Restore offered, and restoring is always the operator's explicit choice.
 */
export function savedDraftDiffers(status: TextDraftStatus, text: string, replyToMessageId: string | null): boolean {
  const draft = savedDraftOf(status);
  if (draft === null || draft.text.length === 0) return false;
  return draft.text !== text || draft.replyToMessageId !== replyToMessageId;
}

export function textDraftConflict(error: unknown): boolean {
  return error instanceof ApiProblem && error.status === 409 && error.apiError?.code === TEXT_DRAFT_CONFLICT;
}

/** One owner per project/scope composer, retained across close/reopen and polling. */
export class SavedTextDraftController {
  private known: SavedTextDraft | null = null;
  // Keep every confirmation while a read/save is unresolved. A second sent
  // message must not overwrite the first message's pending draft cleanup.
  private sentToClear = new Set<string>();
  private pending: { revision: number; text: string; reply: string | null } | null = null;
  private value: { status: TextDraftStatus; busy: boolean; uncertain: boolean } = { status: { kind: "unknown" }, busy: false, uncertain: false };
  private listeners = new Set<() => void>();
  constructor(private api: ConversationTextDraftApi, readonly scope: ConversationScope) {}
  setApi(api: ConversationTextDraftApi): void { this.api = api; }
  snapshot = () => this.value;
  subscribe = (fn: () => void): (() => void) => { this.listeners.add(fn); return () => this.listeners.delete(fn); };
  private publish(status: TextDraftStatus, busy = false): void {
    this.value = { status, busy, uncertain: this.pending !== null && !busy };
    for (const fn of this.listeners) fn();
  }
  async read(force = false): Promise<void> {
    if (this.value.busy || this.pending || (!force && this.value.status.kind !== "unknown")) return;
    this.publish(this.value.status, true);
    try { this.known = await this.api.read(this.scope, new AbortController().signal); this.publish({ kind: "saved", draft: this.known }); await this.clearConfirmedDraft(); }
    catch { this.publish({ kind: "unavailable" }); }
  }
  async write(text: string, reply: string | null): Promise<boolean> {
    if (this.value.busy) return false;
    if (this.known === null) { await this.read(true); if (this.known === null) return false; }
    // A lost response retries exactly this intent, even after the RAM text changes.
    const intent = this.pending ?? { revision: this.known.revision, text, reply };
    this.pending = intent; this.publish({ kind: "saving" }, true);
    try {
      const saved = await this.api.write(this.scope, intent.revision, intent.text, intent.reply, new AbortController().signal);
      // Revisions are monotonic integers, not a +1 counter.
      if (saved.revision <= intent.revision || saved.text !== intent.text || saved.replyToMessageId !== intent.reply) throw new ApiProblem(502, null);
      this.known = saved; this.pending = null; this.publish({ kind: "saved", draft: saved });
      await this.clearConfirmedDraft();
      return true;
    } catch (error) {
      if (error instanceof ApiProblem && error.status >= 400 && error.status < 500) this.pending = null;
      this.publish(textDraftConflict(error) ? { kind: "conflict", draft: this.known } : { kind: "unavailable" }); return false;
    }
  }
  async clearAfterSend(sent: { text: string; reply: string | null }): Promise<void> {
    this.sentToClear.add(JSON.stringify([sent.text, sent.reply]));
    await this.clearConfirmedDraft();
  }
  private async clearConfirmedDraft(): Promise<void> {
    if (this.value.busy || this.pending) return;
    const saved = this.known;
    const confirmed = saved?.text && this.sentToClear.has(JSON.stringify([saved.text, saved.replyToMessageId]));
    // The completed read/save resolves this batch of confirmations. Future
    // deliberately saved drafts are a new intent, even when their bytes match.
    this.sentToClear.clear();
    if (confirmed && !await this.write("", null)) this.publish({ kind: "stale_saved", draft: saved });
  }
}
