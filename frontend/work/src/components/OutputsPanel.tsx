import { createContext, type ReactElement, type ReactNode, useContext, useEffect, useId, useMemo, useRef, useState } from "react";
import type { WorkApi } from "../api.js";
import type { Locale } from "../i18n.js";
import { OutputsApi, OutputPreviewQueue, outputStatus, presentCurrent, reviewLabel } from "../outputsApi.js";
import { outputText } from "../outputsStrings.js";
import { pushDialog } from "../dialogStack.js";
import { Icon } from "./Icon.js";
import { canReadReport, type TextResultContent, canViewImage, canDownloadBuild, filterOutputs, liveNavigation, scopeKey, type LiveOutput, type ImageOutput, type OutputList, type OutputScope, type OutputVersions, type OutputKindFilter, type ReviewState } from "../outputsTypes.js";

type Selection = { owner: OutputsApi; projectId: string | null; scope: OutputScope; title: string; opener: HTMLButtonElement };
type OutputContext = { api: OutputsApi; locale: Locale; revision: string; open: (scope: OutputScope, title: string, opener: HTMLButtonElement) => void };
const Context = createContext<OutputContext | null>(null);

export function OutputsWorkspace({ api: transport, projectId, revision, locale, children, onOpenTask, taskTitles, agentNames }: {
  api: WorkApi; projectId: string | null; revision: string | null; locale: Locale; children: ReactNode; onOpenTask?: (taskId: string) => void; taskTitles?: ReadonlyMap<string, string>; agentNames?: ReadonlyMap<string, string>;
}): ReactElement {
  const api = useMemo(() => new OutputsApi(transport), [projectId]);
  api.setTransport(transport);
  const [selected, setSelected] = useState<Selection | null>(null);
  useEffect(() => () => api.dispose(), [api]);
  const visible = selected?.owner === api && selected.projectId === projectId ? selected : null;
  const context = useMemo<OutputContext>(() => ({ api, locale, revision: revision ?? "",
    open: (scope, title, opener) => setSelected({ owner: api, projectId, scope, title, opener }),
  }), [api, projectId, revision, locale]);
  function close(selection: Selection): void {
    setSelected((current) => current === selection ? null : current);
    // `preventScroll`: the gallery closes without moving the surface under it.
    if (selection.owner === api && selection.projectId === projectId && selection.opener.isConnected) selection.opener.focus({ preventScroll: true });
  }
  return <Context.Provider value={context}>{children}{visible ? <OutputsPanel key={scopeKey(visible.scope)} api={api} scope={visible.scope} title={visible.title} locale={locale} revision={revision ?? ""} onClose={() => close(visible)} onOpenTask={onOpenTask ? (taskId) => { close(visible); onOpenTask(taskId); } : undefined} taskTitles={taskTitles} agentNames={agentNames} /> : null}</Context.Provider>;
}

export function OutputsButton({ scope, title, eager = false, compact = false }: { scope: OutputScope; title: string; eager?: boolean; compact?: boolean }): ReactElement {
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
    if (!owner || !visible || compact) return;
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout> | undefined;
    const load = (): void => {
      void presentCurrent(owner.summary(scope, revision), controller.signal,
        (summary) => { setResult({ owner, key, revision, total: summary.total }); timer = setTimeout(load, 15_000); },
        () => { setResult(null); timer = setTimeout(load, 15_000); });
    };
    load(); return () => { controller.abort(); clearTimeout(timer); };
  }, [owner, key, revision, visible, compact]);
  const total = result?.owner === owner && result?.key === key && result?.revision === revision ? result.total : null;
  if (compact) return <span className="outputs-entry" ref={host}>{context ? <button className="icon-button" type="button" aria-label={`${outputText(context.locale, "results")} ${title}`} title={outputText(context.locale, "results")} onClick={(event) => context.open(scope, title, event.currentTarget)}><Icon name="results" /></button> : null}</span>;
  return <span className="outputs-entry" ref={host}>{context ? <OutputsAction count={total} locale={context.locale} title={title} onOpen={(button) => context.open(scope, title, button)} /> : null}</span>;
}

