import { type FormEvent, type ReactElement, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";

import {
  AbortSlot,
  applyDocumentLocale,
  type GalleryPackage,
  type GalleryResponse,
  type GalleryScreen,
  feedbackMessageIsValid,
  parseFeedbackSuccess,
  parseGalleryResponse,
  parseSuccessfulGalleryRefresh,
  readBoundedJson,
} from "./contracts";
import messages from "./i18n.json";
import "./styles.css";
import { AuthoringPanel } from "./AuthoringPanel";

type Locale = keyof typeof messages;
type MessageKey = keyof (typeof messages)["en"];
type GalleryState =
  | { kind: "checking" }
  | { kind: "authentication_required"; code: "unauthorized" }
  | { kind: "gallery_data_unavailable"; code: "gallery_empty" }
  | { kind: "gallery_unavailable"; code: string }
  | { kind: "ready"; response: GalleryResponse };
type InspectorState =
  | { kind: "closed" }
  | { kind: "loading"; package: GalleryPackage; screen: GalleryScreen }
  | { kind: "ready"; package: GalleryPackage; screen: GalleryScreen; objectUrl: string }
  | { kind: "error"; package: GalleryPackage; screen: GalleryScreen; code: string };
type FeedbackState =
  | { kind: "idle" }
  | { kind: "submitting" }
  | { kind: "success"; threadId: string }
  | { kind: "stale"; code: "design_package_stale" }
  | { kind: "authentication_required"; code: "unauthorized" }
  | { kind: "error"; code: string };
type SessionExchange = { api_base?: unknown };

const API_BASE_STORAGE_KEY = "agent_commons.ui.api_base";
const API_BASE_PATTERN = /^\/api\/[A-Za-z0-9_-]{32,128}$/;
const ERROR_CODE_PATTERN = /^[a-z][a-z0-9_]{0,79}$/;
const SUPPORTED_MEDIA = new Set(["image/png", "image/jpeg"]);
const FEEDBACK_CODES = new Set([
  "design_package_missing", "design_package_stale", "design_package_unauthorized",
  "design_package_unsafe", "design_package_oversized", "design_package_invalid",
  "design_package_unavailable", "invalid_request",
]);
const READ_REFUSAL_CODES = new Set(["setup_uninitialized", "setup_not_a_repository"]);
const PREVIEW_CODES = new Set([
  "artifact_preview_invalid_id", "artifact_preview_not_found", "artifact_preview_manifest_invalid",
  "artifact_preview_classification_blocked", "artifact_preview_unsupported_media_type",
  "artifact_preview_missing_source", "artifact_preview_symlink_source", "artifact_preview_non_regular_source",
  "artifact_preview_stale_source", "artifact_preview_oversize", "artifact_preview_invalid_image",
  "artifact_preview_pixel_limit",
]);

function exchangeCodeFromFragment(): string | null {
  return new URLSearchParams(window.location.hash.slice(1)).get("c");
}

function storedApiBase(): string {
  try {
    const value = window.sessionStorage.getItem(API_BASE_STORAGE_KEY);
    if (value !== null && API_BASE_PATTERN.test(value)) return value;
    if (value !== null) window.sessionStorage.removeItem(API_BASE_STORAGE_KEY);
  } catch { /* A fresh exchange still works when storage is disabled. */ }
  return "";
}

function clearStoredApiBase(): void {
  try { window.sessionStorage.removeItem(API_BASE_STORAGE_KEY); } catch { /* Nothing durable to clear. */ }
}

function rememberApiBase(value: unknown): string {
  if (typeof value !== "string" || !API_BASE_PATTERN.test(value)) throw new Error("local_session_exchange_failed");
  try { window.sessionStorage.setItem(API_BASE_STORAGE_KEY, value); } catch { /* Keep it in this page closure. */ }
  return value;
}

function message(locale: Locale, key: MessageKey): string { return messages[locale][key]; }

function safeErrorCode(value: unknown, allowed?: Set<string>): string | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  const error = (value as Record<string, unknown>).error;
  if (typeof error !== "object" || error === null || Array.isArray(error)) return null;
  const code = (error as Record<string, unknown>).code;
  if (typeof code !== "string" || !ERROR_CODE_PATTERN.test(code)) return null;
  return allowed === undefined || allowed.has(code) ? code : null;
}

