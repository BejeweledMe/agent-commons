import { type ReactElement, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { Background, ReactFlow } from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import messages from "./i18n.json";
import "./styles.css";

type Locale = keyof typeof messages;
type MessageKey = keyof (typeof messages)["en"];

type GalleryState =
  | { kind: "checking" }
  | { kind: "authentication_required"; code: "unauthorized" }
  | { kind: "gallery_data_unavailable"; code: "gallery_data_unavailable" }
  | { kind: "gallery_unavailable"; code: string };

type ApiError = { error?: { code?: string } };
type SessionExchange = { api_base?: unknown };
const API_BASE_STORAGE_KEY = "agent_commons.ui.api_base";
const API_BASE_PATTERN = /^\/api\/[A-Za-z0-9_-]{32,128}$/;

function exchangeCodeFromFragment(): string | null {
  return new URLSearchParams(window.location.hash.slice(1)).get("c");
}

function storedApiBase(): string {
  try {
    const value = window.sessionStorage.getItem(API_BASE_STORAGE_KEY);
    if (value !== null && API_BASE_PATTERN.test(value)) {
      return value;
    }
    if (value !== null) {
      window.sessionStorage.removeItem(API_BASE_STORAGE_KEY);
    }
  } catch {
    // A fresh exchange still works when storage is disabled for this tab.
  }
  return "";
}

function clearStoredApiBase(): void {
  try {
    window.sessionStorage.removeItem(API_BASE_STORAGE_KEY);
  } catch {
    // There is no durable value to clear when storage is disabled.
  }
}

function rememberApiBase(value: unknown): string {
  if (typeof value !== "string" || !API_BASE_PATTERN.test(value)) {
    throw new Error("local_session_exchange_failed");
  }
  try {
    window.sessionStorage.setItem(API_BASE_STORAGE_KEY, value);
  } catch {
    // Keep the capability in this page's closure if storage is unavailable.
  }
  return value;
}

function message(locale: Locale, key: MessageKey): string {
  return messages[locale][key];
}

function GalleryApp(): ReactElement {
  const [locale, setLocale] = useState<Locale>("en");
  const [state, setState] = useState<GalleryState>({ kind: "checking" });
  const text = useMemo(
    () => (key: MessageKey) => message(locale, key),
    [locale],
  );

  useEffect(() => {
    const controller = new AbortController();
    const exchangeCode = exchangeCodeFromFragment();
    let apiBase = storedApiBase();
    // The exchange code is a one-time capability, never an API credential.
    // Remove it before the first network request so it cannot remain in a
    // copied URL or history entry. The returned API base stays in this exact
    // origin's sessionStorage, so refresh can restore it without sharing it
    // with a newly opened browser session.
    window.history.replaceState(null, "", window.location.pathname);

    async function loadGallery(): Promise<Response> {
      if (exchangeCode !== null) {
        const exchange = await fetch("/api/auth/exchange", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify({ code: exchangeCode }),
          signal: controller.signal,
        });
        if (!exchange.ok) {
          clearStoredApiBase();
          throw new Error("local_session_exchange_failed");
        }
        const payload = (await exchange.json()) as SessionExchange;
        try {
          apiBase = rememberApiBase(payload.api_base);
        } catch {
          clearStoredApiBase();
          throw new Error("local_session_exchange_failed");
        }
      }
      if (!apiBase) {
        throw new Error("local_session_exchange_failed");
      }
      const response = await fetch(`${apiBase}/gallery`, {
        credentials: "same-origin",
        signal: controller.signal,
      });
      if (response.status === 401 || response.status === 404) {
        clearStoredApiBase();
        throw new Error("local_session_exchange_failed");
      }
      return response;
    }

    void loadGallery()
      .then(async (response) => {
        const payload = (await response.json()) as ApiError;
        const code = payload.error?.code ?? "gallery_unavailable";
        if (response.status === 401 || code === "unauthorized") {
          setState({ kind: "authentication_required", code: "unauthorized" });
        } else if (code === "gallery_data_unavailable") {
          setState({ kind: "gallery_data_unavailable", code });
        } else {
          setState({ kind: "gallery_unavailable", code });
        }
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        if (error instanceof Error && error.message === "local_session_exchange_failed") {
          setState({ kind: "authentication_required", code: "unauthorized" });
          return;
        }
        setState({ kind: "gallery_unavailable", code: "gallery_unavailable" });
      });
    return () => controller.abort();
  }, []);

  const messageKey: MessageKey = state.kind === "checking" ? "checking_access" : state.kind;
  const helpKey =
    state.kind === "checking" ? null : (`${state.kind}_help` as MessageKey);

  return (
    <main className="gallery-app">
      <header className="gallery-header">
        <div>
          <p className="gallery-eyebrow">Agent Commons</p>
          <h1>{text("app_title")}</h1>
        </div>
        <div className="locale-switcher" aria-label={text("language")}>
          <button
            aria-label={text("switch_to_english")}
            aria-pressed={locale === "en"}
            className={locale === "en" ? "locale-button locale-button-selected" : "locale-button"}
            onClick={() => setLocale("en")}
            type="button"
          >
            EN
          </button>
          <button
            aria-label={text("switch_to_russian")}
            aria-pressed={locale === "ru"}
            className={locale === "ru" ? "locale-button locale-button-selected" : "locale-button"}
            onClick={() => setLocale("ru")}
            type="button"
          >
            RU
          </button>
        </div>
      </header>
      <section className="gallery-canvas" aria-label={text("canvas_label")}>
        <ReactFlow edges={[]} fitView nodes={[]} nodesDraggable={false} nodesFocusable={false}>
          <Background gap={24} size={1} />
        </ReactFlow>
        <section className="gallery-state" aria-live="polite" role="status">
          <h2>{text(messageKey)}</h2>
          {helpKey === null ? null : <p>{text(helpKey)}</p>}
          {state.kind === "checking" ? null : (
            <p className="gallery-code">
              {text("status_code")}: <code>{state.code}</code>
            </p>
          )}
        </section>
      </section>
    </main>
  );
}

createRoot(document.getElementById("root") as HTMLElement).render(<GalleryApp />);
