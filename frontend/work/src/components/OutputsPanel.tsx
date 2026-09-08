import { createContext, type ReactElement, type ReactNode, useContext, useEffect, useId, useMemo, useRef, useState } from "react";
import type { WorkApi } from "../api.js";
import type { Locale } from "../i18n.js";
import { OutputsApi, presentCurrent } from "../outputsApi.js";
import { outputText } from "../outputsStrings.js";
import { canViewImage, liveNavigation, scopeKey, type LiveOutput, type ImageOutput, type OutputList, type OutputScope, type OutputVersions } from "../outputsTypes.js";

type Selection = { owner: OutputsApi; projectId: string | null; scope: OutputScope; title: string; opener: HTMLButtonElement };
type OutputContext = { api: OutputsApi; locale: Locale; revision: string; open: (scope: OutputScope, title: string, opener: HTMLButtonElement) => void };
const Context = createContext<OutputContext | null>(null);

export function OutputsWorkspace({ api: transport, projectId, revision, locale, children }: {
  api: WorkApi; projectId: string | null; revision: string | null; locale: Locale; children: ReactNode;
}): ReactElement {
  const api = useMemo(() => new OutputsApi(transport), [transport]);
  const [selected, setSelected] = useState<Selection | null>(null);
  useEffect(() => () => api.dispose(), [api]);
  const visible = selected?.owner === api && selected.projectId === projectId ? selected : null;
  const context = useMemo<OutputContext>(() => ({ api, locale, revision: revision ?? "",
    open: (scope, title, opener) => setSelected({ owner: api, projectId, scope, title, opener }),
  }), [api, projectId, revision, locale]);
  function close(selection: Selection): void {
    setSelected((current) => current === selection ? null : current);
    if (selection.owner === api && selection.projectId === projectId && selection.opener.isConnected) selection.opener.focus();
  }
  return <Context.Provider value={context}>{children}{visible ? <OutputsPanel key={scopeKey(visible.scope)} api={api} scope={visible.scope} title={visible.title} locale={locale} revision={revision ?? ""} onClose={() => close(visible)} /> : null}</Context.Provider>;
}

export function OutputsButton({ scope, title, eager = false }: { scope: OutputScope; title: string; eager?: boolean }): ReactElement {
  const context = useContext(Context), host = useRef<HTMLSpanElement>(null);
  const [visible, setVisible] = useState(eager);
  const [result, setResult] = useState<{ owner: OutputsApi; key: string; revision: string; total: number } | null>(null);
  const key = scopeKey(scope), owner = context?.api, revision = context?.revision ?? "";
  useEffect(() => {
    if (eager || typeof IntersectionObserver === "undefined") { setVisible(true); return; }
    const observer = new IntersectionObserver((entries) => { if (entries.some((entry) => entry.isIntersecting)) setVisible(true); });
    if (host.current) observer.observe(host.current);
    return () => observer.disconnect();
  }, [eager]);
  useEffect(() => {
    if (!owner || !visible) return;
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout> | undefined;
    const load = (): void => {
      void presentCurrent(owner.summary(scope, revision), controller.signal,
        (summary) => { setResult({ owner, key, revision, total: summary.total }); timer = setTimeout(load, 15_000); },
        () => { setResult(null); timer = setTimeout(load, 15_000); });
    };
    load(); return () => { controller.abort(); clearTimeout(timer); };
  }, [owner, key, revision, visible]);
  const total = result?.owner === owner && result?.key === key && result?.revision === revision ? result.total : null;
  return <span className="outputs-entry" ref={host}>{context ? <OutputsAction count={total} locale={context.locale} title={title} onOpen={(button) => context.open(scope, title, button)} /> : null}</span>;
}

export function OutputsAction({ count, locale, title, onOpen }: { count: number | null; locale: Locale; title: string; onOpen: (button: HTMLButtonElement) => void }): ReactElement | null {
  if (count === null || count <= 0) return null;
  return <button type="button" className="button button-secondary button-inline" aria-label={`${outputText(locale, "titlePrefix")} ${title}`} onClick={(event) => onOpen(event.currentTarget)}>{outputText(locale, "results")} <span aria-hidden="true">({count})</span></button>;
}