function formatTimestamp(locale: Locale, value: string | null): string {
  if (value === null) return message(locale, "not_available");
  return new Intl.DateTimeFormat(locale === "ru" ? "ru-RU" : "en-GB", {
    dateStyle: "medium", timeStyle: "short",
  }).format(new Date(value));
}

type ScreenCardProps = {
  packageValue: GalleryPackage;
  screen: GalleryScreen;
  locale: Locale;
  open: (packageValue: GalleryPackage, screen: GalleryScreen, opener: HTMLElement) => void;
};

function ScreenCard({ packageValue, screen, locale, open }: ScreenCardProps): ReactElement {
  const text = (key: MessageKey) => message(locale, key);
  const unavailable = screen.preview_state !== "ready";
  return (
    <article className={`screen-card screen-card-${screen.preview_state}`}>
      <div className="screen-card-order" aria-label={`${text("screen_order")} ${screen.ordinal}`}>{screen.ordinal}</div>
      <div className="screen-card-body">
        <h3>{screen.title}</h3>
        <p className="screen-card-meta">{screen.width === null || screen.height === null ? screen.media_type : `${screen.media_type} · ${screen.width} × ${screen.height}`}</p>
        <span className={`status-pill status-pill-${screen.preview_state}`}>{text(`preview_${screen.preview_state}` as MessageKey)}</span>
      </div>
      <button className="screen-card-action" disabled={unavailable} onClick={(event) => open(packageValue, screen, event.currentTarget)} type="button">
        {unavailable ? text("preview_blocked") : text("inspect_screen")}
      </button>
    </article>
  );
}

