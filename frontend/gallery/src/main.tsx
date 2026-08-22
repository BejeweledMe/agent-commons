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

function tokenFromFragment(): string | null {
  return new URLSearchParams(window.location.hash.slice(1)).get("t");
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
    const token = tokenFromFragment();
    if (token === null) {
      setState({ kind: "authentication_required", code: "unauthorized" });
      return;
    }

    const controller = new AbortController();
    void fetch("/api/gallery", {
      headers: { Authorization: `Bearer ${token}` },
      signal: controller.signal,
    })
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