export function OutputsAction({ count, locale, title, onOpen }: { count: number | null; locale: Locale; title: string; onOpen: (button: HTMLButtonElement) => void }): ReactElement | null {
  if (count === null || count <= 0) return null;
  return <button type="button" className="button button-secondary button-inline" aria-label={`${outputText(locale, "titlePrefix")} ${title}`} onClick={(event) => onOpen(event.currentTarget)}>{outputText(locale, "results")} <span aria-hidden="true">({count})</span></button>;
}

/** This entry is independent of summary counts, so an empty or failed gallery stays reachable. */
export function AgentGalleryButton({ scope, title, displayTitle }: { scope: OutputScope; title: string; displayTitle?: string }): ReactElement {
  const context = useContext(Context);
  return <AgentGalleryAction title={title} displayTitle={displayTitle} locale={context?.locale ?? "en"} disabled={!context} onOpen={(button) => context?.open(scope, title, button)} />;
}
export function AgentGalleryAction({ title, displayTitle = title, locale, disabled = false, onOpen }: { title: string; displayTitle?: string; locale: Locale; disabled?: boolean; onOpen: (button: HTMLButtonElement) => void }): ReactElement {
  return <button type="button" className="board-role-gallery nodrag nowheel" aria-haspopup="dialog" aria-label={`${outputText(locale, "openGallery")} ${title}`} title={`${outputText(locale, "openGallery")} ${title}`} disabled={disabled} onClick={(event) => onOpen(event.currentTarget)}>
    <span className="board-role-avatar" aria-hidden="true">{Array.from(displayTitle.trim())[0] ?? "·"}</span><span className="board-role-name">{displayTitle}</span>
  </button>;
}

