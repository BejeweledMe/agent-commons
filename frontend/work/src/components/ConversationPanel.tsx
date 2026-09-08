import { createContext, useContext, useEffect, useId, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import type { WorkApi } from "../api.js";
import type { Locale } from "../i18n.js";
import { ConversationApi, scopeKey, type Attachment, type ConversationScope } from "../conversationApi.js";
import { ConversationObjectUrl, ConversationSessions, ConversationSession } from "../conversationState.js";
import { conversationText } from "../conversationStrings.js";

type Selection = { scope: ConversationScope; title: string; opener: HTMLButtonElement };
type Workspace = { locale: Locale; open: (selection: Selection) => void };
const Context = createContext<Workspace | null>(null);
export function ConversationButton({ scope, title }: { scope: ConversationScope; title: string }) {
  const workspace = useContext(Context); if (!workspace) return null;
  const text = conversationText(workspace.locale);
  return <button type="button" className="button button-secondary button-inline conversation-entry" aria-haspopup="dialog" aria-label={`${text.for} ${title}`} onClick={(event) => workspace.open({ scope, title, opener: event.currentTarget })}>{text.conversation}</button>;
}
export function ConversationWorkspace({ api, projectId, locale, writesEnabled, sessions, children }: { api: WorkApi; projectId: string | null; locale: Locale; writesEnabled: boolean; sessions: ConversationSessions; children: ReactNode }) {
  const [selection, setSelection] = useState<Selection | null>(null);
  const transport = useMemo(() => new ConversationApi(api), [api]);
  const close = () => { const opener = selection?.opener; setSelection(null); if (opener?.isConnected) opener.focus(); };
  return <Context.Provider value={{ locale, open: setSelection }}>{children}{selection ? <ConversationDialog key={`${projectId}/${scopeKey(selection.scope)}`} api={transport} session={sessions.get(projectId ?? "legacy", selection.scope, transport)} scope={selection.scope} title={selection.title} locale={locale} writable={writesEnabled} onClose={close} /> : null}</Context.Provider>;
}
function ConversationDialog({ api, session, scope, title, locale, writable, onClose }: { api: ConversationApi; session: ConversationSession; scope: ConversationScope; title: string; locale: Locale; writable: boolean; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null), heading = useId(), inputId = useId(), hintId = useId();
  const snapshot = useSyncExternalStore(session.subscribe, session.snapshot, session.snapshot);
  const [visible, setVisible] = useState(100), [archiveId, setArchiveId] = useState<string | null>(null);
  const archive = useMemo(() => {
    const record = snapshot.archives.find((item) => item.thread_id === archiveId);
    return record ? new ConversationSession(api, scope, record) : null;
  }, [archiveId, api, session]);
  const historySession = archive ?? session;
  const history = useSyncExternalStore(historySession.subscribe, historySession.snapshot, historySession.snapshot);
  useEffect(() => { if (archive) void archive.connect(false); setVisible(100); }, [archive]);
  const text = conversationText(locale), locked = session.locked, canWrite = !archiveId && writable && (snapshot.conversation?.state === "open" || locked);
  useEffect(() => { const dialog = ref.current; dialog?.showModal(); return () => { if (dialog?.open) dialog.close(); }; }, []);
  useEffect(() => { let disposed = false, timer: ReturnType<typeof setTimeout>; const poll = async () => { await session.connect(writable); if (!disposed) timer = setTimeout(poll, 4000); }; void poll(); return () => { disposed = true; clearTimeout(timer); }; }, [session, writable]);
  const thread = history.conversation?.thread_id;
  return <dialog className="conversation-dialog" ref={ref} aria-labelledby={heading} onCancel={(event) => { event.preventDefault(); onClose(); }}>
    <header className="conversation-header"><div><h2 id={heading}>{text.for} {title}</h2><p>{text[scope.kind]}</p></div><button type="button" className="button button-secondary" onClick={onClose}>{text.close}</button></header>
    {snapshot.archives.some((item) => item.thread_id !== snapshot.conversation?.thread_id) ? <label className="conversation-history-picker">{text.history}<select value={archiveId ?? ""} onChange={(event) => setArchiveId(event.target.value || null)}><option value="">{text.current}</option>{snapshot.archives.filter((item) => item.thread_id !== snapshot.conversation?.thread_id).map((item, index) => <option key={item.thread_id} value={item.thread_id}>{text.closed} {index + 1} · {item.message_count}</option>)}</select></label> : null}
    <div className="conversation-history">
      {history.loading ? <p role="status">{text.loading}</p> : null}
      {history.readError ? <p className="conversation-notice" role="alert">{text.unavailable} <button className="button button-secondary" type="button" onClick={() => void historySession.connect(false)}>{text.refresh}</button></p> : null}
      {!history.loading && !history.readError && history.messages.length === 0 ? <p className="conversation-empty">{text.empty}</p> : null}
      {history.historyLimited ? <p className="conversation-notice" role="status">{text.historyLimited}</p> : history.previousCursor ? <p className="conversation-hint">{text.historyPartial}</p> : null}
      {history.olderError ? <p role="alert">{text.olderError}</p> : null}
      {history.messages.length > visible || (history.previousCursor && !history.historyLimited) ? <button type="button" className="button button-secondary" disabled={history.olderLoading} onClick={() => { setVisible((count) => count + 100); if (history.messages.length <= visible) void historySession.loadOlder(); }}>{history.olderLoading ? text.loading : text.earlier}</button> : null}
      <ol className="conversation-messages" aria-label={text.conversation}>
        {history.messages.slice(-visible).map((message) => <li className={`conversation-message conversation-message-${message.author.kind}`} key={message.message_id}>
          <header><strong>{message.author.name ?? (message.author.kind === "operator" ? text.operator : message.author.kind === "agent" ? text.agentAuthor : text.unknown)}</strong><time dateTime={message.recorded_at}>{new Date(message.recorded_at).toLocaleString(locale)}</time></header>
          {message.reply_to_message_id ? <small>{text.replying}</small> : null}
          {message.body ? <p className="conversation-body">{message.body}</p> : null}
          {thread && message.attachments.length ? <ul className="conversation-attachments">{message.attachments.map((item) => <li key={item.attachment_id}><BoundAttachment api={api} thread={thread} message={message.message_id} item={item} locale={locale} /></li>)}</ul> : null}
          <footer><span className={`conversation-delivery conversation-delivery-${message.delivery.state}`}>{text[message.delivery.state]}</span>{message.delivery.recipients.length ? <span>{message.delivery.read_confirmed ? text.readConfirmed : text.noRead}</span> : null}{canWrite ? <button type="button" className="button button-secondary button-inline" disabled={locked} onClick={() => session.setReply(message.message_id)}>{text.reply}</button> : null}</footer>
        </li>)}
      </ol>
    </div>
    {!archiveId ? <form className="conversation-composer" onSubmit={(event) => { event.preventDefault(); if (canWrite) void session.send(); }}>
      {!writable ? <p>{text.readonly}</p> : null}
      {snapshot.sendState === "recorded" ? <p role="status">{text.recordedNotice}</p> : null}
      {snapshot.sendState === "refused" ? <p role="alert">{text[snapshot.refusal === "expired" ? "expired" : snapshot.refusal === "revision" ? "revision" : "validation"]}</p> : null}
      {snapshot.sendState === "uncertain" ? <p className="conversation-notice" role="alert">{text.uncertain}</p> : null}
      {snapshot.reply ? <div className="conversation-reply">{text.replying} <button className="button button-secondary button-inline" disabled={locked} type="button" onClick={() => session.setReply(null)}>{text.cancelReply}</button></div> : null}
      <label htmlFor={inputId}>{text.message}</label><textarea id={inputId} rows={3} value={snapshot.text} disabled={!writable || !session.canEditText} aria-describedby={hintId} onChange={(event) => session.setText(event.target.value)} />
      {new TextEncoder().encode(snapshot.text).length > 64_000 ? <p role="alert">{text.bytes}</p> : null}
      <p id={hintId} className="conversation-hint">{text.limits}</p>
      {snapshot.fileProblem ? <p role="alert">{text[snapshot.fileProblem]}</p> : null}
      <ul className="conversation-uploads">{snapshot.uploads.map((item) => <li key={item.id}>
        {item.category === "image" && item.state === "ready" ? <LocalImage file={item.file} name={item.file.name} locale={locale} /> : null}
        <div><strong>{item.file.name}</strong><small>{formatBytes(item.file.size)} · {text[({ queued: "queuedUpload", uploading: "uploading", ready: "ready", uncertain: "uploadUncertain", rejected: "uploadRejected", removing: "removing", remove_uncertain: "removeUncertain" } as const)[item.state]]}</small></div>
        {item.state === "uncertain" ? <button disabled={locked || !canWrite} className="button button-secondary button-inline" type="button" onClick={() => session.retryUpload(item.id)}>{text.retry}</button> : null}
        {["queued", "ready", "rejected", "remove_uncertain"].includes(item.state) ? <button disabled={locked || !canWrite} className="button button-secondary button-inline" type="button" onClick={() => void session.remove(item.id)}>{item.state === "remove_uncertain" ? text.retryRemove : text.remove}</button> : null}
      </li>)}</ul>
      <div className="conversation-compose-actions"><label className="conversation-file-label">{text.attach}<input type="file" multiple disabled={!canWrite || locked} onChange={(event) => { session.addFiles(Array.from(event.currentTarget.files ?? [])); event.currentTarget.value = ""; }} /></label><button className="button button-primary" type="submit" disabled={!canWrite || (snapshot.sendState !== "uncertain" && !session.canSend)}>{snapshot.sendState === "sending" ? text.sending : snapshot.sendState === "uncertain" ? text.retrySend : text.send}</button></div>
    </form> : <p className="conversation-hint">{text.closedNotice}</p>}
  </dialog>;
}
function formatBytes(size: number): string { return size >= 1_000_000 ? `${(size / 1_000_000).toFixed(1)} MB` : `${Math.max(1, Math.round(size / 1000))} KB`; }
function LocalImage({ file, name, locale }: { file: Blob; name: string; locale: Locale }) {
  const [url, setUrl] = useState<string | null>(null), [failed, setFailed] = useState(false);
  useEffect(() => { const lease = new ConversationObjectUrl(); setUrl(lease.create(file)); setFailed(false); return () => lease.release(); }, [file]);
  return failed ? <span>{conversationText(locale).previewError}</span> : url ? <img className="conversation-image" src={url} alt={name} onError={() => setFailed(true)} /> : null;
}
function BoundAttachment({ api, thread, message, item, locale }: { api: ConversationApi; thread: string; message: string; item: Attachment; locale: Locale }) {
  const [blob, setBlob] = useState<Blob | null>(null), [failed, setFailed] = useState(false), [loading, setLoading] = useState(false);
  const alive = useRef(true), active = useRef<AbortController | null>(null), downloads = useRef(new Set<ConversationObjectUrl>()), text = conversationText(locale);
  useEffect(() => { alive.current = true; const controller = new AbortController(); if (item.category === "image") void api.download(thread, message, item, controller.signal).then((value) => { if (!controller.signal.aborted) setBlob(value); }).catch(() => { if (!controller.signal.aborted) setFailed(true); }); return () => { alive.current = false; controller.abort(); active.current?.abort(); for (const lease of downloads.current) lease.release(); downloads.current.clear(); }; }, [api, thread, message, item.attachment_id, item.digest]);
  const download = async () => { if (loading) return; const controller = new AbortController(); active.current = controller; setLoading(true); setFailed(false); try { const value = blob ?? await api.download(thread, message, item, controller.signal); if (!alive.current || controller.signal.aborted) return; const lease = new ConversationObjectUrl(); downloads.current.add(lease); const anchor = document.createElement("a"); anchor.href = lease.create(value); anchor.download = item.display_name; anchor.click(); setTimeout(() => { lease.release(); downloads.current.delete(lease); }, 1000); } catch { if (alive.current) setFailed(true); } finally { if (alive.current) setLoading(false); } };
  return <div className="conversation-attachment">{blob && item.category === "image" ? <LocalImage file={blob} name={item.display_name} locale={locale} /> : null}<span>{item.display_name} <small>{formatBytes(item.size_bytes)}</small></span><button type="button" className="button button-secondary button-inline" disabled={loading} onClick={() => void download()}>{text.download}</button>{failed ? <small role="alert">{text.downloadError}</small> : null}</div>;
}
