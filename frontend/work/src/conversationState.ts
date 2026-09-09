import { ApiProblem } from "./api.js";
import { fileCategory, scopeKey, validateFiles, UNKNOWN_AVAILABILITY, type Attachment, type Conversation, type ConversationApi, type ConversationScope, type Draft, type FileProblem, type Message, type SendIntent } from "./conversationApi.js";

type Transport = Pick<ConversationApi, "list" | "ensure" | "messages" | "draft" | "upload" | "remove" | "send">;
export type Upload = Readonly<{ id: string; key: string; file: File; category: "image" | "file"; attachment?: Attachment; state: "queued" | "uploading" | "ready" | "uncertain" | "rejected" | "removing" | "remove_uncertain" }>;
export type ConversationSnapshot = Readonly<{ conversation: Conversation | null; archives: readonly Conversation[]; previousCursor: string | null; olderLoading: boolean; olderError: boolean; historyLimited: boolean; messages: readonly Message[]; text: string; reply: string | null; uploads: readonly Upload[]; loading: boolean; readError: boolean; fileProblem: FileProblem | null; sendState: "idle" | "sending" | "uncertain" | "recorded" | "refused"; intent: SendIntent | null; refusal: "revision" | "validation" | "expired" | null }>;
const signal = (): AbortSignal => new AbortController().signal;
const key = (): string => crypto.randomUUID();
/** User content lives only in this workspace's RAM. Writes outlive a closed dialog. */
export class ConversationSession {
  private value: ConversationSnapshot = { conversation: null, archives: [], previousCursor: null, olderLoading: false, olderError: false, historyLimited: false, messages: [], text: "", reply: null, uploads: [], loading: false, readError: false, fileProblem: null, sendState: "idle", intent: null, refusal: null };
  private listeners = new Set<() => void>();
  private ensureKey = key();
  private closedForKey: string | null = null;
  private connecting: Promise<void> | null = null;
  private draft: Draft | null = null;
  private reserving: Promise<Draft> | null = null;
  private uploading = false;
  private generation = 0;
  private read: Promise<void> | null = null;
  constructor(readonly api: Transport, readonly scope: ConversationScope, private readonly pinned?: Conversation) {
    if (pinned) this.value = { ...this.value, conversation: pinned };
  }
  snapshot = (): ConversationSnapshot => this.value;
  subscribe = (listener: () => void): (() => void) => { this.listeners.add(listener); return () => this.listeners.delete(listener); };
  private set(next: Partial<ConversationSnapshot>): void { this.value = { ...this.value, ...next }; for (const listener of this.listeners) listener(); }
  private file(id: string, changes: Partial<Upload>): void { this.set({ uploads: this.value.uploads.map((item) => item.id === id ? { ...item, ...changes } : item) }); }
  get locked(): boolean { return this.value.sendState === "sending" || this.value.sendState === "uncertain"; }
  get canEditText(): boolean { return !this.locked && (!this.value.conversation || this.value.conversation.state === "open"); }
  setText(text: string): void { if (this.canEditText) this.set({ text, sendState: "idle", refusal: null }); }
  setReply(reply: string | null): void { if (!this.locked) this.set({ reply, sendState: "idle", refusal: null }); }
  connect(writable: boolean): Promise<void> {
    if (this.value.conversation && (this.pinned || this.value.conversation.state === "open" || !writable || this.locked || this.uploading || this.value.uploads.some((item) => item.state === "removing"))) return this.refresh();
    if (this.connecting) return this.connecting;
    this.set({ loading: true, readError: false });
    this.connecting = (async () => {
      try {
        const previous = this.value.conversation;
        if (previous?.state === "resolved" && this.closedForKey !== previous.thread_id) { this.ensureKey = key(); this.closedForKey = previous.thread_id; }
        const existing = await this.api.list(this.scope, signal());
        const archives = [...existing.filter((item) => item.state === "resolved")];
        if (previous?.state === "resolved" && !archives.some((item) => item.thread_id === previous.thread_id)) archives.push(previous);
        this.set({ archives });
        const conversation = existing.find((item) => item.state === "open") ?? (writable ? await this.api.ensure(this.scope, this.ensureKey, signal()) : existing[0] ?? null);
        if (conversation && scopeKey(conversation.scope) !== scopeKey(this.scope)) throw new Error("scope");
        if (previous && conversation?.thread_id !== previous.thread_id) {
          this.generation++; this.draft = null;
          this.set({ messages: [], previousCursor: null, historyLimited: false, reply: null, uploads: this.value.uploads.map((item) => ({ id: item.id, key: key(), file: item.file, category: item.category, state: "queued" })) });
        }
        this.set({ conversation }); if (conversation) await this.refresh();
        if (conversation?.state === "open") void this.pump();
      } catch { this.set({ readError: true }); }
      finally { this.connecting = null; this.set({ loading: false }); }
    })();
    return this.connecting;
  }
  refresh(): Promise<void> {
    if (!this.value.conversation) return Promise.resolve();
    if (this.read) return this.read;
    const thread = this.value.conversation.thread_id, generation = this.generation;
    this.read = (async () => {
      try {
        const next = await this.api.messages(thread, signal());
        if (next.conversation.thread_id !== thread || scopeKey(next.conversation.scope) !== scopeKey(this.scope)) throw new Error("scope");
        if (generation === this.generation) {
          const anchor = next.messages.length ? this.value.messages.findIndex((item) => item.message_id === next.messages[0].message_id) : -1;
          const combined = anchor >= 0 ? [...this.value.messages.slice(0, anchor), ...next.messages] : next.messages;
          const messages = combined.slice(-2000), clipped = combined.length > messages.length;
          const previousCursor = clipped ? messages[0].message_id : anchor >= 0 ? this.value.previousCursor : next.previousCursor ?? null;
          const archives = next.conversation.state === "resolved" ? [...this.value.archives.filter((item) => item.thread_id !== thread), next.conversation] : this.value.archives;
          this.set({ conversation: next.conversation, messages, previousCursor, historyLimited: Boolean(previousCursor && messages.length >= 2000), archives, readError: false });
        }
      } catch { if (generation === this.generation) this.set({ readError: true }); }
      finally { this.read = null; }
    })();
    return this.read;
  }
  async loadOlder(): Promise<void> {
    const { conversation, previousCursor, olderLoading, historyLimited } = this.value;
    if (!conversation || !previousCursor || olderLoading || historyLimited) return;
    const generation = this.generation, first = this.value.messages[0]?.message_id;
    this.set({ olderLoading: true, olderError: false });
    try {
      const page = await this.api.messages(conversation.thread_id, signal(), previousCursor);
      if (page.conversation.thread_id !== conversation.thread_id || scopeKey(page.conversation.scope) !== scopeKey(this.scope)) throw new Error("scope");
      if (generation !== this.generation || this.value.messages[0]?.message_id !== first) return;
      const ids = new Set(this.value.messages.map((item) => item.message_id));
      if (page.messages.some((item) => ids.has(item.message_id)) || (page.previousCursor && !page.messages.length)) throw new Error("history");
      const combined = [...page.messages, ...this.value.messages], messages = combined.slice(-2000);
      const cursor = combined.length > 2000 ? messages[0].message_id : page.previousCursor ?? null;
      this.set({ messages, previousCursor: cursor, historyLimited: Boolean(cursor && messages.length >= 2000) });
    } catch { if (generation === this.generation) this.set({ olderError: true }); }
    finally { this.set({ olderLoading: false }); }
  }
  addFiles(files: readonly File[]): void {
    if (this.locked || !this.value.conversation || this.value.conversation.state !== "open") return;
    let problem: FileProblem | null = null;
    const uploads = [...this.value.uploads];
    for (const file of files) {
      const invalid = validateFiles(uploads, [file]);
      if (invalid) { problem = invalid; continue; }
      uploads.push({ id: key(), key: key(), file, category: fileCategory(file), state: "queued" });
    }
    this.set({ uploads, fileProblem: problem }); void this.pump();
  }
  private async reserve(): Promise<Draft> {
    if (this.draft) return this.draft;
    if (!this.reserving) this.reserving = this.api.draft(this.value.conversation!.thread_id, signal()).then((draft) => {
      if (draft.state !== "ready" || draft.attachments.length) throw new Error("draft"); this.draft = draft; return draft;
    }).finally(() => { this.reserving = null; });
    return this.reserving;
  }
  private async pump(): Promise<void> {
    if (this.uploading || this.locked) return;
    this.uploading = true;
    try {
      while (true) {
        const item = this.value.uploads.find((entry) => entry.state === "queued"); if (!item) break;
        this.file(item.id, { state: "uploading" });
        try {
          const draft = await this.reserve();
          const attachment = await this.api.upload(this.value.conversation!.thread_id, draft.draft_id, item.file, item.key, signal());
          if (attachment.display_name !== item.file.name || attachment.size_bytes !== item.file.size) throw new Error("attachment");
          this.file(item.id, { attachment, category: attachment.category, state: "ready" });
        } catch (error) { this.file(item.id, { state: error instanceof ApiProblem && error.status === 422 && error.apiError?.code === "attachment_invalid" ? "rejected" : "uncertain" }); }
      }
    } finally { this.uploading = false; }
  }
  retryUpload(id: string): void { const item = this.value.uploads.find((entry) => entry.id === id); if (this.locked || item?.state !== "uncertain") return; this.file(id, { state: "queued" }); void this.pump(); }
  async remove(id: string): Promise<void> {
    if (this.locked) return;
    const item = this.value.uploads.find((entry) => entry.id === id); if (!item || !["queued", "ready", "rejected", "remove_uncertain"].includes(item.state)) return;
    if (!item.attachment) { this.set({ uploads: this.value.uploads.filter((entry) => entry.id !== id) }); return; }
    this.file(id, { state: "removing" });
    try {
      const draft = await this.api.remove(this.value.conversation!.thread_id, this.draft!.draft_id, item.attachment.attachment_id, signal());
      if (draft.state !== "ready" || draft.attachments.some((entry) => entry.attachment_id === item.attachment!.attachment_id)) throw new Error("removal");
      this.set({ uploads: this.value.uploads.filter((entry) => entry.id !== id) });
    } catch { this.file(id, { state: "remove_uncertain" }); }
  }
  get canSend(): boolean { return !this.locked && this.value.conversation?.state === "open" && !this.value.readError && this.value.uploads.every((item) => item.state === "ready") && (this.value.text.trim().length > 0 || this.value.uploads.length > 0) && new TextEncoder().encode(this.value.text).length <= 64_000; }
  async send(): Promise<void> {
    if (this.value.sendState === "sending" || (!this.value.intent && !this.canSend)) return;
    const conversation = this.value.conversation!;
    const intent = this.value.intent ?? Object.freeze({ body: this.value.text, expected_revision: conversation.revision, idempotency_key: key(), ...(this.value.uploads.length ? { draft_id: this.draft!.draft_id } : {}), ...(this.value.reply ? { reply_to_message_id: this.value.reply } : {}) });
    this.set({ intent, sendState: "sending", refusal: null });
    try {
      const result = await this.api.send(conversation.thread_id, intent, signal());
      // The POST is authoritative. Clear its retry identity before any fallible read.
      this.generation++; this.draft = null;
      // The locally advanced revision outdates the observed availability; the following read restores it.
      this.set({ text: "", reply: null, uploads: [], intent: null, sendState: "recorded", conversation: { ...this.value.conversation!, revision: result.revision, recipient_availability: UNKNOWN_AVAILABILITY }, readError: false, fileProblem: null });
      const pending = this.read; if (pending) await pending;
      await this.refresh();
    } catch (error) {
      const revision = error instanceof ApiProblem && error.status === 409 && error.apiError?.code === "conversation_revision_conflict";
      const validation = error instanceof ApiProblem && error.status === 422 && error.apiError?.code === "conversation_invalid_message";
      const expired = error instanceof ApiProblem && error.status === 409 && error.apiError?.code === "conversation_draft_expired";
      if (expired) {
        // This exact response proves no binding, reservation, or message exists.
        // Preserve original Files and prose, but use a fresh draft/upload identity.
        this.generation++; this.draft = null;
        this.set({ intent: null, sendState: "refused", refusal: "expired", readError: true, uploads: this.value.uploads.map((item) => ({ id: item.id, key: key(), file: item.file, category: item.category, state: "queued" })) });
        const pending = this.read; if (pending) await pending; await this.refresh(); void this.pump();
      }
      else if (revision || validation) { this.generation++; this.set({ intent: null, sendState: "refused", refusal: revision ? "revision" : "validation", readError: true }); const pending = this.read; if (pending) await pending; await this.refresh(); }
      else this.set({ sendState: "uncertain" });
    }
  }
}
/**
 * The task a conversation's Prepare run action points at: its own task for a
 * task scope, otherwise the task already selected in Work. `null` means there
 * is nothing to prefill and the caller only navigates. No scope starts a run.
 */