type ListState = { owner: OutputsApi; key: string; versions: OutputVersions; revision: string; kind: "ready"; value: OutputList } | { owner: OutputsApi; key: string; versions: OutputVersions; revision: string; kind: "error" };
type PreviewState = { owner: OutputsApi; key: string; versions: OutputVersions; outputId: string; contentRevision: string; kind: "loading" | "error" } | { owner: OutputsApi; key: string; versions: OutputVersions; outputId: string; contentRevision: string; kind: "ready"; url: string };
type AttributionProps = { onOpenTask?: (taskId: string) => void; taskTitles?: ReadonlyMap<string, string>; agentNames?: ReadonlyMap<string, string> };
export function OutputsPanel({ api, scope, title, locale, revision, onClose, onOpenTask, taskTitles, agentNames }: { api: OutputsApi; scope: OutputScope; title: string; locale: Locale; revision: string; onClose: () => void } & AttributionProps): ReactElement {
  const dialog = useRef<HTMLDialogElement>(null), heading = useId();
  const queue = useMemo(() => new OutputPreviewQueue(), [api]);
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, []);
  const [versions, setVersions] = useState<OutputVersions>("latest"), [attempt, setAttempt] = useState(0), [filter, setFilter] = useState<OutputKindFilter>("all");
  const [state, setState] = useState<ListState | null>(null), [preview, setPreview] = useState<PreviewState | null>(null);
  const previewRequest = useRef<AbortController | null>(null), objectUrl = useRef<string | null>(null);
  const key = scopeKey(scope), text = (name: Parameters<typeof outputText>[1]): string => outputText(locale, name);
  function releasePreview(): void {
    previewRequest.current?.abort(); previewRequest.current = null;
    if (objectUrl.current) URL.revokeObjectURL(objectUrl.current); objectUrl.current = null;
  }
  // Registered in the shared stack: a gallery opened from inside a task detail
  // answers Escape alone and leaves the detail open underneath.
  useEffect(() => {
    const node = dialog.current;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const registration = pushDialog();
    if (node !== null && !node.open) node.showModal();
    return () => { registration.release(); node?.close(); if (opener?.isConnected) opener.focus({ preventScroll: true }); };
  }, []);
  useEffect(() => {
    releasePreview(); setPreview(null); const controller = new AbortController();
    // Keep exact cards mounted during a same-scope refresh, so expanded
    // retained reports and the gallery selection survive ordinary polling.
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = (): void => { void presentCurrent(api.list(scope, versions, controller.signal), controller.signal,
      (value) => { setState({ owner: api, key, versions, revision, kind: "ready", value }); timer = setTimeout(load, 15_000); },
      () => { setState({ owner: api, key, versions, revision, kind: "error" }); timer = setTimeout(load, 15_000); }); };
    load();
    return () => { controller.abort(); clearTimeout(timer); releasePreview(); };
  }, [api, key, versions, revision, attempt]);
  useEffect(() => { releasePreview(); setPreview(null); }, [filter]);
  async function openImage(item: ImageOutput): Promise<void> {
    releasePreview(); const controller = new AbortController(); previewRequest.current = controller;
    setPreview({ owner: api, key, versions, outputId: item.outputId, contentRevision: item.contentRevision, kind: "loading" });
    await presentCurrent(queue.run(() => api.preview(scope, item, versions, controller.signal), controller.signal), controller.signal, (blob) => {
      const url = URL.createObjectURL(blob); objectUrl.current = url;
      setPreview({ owner: api, key, versions, outputId: item.outputId, contentRevision: item.contentRevision, kind: "ready", url });
    }, () => setPreview({ owner: api, key, versions, outputId: item.outputId, contentRevision: item.contentRevision, kind: "error" }));
  }
  const current = state?.owner === api && state.key === key && state.versions === versions ? state : null;
  const items = current?.kind === "ready" ? filterOutputs(current.value.items, filter) : [];
  const currentPreview = preview?.owner === api && preview.key === key && preview.versions === versions && items.some((item) => item.kind !== "live_preview" && item.outputId === preview.outputId && item.contentRevision === preview.contentRevision && canViewImage(item)) ? preview : null;
  const attribution = { onOpenTask, taskTitles, agentNames };
  function dismiss(): void { dialog.current?.close(); onClose(); }
  return <dialog className="outputs-dialog" ref={dialog} aria-labelledby={heading} onCancel={(event) => { event.preventDefault(); event.stopPropagation(); dismiss(); }} onKeyDown={(event) => { if (event.key === "Escape") event.stopPropagation(); }}>
    <header className="outputs-heading"><div><p className="small-copy">{text("gallery")}</p><h2 id={heading}>{text("titlePrefix")} {title}</h2></div><button autoFocus type="button" className="icon-button outputs-close" aria-label={text("close")} title={text("close")} onClick={dismiss}><Icon name="close" size={18} /></button></header>
    <div className="outputs-toolbar"><div role="group" aria-label={text("versionFilter")}>
      <button type="button" className="button button-secondary button-inline" aria-pressed={versions === "latest"} onClick={() => setVersions("latest")}>{text("latest")}</button>
      <button type="button" className="button button-secondary button-inline" aria-pressed={versions === "all"} onClick={() => setVersions("all")}>{text("history")}</button>
    </div><div role="group" aria-label={text("kindFilter")}>
      {(["all", "images", "builds", "reports", "live"] as const).map((kind) => <button key={kind} type="button" className="button button-secondary button-inline" aria-pressed={filter === kind} onClick={() => setFilter(kind)}>{text(kind === "all" ? "allKinds" : kind === "images" ? "images" : kind === "builds" ? "builds" : kind === "reports" ? "reports" : "liveKinds")}</button>)}
    </div><button type="button" className="button button-secondary button-inline" onClick={() => setAttempt((value) => value + 1)}>{text(current?.kind === "error" ? "retry" : "refresh")}</button></div>
    {current === null ? <p role="status">{text("loading")}</p> : current.kind === "error" ? <p role="alert">{text("failed")}</p> : items.length === 0 ? <p role="status">{text(current.value.items.length === 0 ? "galleryEmpty" : "filterEmpty")}</p> : <ul className="outputs-list">{items.map((item) => item.kind === "live_preview" ? <LivePreviewCard key={item.outputId} item={item} locale={locale} now={now} uiOrigin={window.location.origin} {...attribution} /> : <li key={item.outputId} data-expanded={currentPreview?.outputId === item.outputId && currentPreview.kind === "ready"}>
      {(item.kind === "artifact_image" || item.kind === "design_image") && <ImageThumbnail key={`${item.outputId}:${item.contentRevision}:${item.state}:${item.historicalPreviewVerified}:${revision}:${attempt}`} api={api} queue={queue} scope={scope} versions={versions} item={item} locale={locale} root={dialog.current} />}
      <p className="small-copy">{text(item.kind === "design_image" ? "designImage" : item.kind === "static_build" ? "staticBuild" : item.kind === "text_result" ? "textReport" : "generatedImage")}</p><h3>{item.title}</h3><p className="outputs-status" data-status={outputStatus(item, now)}>{text(outputStatus(item, now))}</p>
      <p className="small-copy">{text(item.retained ? "retainedBytes" : "sourceBytes")}</p>
      <p className="outputs-review" data-result-review={item.resultReviewState ?? "none"}>{text(item.resultReviewState === "approved" ? "resultApproved" : item.resultReviewState === "returned" ? "resultReturned" : item.resultReviewState === "awaiting" ? "resultAwaiting" : "resultUnreviewed")}</p>
      <ReviewStateLine state={item.reviewState} locale={locale} />
      <p className="small-copy">{text(item.latest ? "current" : "previous")} · {item.versionCount} {text("versions")}</p>
      <OutputAttribution item={item} locale={locale} {...attribution} />
      {item.kind === "text_result" ? <TextReportCard api={api} scope={scope} item={item} locale={locale} /> : item.kind === "static_build" ? <BuildDownloadAction api={api} scope={scope} item={item} locale={locale} /> : <ImagePreviewAction item={item} locale={locale} onOpen={() => void openImage(item)} />}
      {currentPreview?.outputId !== item.outputId ? null : currentPreview.kind === "loading" ? <p role="status">{text("imageLoading")}</p> : currentPreview.kind === "error" ? <p role="alert">{text("imageFailed")}</p> : currentPreview.kind === "ready" ? <img className="outputs-image" src={currentPreview.url} alt={item.title} width={item.width ?? undefined} height={item.height ?? undefined} /> : null}
    </li>)}</ul>}
  </dialog>;
}