type ListState = { owner: OutputsApi; key: string; versions: OutputVersions; revision: string; kind: "ready"; value: OutputList } | { owner: OutputsApi; key: string; versions: OutputVersions; revision: string; kind: "error" };
type PreviewState = { owner: OutputsApi; key: string; versions: OutputVersions; outputId: string; kind: "loading" | "error" } | { owner: OutputsApi; key: string; versions: OutputVersions; outputId: string; kind: "ready"; url: string };
export function OutputsPanel({ api, scope, title, locale, revision, onClose }: { api: OutputsApi; scope: OutputScope; title: string; locale: Locale; revision: string; onClose: () => void }): ReactElement {
  const dialog = useRef<HTMLDialogElement>(null), heading = useId();
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, []);
  const [versions, setVersions] = useState<OutputVersions>("latest"), [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<ListState | null>(null), [preview, setPreview] = useState<PreviewState | null>(null);
  const previewRequest = useRef<AbortController | null>(null), objectUrl = useRef<string | null>(null);
  const key = scopeKey(scope), text = (name: Parameters<typeof outputText>[1]): string => outputText(locale, name);
  function releasePreview(): void {
    previewRequest.current?.abort(); previewRequest.current = null;
    if (objectUrl.current) URL.revokeObjectURL(objectUrl.current); objectUrl.current = null;
  }
  useEffect(() => { const node = dialog.current; node?.showModal(); return () => node?.close(); }, []);
  useEffect(() => {
    releasePreview(); setPreview(null); const controller = new AbortController();
    setState(null);
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = (): void => { void presentCurrent(api.list(scope, versions, controller.signal), controller.signal,
      (value) => { setState({ owner: api, key, versions, revision, kind: "ready", value }); timer = setTimeout(load, 15_000); },
      () => { setState({ owner: api, key, versions, revision, kind: "error" }); timer = setTimeout(load, 15_000); }); };
    load();
    return () => { controller.abort(); clearTimeout(timer); releasePreview(); };
  }, [api, key, versions, revision, attempt]);
  async function openImage(item: ImageOutput): Promise<void> {
    releasePreview(); const controller = new AbortController(); previewRequest.current = controller;
    setPreview({ owner: api, key, versions, outputId: item.outputId, kind: "loading" });
    await presentCurrent(api.preview(scope, item, versions, controller.signal), controller.signal, (blob) => {
      const url = URL.createObjectURL(blob); objectUrl.current = url;
      setPreview({ owner: api, key, versions, outputId: item.outputId, kind: "ready", url });
    }, () => setPreview({ owner: api, key, versions, outputId: item.outputId, kind: "error" }));
  }
  const current = state?.owner === api && state.key === key && state.versions === versions && state.revision === revision ? state : null;
  const currentPreview = preview?.owner === api && preview.key === key && preview.versions === versions && current?.kind === "ready" && current.value.items.some((item) => item.kind !== "live_preview" && item.outputId === preview.outputId && canViewImage(item)) ? preview : null;
  return <dialog className="outputs-dialog" ref={dialog} aria-labelledby={heading} onCancel={(event) => { event.preventDefault(); event.stopPropagation(); onClose(); }}>
    <header className="outputs-heading"><h2 id={heading}>{text("titlePrefix")} {title}</h2><button autoFocus type="button" className="button button-secondary button-inline" onClick={onClose}>{text("close")}</button></header>
    <div className="outputs-toolbar"><div role="group" aria-label={text("designs")}>
      <button type="button" className="button button-secondary button-inline" aria-pressed={versions === "latest"} onClick={() => setVersions("latest")}>{text("latest")}</button>
      <button type="button" className="button button-secondary button-inline" aria-pressed={versions === "all"} onClick={() => setVersions("all")}>{text("history")}</button>
    </div><button type="button" className="button button-secondary button-inline" onClick={() => setAttempt((value) => value + 1)}>{text("refresh")}</button></div>
    {current === null ? <p role="status">{text("loading")}</p> : current.kind === "error" ? <p role="alert">{text("failed")}</p> : current.value.items.length === 0 ? <p role="status">{text("empty")}</p> : <ul className="outputs-list">{current.value.items.map((item) => item.kind === "live_preview" ? <LivePreviewCard key={item.outputId} item={item} locale={locale} now={now} uiOrigin={window.location.origin} /> : <li key={item.outputId}>
      <h3>{item.title}</h3><p className="outputs-status" data-state={item.state}>{text(item.state)}</p>
      <p className="small-copy">{text(item.latest ? "current" : "previous")} · {item.versionCount} {text("versions")}</p>
      <ImagePreviewAction item={item} locale={locale} onOpen={() => void openImage(item)} />
      {currentPreview?.outputId !== item.outputId ? null : currentPreview.kind === "loading" ? <p role="status">{text("imageLoading")}</p> : currentPreview.kind === "error" ? <p role="alert">{text("imageFailed")}</p> : currentPreview.kind === "ready" ? <img className="outputs-image" src={currentPreview.url} alt={item.title} width={item.width ?? undefined} height={item.height ?? undefined} /> : null}
    </li>)}</ul>}
  </dialog>;
}


export function LivePreviewCard({ item, locale, now, uiOrigin }: { item: LiveOutput; locale: Locale; now: number; uiOrigin: string }): ReactElement {
  const text = (key: Parameters<typeof outputText>[1]): string => outputText(locale, key);
  const href = liveNavigation(item, now, uiOrigin);
  const state = now >= item.expiresAt && item.state !== "stale" ? "expired" : item.state;
  const date = (value: number): string => new Intl.DateTimeFormat(locale === "ru" ? "ru-RU" : "en-US", { dateStyle: "short", timeStyle: "medium" }).format(new Date(value));
  return <li><p className="small-copy">{text("live")}</p><h3>{item.title}</h3>
    <p className="outputs-status" data-state={state}>{text(state)}</p><p>{text("reachability")}</p>
    <p><code>{item.origin}</code></p><p className="small-copy">{text("published")}: {date(item.publishedAt)} · {text("expires")}: {date(item.expiresAt)}</p>
    {href ? <a className="button button-secondary button-inline" href={href} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer" onClick={(event) => { if (!liveNavigation(item, Date.now(), window.location.origin)) event.preventDefault(); }}>{text("openLive")}</a> : null}
  </li>;
}

export function ImagePreviewAction({ item, locale, onOpen }: { item: ImageOutput; locale: Locale; onOpen: () => void }): ReactElement {
  const text = (key: Parameters<typeof outputText>[1]): string => outputText(locale, key);
  return <>{item.historicalPreviewVerified ? <p>{text("historicalHelp")}</p> : item.state !== "ready" ? <p>{text(item.state === "stale" ? "staleHelp" : "unavailableHelp")}</p> : null}
    {canViewImage(item) ? <button type="button" className="button button-secondary button-inline" onClick={onOpen}>{text(item.historicalPreviewVerified ? "viewHistorical" : "view")}</button> : null}</>;
}