export function prepareRunTaskId(scope: ConversationScope, selectedTaskId: string | null): string | null {
  return scope.kind === "task" ? scope.id : selectedTaskId;
}
/** Unsent work an operator would lose: prose, a chosen reply target, or attachments. */
export function sessionHasUnsentDraft(snapshot: ConversationSnapshot): boolean {
  return snapshot.text.trim().length > 0 || snapshot.reply !== null || snapshot.uploads.length > 0;
}
/** Owned by the mounted Work application, independent of project/dialog selection. */
export class ConversationSessions {
  private sessions = new Map<string, ConversationSession>();
  private listeners = new Set<() => void>();
  get(project: string, scope: ConversationScope, api: Transport): ConversationSession { const id = `${project}/${scopeKey(scope)}`; let session = this.sessions.get(id); if (!session) { session = new ConversationSession(api, scope); session.subscribe(() => { for (const listener of this.listeners) listener(); }); this.sessions.set(id, session); } return session; }
  subscribe = (listener: () => void): (() => void) => { this.listeners.add(listener); return () => this.listeners.delete(listener); };
  /** Every open session, not only the one a dialog is showing. */
  hasUnsentDraft = (): boolean => [...this.sessions.values()].some((session) => sessionHasUnsentDraft(session.snapshot()));
}
type UnloadTarget = Pick<Window, "addEventListener" | "removeEventListener">;
/**
 * One `beforeunload` handler for the whole workspace, registered only while an
 * unsent draft exists and removed the moment the last one is gone. The draft
 * itself is never persisted: a reload deliberately loses it.
 */
export class ConversationDraftGuard {
  private handler: ((event: BeforeUnloadEvent) => void) | null = null;
  constructor(private readonly target: UnloadTarget, private readonly drafts: Pick<ConversationSessions, "hasUnsentDraft">) {}
  get armed(): boolean { return this.handler !== null; }
  sync = (): void => {
    if (!this.drafts.hasUnsentDraft()) return this.release();
    if (this.handler) return;
    this.handler = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    this.target.addEventListener("beforeunload", this.handler);
  };
  release = (): void => { if (this.handler) { this.target.removeEventListener("beforeunload", this.handler); this.handler = null; } };
}
/** A single owner prevents leaked or reused URLs when a preview closes or changes. */
export class ConversationObjectUrl {
  private url: string | null = null;
  constructor(private readonly urls: Pick<typeof URL, "createObjectURL" | "revokeObjectURL"> = URL) {}
  create(blob: Blob): string { this.release(); this.url = this.urls.createObjectURL(blob); return this.url; }
  release(): void { if (this.url) this.urls.revokeObjectURL(this.url); this.url = null; }
}