/** Only verified image bytes become blob URLs; leaving the visible area cancels and releases them. */
export function ImageThumbnail({ api, queue, scope, versions, item, locale, root }: { api: OutputsApi; queue: OutputPreviewQueue; scope: OutputScope; versions: OutputVersions; item: ImageOutput; locale: Locale; root: HTMLDialogElement | null }): ReactElement {
  const host = useRef<HTMLDivElement>(null), [visible, setVisible] = useState(false);
  const [state, setState] = useState<{ kind: "ready"; url: string } | { kind: "loading" | "error" } | null>(null);
  useEffect(() => {
    if (!canViewImage(item)) return;
    if (typeof IntersectionObserver === "undefined") { setVisible(true); return; }
    const observer = new IntersectionObserver((entries) => setVisible(entries.some((entry) => entry.isIntersecting)), { root, rootMargin: "120px" });
    if (host.current) observer.observe(host.current);
    return () => observer.disconnect();
  }, [root]);
  const identity = `${scopeKey(scope)}:${item.outputId}:${item.contentRevision}:${item.state}:${item.historicalPreviewVerified}`;
  useEffect(() => {
    if (!visible || !canViewImage(item)) return;
    const controller = new AbortController(); let url: string | null = null;
    setState({ kind: "loading" });
    void presentCurrent(queue.run(() => api.preview(scope, item, versions, controller.signal), controller.signal), controller.signal, (blob) => {
      url = URL.createObjectURL(blob); setState({ kind: "ready", url });
    }, () => setState({ kind: "error" }));
    return () => { controller.abort(); if (url) URL.revokeObjectURL(url); setState(null); };
  }, [api, queue, identity, versions, visible]);
  return <div className="outputs-thumbnail" ref={host}>
    {visible && state?.kind === "ready" ? <img src={state.url} alt={item.title} loading="lazy" decoding="async" width={item.width ?? undefined} height={item.height ?? undefined} /> : <p className="small-copy">{outputText(locale, !canViewImage(item) ? "noThumbnail" : visible && state?.kind === "error" ? "imageFailed" : "imageLoading")}</p>}
  </div>;
}