function GalleryApp(): ReactElement {
  const [locale, setLocale] = useState<Locale>("en");
  const [state, setState] = useState<GalleryState>({ kind: "checking" });
  const [apiBase, setApiBase] = useState("");
  const [inspector, setInspector] = useState<InspectorState>({ kind: "closed" });
  const [feedback, setFeedback] = useState<FeedbackState>({ kind: "idle" });
  const [feedbackMessage, setFeedbackMessage] = useState("");
  const [announcement, setAnnouncement] = useState("");
  const dialogRef = useRef<HTMLDialogElement>(null);
  const openerRef = useRef<HTMLElement | null>(null);
  const previewRequests = useRef(new AbortSlot());
  const feedbackRequests = useRef(new AbortSlot());
  const objectUrlRef = useRef<string | null>(null);
  const feedbackIntentRef = useRef<{ fingerprint: string; key: string } | null>(null);
  const text = useMemo(() => (key: MessageKey) => message(locale, key), [locale]);

  function applyGalleryResponse(response: GalleryResponse): void {
    if (response.state === "empty") setState({ kind: "gallery_data_unavailable", code: "gallery_empty" });
    else if (response.state === "error") setState({ kind: "gallery_unavailable", code: response.error?.code ?? "gallery_unavailable" });
    else if (response.state === "ready" || response.state === "stale") setState({ kind: "ready", response });
    else setState({ kind: "gallery_unavailable", code: "gallery_contract_invalid" });
  }

  async function refreshGallery(): Promise<void> {
    if (!apiBase) return;
    const response = await fetch(`${apiBase}/gallery`, { credentials: "same-origin" });
    const payload = await parseSuccessfulGalleryRefresh(response);
    applyGalleryResponse(payload);
  }

  useEffect(() => {
    applyDocumentLocale(document.documentElement, locale);
  }, [locale]);

  function revokeObjectUrl(): void {
    if (objectUrlRef.current !== null) URL.revokeObjectURL(objectUrlRef.current);
    objectUrlRef.current = null;
  }

  useEffect(() => {
    const controller = new AbortController();
    const exchangeCode = exchangeCodeFromFragment();
    let currentApiBase = storedApiBase();
    window.history.replaceState(null, "", window.location.pathname);

    async function loadGallery(): Promise<GalleryResponse> {
      if (exchangeCode !== null) {
        const exchange = await fetch("/api/auth/exchange", {
          method: "POST", headers: { "Content-Type": "application/json" }, credentials: "same-origin",
          body: JSON.stringify({ code: exchangeCode }), signal: controller.signal,
        });
        if (!exchange.ok) { clearStoredApiBase(); throw new Error("local_session_exchange_failed"); }
        const payload = (await readBoundedJson(exchange, 4_096)) as SessionExchange;
        try { currentApiBase = rememberApiBase(payload.api_base); }
        catch { clearStoredApiBase(); throw new Error("local_session_exchange_failed"); }
      }
      if (!currentApiBase) throw new Error("local_session_exchange_failed");
      setApiBase(currentApiBase);
      const response = await fetch(`${currentApiBase}/gallery`, { credentials: "same-origin", signal: controller.signal });
      if (response.status === 401 || response.status === 404) { clearStoredApiBase(); throw new Error("local_session_exchange_failed"); }
      const raw = await readBoundedJson(response);
      if (!response.ok && (raw as { schema?: unknown }).schema !== "agent_commons.gallery.v1") {
        throw new Error(safeErrorCode(raw, READ_REFUSAL_CODES) ?? "gallery_unavailable");
      }
      const payload = parseGalleryResponse(raw);
      if (!response.ok && payload.state !== "error") throw new Error("gallery_contract_invalid");
      return payload;
    }

    void loadGallery().then(applyGalleryResponse).catch((error: unknown) => {
      if (error instanceof DOMException && error.name === "AbortError") return;
      if (error instanceof Error && error.message === "local_session_exchange_failed") {
        setState({ kind: "authentication_required", code: "unauthorized" }); return;
      }
      const code = error instanceof Error && (READ_REFUSAL_CODES.has(error.message) || error.message === "gallery_contract_invalid")
        ? error.message : "gallery_unavailable";
      setState({ kind: "gallery_unavailable", code });
    });
    return () => controller.abort();
  }, []);

  useEffect(() => () => {
    previewRequests.current.abort();
    feedbackRequests.current.abort();
    revokeObjectUrl();
  }, []);

  function closeInspector(): void {
    previewRequests.current.abort();
    feedbackRequests.current.abort();
    revokeObjectUrl();
    setInspector({ kind: "closed" });
    setFeedback({ kind: "idle" });
    setFeedbackMessage("");
    feedbackIntentRef.current = null;
    setAnnouncement(text("inspector_closed"));
    dialogRef.current?.close();
    window.setTimeout(() => openerRef.current?.focus(), 0);
  }

  async function openInspector(packageValue: GalleryPackage, screen: GalleryScreen, opener: HTMLElement): Promise<void> {
    openerRef.current = opener;
    revokeObjectUrl();
    const controller = previewRequests.current.begin();
    setInspector({ kind: "loading", package: packageValue, screen });
    setFeedback({ kind: "idle" });
    setFeedbackMessage("");
    feedbackIntentRef.current = null;
    setAnnouncement(text("preview_loading"));
    window.setTimeout(() => { const dialog = dialogRef.current; if (dialog !== null && !dialog.open) dialog.showModal(); }, 0);
    try {
      if (!apiBase || !screen.preview_eligible || screen.preview_state !== "ready") throw new Error("artifact_preview_unavailable");
      const preview = await fetch(`${apiBase}/artifacts/${encodeURIComponent(screen.artifact_id)}/preview`, {
        credentials: "same-origin", signal: controller.signal,
      });
      if (preview.status === 401) { clearStoredApiBase(); throw new Error("unauthorized"); }
      if (!preview.ok) {
        const payload = await readBoundedJson(preview, 16_384).catch(() => null);
        throw new Error(safeErrorCode(payload, PREVIEW_CODES) ?? "artifact_preview_unavailable");
      }
      const contentType = (preview.headers.get("Content-Type") ?? "").split(";", 1)[0].trim();
      if (!SUPPORTED_MEDIA.has(contentType)) throw new Error("artifact_preview_unsupported_media_type");
      const blob = await preview.blob();
      if (blob.size < 1 || !SUPPORTED_MEDIA.has(blob.type)) throw new Error("artifact_preview_invalid_image");
      const detailResponse = await fetch(`${apiBase}/gallery/${encodeURIComponent(packageValue.design_package_id)}`, {
        credentials: "same-origin", signal: controller.signal,
      });
      if (!detailResponse.ok) throw new Error("gallery_package_stale");
      const detail = parseGalleryResponse(await readBoundedJson(detailResponse));
      const currentPackage = detail.packages[0];
      const currentScreen = currentPackage?.screens.find((candidate) => candidate.screen_id === screen.screen_id);
      if (currentPackage?.revision !== packageValue.revision || currentScreen?.artifact_revision !== screen.artifact_revision ||
          currentScreen?.artifact_content_revision !== screen.artifact_content_revision || currentScreen?.producer_task_revision !== screen.producer_task_revision ||
          currentScreen?.preview_state !== "ready") throw new Error("gallery_package_stale");
      if (!previewRequests.current.isCurrent(controller)) return;
      const objectUrl = URL.createObjectURL(blob);
      objectUrlRef.current = objectUrl;
      setInspector({ kind: "ready", package: packageValue, screen, objectUrl });
      setAnnouncement(text("preview_ready"));
    } catch (error: unknown) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      if (!previewRequests.current.isCurrent(controller)) return;
      setInspector({ kind: "error", package: packageValue, screen, code: error instanceof Error ? error.message : "artifact_preview_unavailable" });
      setAnnouncement(text("preview_failed"));
    } finally { previewRequests.current.finish(controller); }
  }

  async function submitFeedback(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (inspector.kind === "closed" || !feedbackMessageIsValid(feedbackMessage) || !apiBase) {
      setFeedback({ kind: "error", code: "invalid_request" }); return;
    }
    const packageValue = inspector.package;
    const screen = inspector.screen;
    const controller = feedbackRequests.current.begin();
    const normalizedMessage = feedbackMessage.trim();
    const fingerprint = [packageValue.revision, screen.screen_id, screen.artifact_revision, screen.producer_task_revision, normalizedMessage].join("\n");
    if (feedbackIntentRef.current?.fingerprint !== fingerprint) {
      feedbackIntentRef.current = { fingerprint, key: `gallery-feedback-${crypto.randomUUID()}` };
    }
    const idempotencyKey = feedbackIntentRef.current.key;
    setFeedback({ kind: "submitting" });
    setAnnouncement(text("feedback_submitting"));
    try {
      const response = await fetch(`${apiBase}/gallery/${encodeURIComponent(packageValue.design_package_id)}/screens/${encodeURIComponent(screen.screen_id)}/feedback`, {
        method: "POST", headers: { "Content-Type": "application/json" }, credentials: "same-origin", signal: controller.signal,
        body: JSON.stringify({
          design_package_revision: packageValue.revision,
          artifact_revision: screen.artifact_revision,
          producer_task_revision: screen.producer_task_revision,
          message: normalizedMessage,
          idempotency_key: idempotencyKey,
        }),
      });
      const payload = await readBoundedJson(response, 131_072).catch(() => null);
      if (!feedbackRequests.current.isCurrent(controller)) return;
      if (response.status === 401) {
        clearStoredApiBase(); setFeedback({ kind: "authentication_required", code: "unauthorized" });
        setAnnouncement(text("feedback_authentication_required")); return;
      }
      if (!response.ok) {
        const code = safeErrorCode(payload, FEEDBACK_CODES) ?? "feedback_failed";
        if (code === "design_package_stale") { setFeedback({ kind: "stale", code }); setAnnouncement(text("feedback_stale")); }
        else { setFeedback({ kind: "error", code }); setAnnouncement(text("feedback_failed")); }
        return;
      }
      const result = parseFeedbackSuccess(payload);
      setFeedback({ kind: "success", threadId: result.threadId });
      setFeedbackMessage("");
      feedbackIntentRef.current = null;
      setAnnouncement(text("feedback_sent"));
    } catch (error: unknown) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      if (!feedbackRequests.current.isCurrent(controller)) return;
      setFeedback({ kind: "error", code: "feedback_failed" });
      setAnnouncement(text("feedback_failed"));
    } finally { feedbackRequests.current.finish(controller); }
  }

  const inspectorPackage = inspector.kind === "closed" ? null : inspector.package;
  const inspectorScreen = inspector.kind === "closed" ? null : inspector.screen;
  const stateHelpKey: MessageKey = state.kind === "gallery_unavailable" && state.code === "setup_uninitialized"
    ? "setup_uninitialized_help" : state.kind === "gallery_unavailable" && state.code === "setup_not_a_repository"
      ? "setup_not_a_repository_help" : state.kind === "checking" ? "checking_access" : (`${state.kind}_help` as MessageKey);

  return (
    <main className="gallery-app">
      <p className="sr-only" aria-live="polite">{announcement}</p>
      <header className="gallery-header">
        <div><p className="gallery-eyebrow">Agent Commons</p><h1>{text("app_title")}</h1><p className="gallery-subtitle">{text("app_subtitle")}</p></div>
        <div className="locale-switcher" aria-label={text("language")} role="group">
          <button aria-label={text("switch_to_english")} aria-pressed={locale === "en"} className={locale === "en" ? "locale-button locale-button-selected" : "locale-button"} onClick={() => setLocale("en")} type="button">EN</button>
          <button aria-label={text("switch_to_russian")} aria-pressed={locale === "ru"} className={locale === "ru" ? "locale-button locale-button-selected" : "locale-button"} onClick={() => setLocale("ru")} type="button">RU</button>
        </div>
      </header>
      {apiBase ? <AuthoringPanel
        announce={setAnnouncement}
        apiBase={apiBase}
        onPublished={refreshGallery}
        packages={state.kind === "ready" ? state.response.packages : []}
        text={(key) => text(key as MessageKey)}
      /> : null}
      {state.kind === "ready" ? (
        <>
          <section className="gallery-summary" aria-label={text("package_summary")}>
            <div><strong>{state.response.packages.length}</strong><span>{text("packages")}</span></div>
            <div><strong>{state.response.packages.reduce((total, item) => total + item.screen_count, 0)}</strong><span>{text("screens")}</span></div>
            <p>{text("snapshot_time")}: <time>{formatTimestamp(locale, state.response.read_at)}</time></p>
          </section>
          {state.response.state === "stale" ? <aside className="gallery-warning" role="status"><strong>{text("stale_title")}</strong> {text("stale_help")}</aside> : null}
          <section className="gallery-canvas" aria-label={text("canvas_label")}>
            <div className="gallery-board">
              {state.response.packages.map((packageValue) => (
                <section aria-labelledby={`package-${packageValue.design_package_id}`} className="package-column" key={packageValue.design_package_id}>
                  <header className="package-header"><h2 id={`package-${packageValue.design_package_id}`}>{packageValue.title}</h2><p><code>{packageValue.revision}</code></p></header>
                  <ol className="screen-list">{packageValue.screens.map((screen) => <li key={screen.screen_id}><ScreenCard locale={locale} open={openInspector} packageValue={packageValue} screen={screen} /></li>)}</ol>
                </section>
              ))}
            </div>
          </section>
        </>
      ) : (
        <section className="gallery-canvas" aria-label={text("canvas_label")}>
          <div className="gallery-grid-background" aria-hidden="true" />
          <section className="gallery-state" aria-live="polite" role="status">
            <h2>{text(state.kind === "checking" ? "checking_access" : state.kind)}</h2>
            {state.kind === "checking" ? <div className="loading-line" aria-hidden="true" /> : <p>{text(stateHelpKey)}</p>}
            {state.kind === "checking" ? null : <p className="gallery-code">{text("status_code")}: <code>{state.code}</code></p>}
          </section>
        </section>
      )}
      <dialog aria-labelledby="inspector-title" className="inspector" onCancel={(event) => { event.preventDefault(); closeInspector(); }} ref={dialogRef}>
        {inspectorPackage === null || inspectorScreen === null ? null : (
          <div className="inspector-layout">
            <header className="inspector-header"><div><p className="gallery-eyebrow">{inspectorPackage.title}</p><h2 id="inspector-title">{inspectorScreen.title}</h2></div><button autoFocus className="icon-button" onClick={closeInspector} type="button">{text("close_inspector")}</button></header>
            <div className="inspector-body">
              <section className="preview-stage" aria-label={text("screen_preview")}>
                {inspector.kind === "loading" ? <p role="status">{text("preview_loading")}</p> : null}
                {inspector.kind === "error" ? <div className="preview-error" role="alert"><strong>{text("preview_failed")}</strong><p>{text("preview_failed_help")}</p><code>{inspector.code}</code></div> : null}
                {inspector.kind === "ready" ? <img alt={`${inspectorScreen.title} — ${text("screen_preview")}`} src={inspector.objectUrl} /> : null}
              </section>
              <aside className="inspector-details">
                <h3>{text("provenance")}</h3>
                <dl>
                  <dt>{text("package_id")}</dt><dd><code>{inspectorPackage.design_package_id}</code></dd><dt>{text("package_revision")}</dt><dd><code>{inspectorPackage.revision}</code></dd>
                  <dt>{text("artifact_id")}</dt><dd><code>{inspectorScreen.artifact_id}</code></dd><dt>{text("artifact_revision")}</dt><dd><code>{inspectorScreen.artifact_revision}</code></dd>
                  <dt>{text("content_revision")}</dt><dd><code>{inspectorScreen.artifact_content_revision}</code></dd><dt>{text("producer_task")}</dt><dd><code>{inspectorScreen.producer_task_id}</code></dd>
                  <dt>{text("task_revision")}</dt><dd><code>{inspectorScreen.producer_task_revision}</code></dd><dt>{text("producer_session")}</dt><dd><code>{inspectorScreen.producer_session_id}</code></dd>
                  <dt>{text("classification")}</dt><dd><code>{inspectorScreen.classification}</code></dd>
                </dl>
                <section className="feedback-panel" aria-labelledby="feedback-title">
                  <h3 id="feedback-title">{text("feedback_title")}</h3>
                  <form onSubmit={(event) => void submitFeedback(event)}>
                    <label htmlFor="feedback-message">{text("feedback_message_label")}</label>
                    <textarea disabled={feedback.kind === "submitting"} id="feedback-message" maxLength={8192} onChange={(event) => setFeedbackMessage(event.target.value)} rows={5} value={feedbackMessage} />
                    <p className="feedback-help" id="feedback-help">{text("feedback_message_help")}</p>
                    <button aria-describedby="feedback-help" className="screen-card-action" disabled={feedback.kind === "submitting" || !feedbackMessageIsValid(feedbackMessage)} type="submit">{feedback.kind === "submitting" ? text("feedback_submitting") : text("feedback_submit")}</button>
                  </form>
                  <div aria-live="polite" className={`feedback-status feedback-status-${feedback.kind}`}>
                    {feedback.kind === "success" ? <p>{text("feedback_sent")} <code>{feedback.threadId}</code></p> : null}
                    {feedback.kind === "stale" ? <p role="alert">{text("feedback_stale_help")}</p> : null}
                    {feedback.kind === "authentication_required" ? <p role="alert">{text("feedback_authentication_required_help")}</p> : null}
                    {feedback.kind === "error" ? <p role="alert">{text("feedback_failed_help")} <code>{feedback.code}</code></p> : null}
                  </div>
                </section>
              </aside>
            </div>
          </div>
        )}
      </dialog>
    </main>
  );
}

createRoot(document.getElementById("root") as HTMLElement).render(<GalleryApp />);