export function OutputAttribution({ item, locale, onOpenTask, taskTitles, agentNames }: { item: (ImageOutput | LiveOutput); locale: Locale } & AttributionProps): ReactElement {
  const text = (key: Parameters<typeof outputText>[1]): string => outputText(locale, key);
  const taskTitle = taskTitles?.get(item.taskId) ?? `${text("task")} · ${item.taskId.slice(-8)}`;
  const agentName = item.producerAgentId ? agentNames?.get(item.producerAgentId) ?? `${text("agent")} · ${item.producerAgentId.slice(-8)}` : text("producerUnknown");
  const fields: [string, string | null][] = [["task_id", item.taskId], ["task_revision", item.taskRevision], ["producer_agent_id", item.producerAgentId], ["producer_session_id", item.producerSessionId], ["producer_delegation_id", item.producerDelegationId], ["delegation_revision", item.delegationRevision]];
  if (item.kind !== "live_preview") fields.push(["artifact_id", item.artifactId], ["artifact_revision", item.artifactRevision], ["content_revision", item.contentRevision], ["package_id", item.packageId], ["package_revision", item.packageRevision], ["screen_id", item.screenId], ["recorded_at", item.recordedAt]);
  return <div className="outputs-attribution"><p className="small-copy">{text("task")}: {onOpenTask ? <button type="button" className="notice-link" onClick={() => onOpenTask(item.taskId)}>{taskTitle}</button> : taskTitle}<br />{text("agent")}: {agentName}</p>
    <details><summary>{text("technicalDetails")}</summary>{item.kind !== "live_preview" ? <div><p className="small-copy">{text("reviewCommandContext")}</p><p><code>{`uv run agent-commons review request --target-ref artifact:${item.artifactId} --target-revision ${item.artifactRevision} --criterion "Check this exact result" --independent --idempotency-key result-review-${item.artifactId}-${item.artifactRevision}`}</code></p></div> : null}<dl>{fields.filter(([, value]) => value !== null).map(([field, value]) => <div key={field}><dt><code>{field}</code></dt><dd><code>{value}</code></dd></div>)}</dl></details>
  </div>;
}

/** A review label is rendered only when the server actually reported one. */
export function ReviewStateLine({ state, locale }: { state: ReviewState | null; locale: Locale }): ReactElement | null {
  const label = reviewLabel(state);
  return label === null ? null : <p className="outputs-review" data-review={state}>{outputText(locale, label)}</p>;
}

export function LivePreviewCard({ item, locale, now, uiOrigin, ...attribution }: { item: LiveOutput; locale: Locale; now: number; uiOrigin: string } & AttributionProps): ReactElement {
  const text = (key: Parameters<typeof outputText>[1]): string => outputText(locale, key);
  const href = liveNavigation(item, now, uiOrigin);
  const status = outputStatus(item, now);
  const date = (value: number): string => new Intl.DateTimeFormat(locale === "ru" ? "ru-RU" : "en-US", { dateStyle: "short", timeStyle: "medium" }).format(new Date(value));
  return <li><div className="outputs-live-cover" aria-hidden="true">↗</div><p className="small-copy">{text("live")}</p><h3>{item.title}</h3>
    <p className="outputs-status" data-status={status}>{text(status)}</p><p className="small-copy">{text(item.latest ? "current" : "previous")} · {item.versionCount} {text("versions")}</p><p>{text("reachability")}</p>
    <OutputAttribution item={item} locale={locale} {...attribution} /><details className="outputs-address"><summary>{text("address")}</summary><code>{item.origin}</code></details><p className="small-copy">{text("published")}: {date(item.publishedAt)} · {text("expires")}: {date(item.expiresAt)}</p>
    {href ? <a className="button button-secondary button-inline" href={href} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer" onClick={(event) => { if (!liveNavigation(item, Date.now(), window.location.origin)) event.preventDefault(); }}>{text("openLive")}</a> : null}
  </li>;
}

export function ImagePreviewAction({ item, locale, onOpen }: { item: ImageOutput; locale: Locale; onOpen: () => void }): ReactElement {
  const text = (key: Parameters<typeof outputText>[1]): string => outputText(locale, key);
  return <>{item.historicalPreviewVerified ? <p>{text("historicalHelp")}</p> : item.state !== "ready" ? <p>{text(item.state === "unavailable" ? "unavailableHelp" : item.state === "stale" ? "earlierHelp" : "uncheckedHelp")}</p> : null}
    {canViewImage(item) ? <button type="button" className="button button-secondary button-inline" onClick={onOpen}>{text(item.historicalPreviewVerified ? "viewHistorical" : "view")}</button> : null}</>;
}

export function BuildDownloadAction({ api, scope, item, locale }: { api: OutputsApi; scope: OutputScope; item: ImageOutput; locale: Locale }): ReactElement {
  const [state, setState] = useState<"idle" | "loading" | "error">("idle");
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), [api, item.outputId]);
  async function download(): Promise<void> {
    controller.current?.abort(); const active = new AbortController(); controller.current = active;
    setState("loading");
    try {
      const blob = await api.build(scope, item, active.signal);
      if (active.signal.aborted) return;
      const url = URL.createObjectURL(blob), link = document.createElement("a");
      link.href = url; link.download = "commons-static-build.zip"; link.rel = "noopener"; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000); setState("idle");
    } catch { if (!active.signal.aborted) setState("error"); }
  }
  return <div><button type="button" className="button button-secondary button-inline" disabled={!canDownloadBuild(item) || state === "loading"} onClick={() => void download()}>{outputText(locale, "downloadBuild")}</button><p className="small-copy">{outputText(locale, "openLocally")}</p>{state === "error" ? <p role="alert">{outputText(locale, "imageFailed")}</p> : null}</div>;
}

/** Report prose is rendered as text; the request binds the exact card and scope. */
export function TextReportCard({ api, scope, item, locale }: { api: OutputsApi; scope: OutputScope; item: ImageOutput; locale: Locale }): ReactElement {
  const [state, setState] = useState<{ kind: "loading" | "error" } | { kind: "ready"; value: TextResultContent } | null>(null);
  const request = useRef<AbortController | null>(null);
  useEffect(() => { setState(null); return () => request.current?.abort(); }, [api, scopeKey(scope), item.outputId, item.contentRevision]);
  const t = (key: Parameters<typeof outputText>[1]): string => outputText(locale, key);
  async function read(): Promise<void> {
    request.current?.abort(); const controller = new AbortController(); request.current = controller;
    setState({ kind: "loading" });
    await presentCurrent(api.text(scope, item, controller.signal), controller.signal,
      (value) => setState({ kind: "ready", value }), () => setState({ kind: "error" }));
  }
  return <div className="outputs-report"><p className="inspector-prose">{item.summary}</p>
    {item.checks?.length ? <><h4>{t("checks")}</h4><ul>{item.checks.map((check, index) => <li key={index}>{check}</li>)}</ul></> : null}
    {item.historicalPreviewVerified ? <p className="small-copy">{t("historicalHelp")}</p> : null}
    {canReadReport(item) ? <button className="button button-secondary" type="button" disabled={state?.kind === "loading"} onClick={() => void read()}>{t(state?.kind === "error" ? "retry" : "readReport")}</button> : null}
    {state?.kind === "loading" ? <p role="status">{t("loading")}</p> : state?.kind === "error" ? <p role="alert">{t("failed")}</p> : state?.kind === "ready" && canReadReport(item) ? <pre className="outputs-report-text">{state.value.content}</pre> : null}
  </div>;
}
