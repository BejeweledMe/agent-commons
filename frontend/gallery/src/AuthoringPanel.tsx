import { type FormEvent, type ReactElement, useEffect, useRef, useState } from "react";

import {
  AbortSlot,
  type AuthoringCandidate,
  type AuthoringResponse,
  type AuthoringSavedState,
  type GalleryPackage,
  type AuthoringIntent,
  authoringSavedCopyKeys,
  authoringSavedState,
  authoringTitleIsValid,
  parseAuthoringResponse,
  parseAuthoringResult,
  readBoundedJson,
  retainAuthoringIntent,
  settleAuthoringRefresh,
} from "./contracts";

type AuthoringText = (key: string) => string;
type Props = {
  apiBase: string;
  packages: GalleryPackage[];
  text: AuthoringText;
  onPublished: () => Promise<void>;
  announce: (value: string) => void;
};
type State =
  | { kind: "loading"; previous: AuthoringResponse | null }
  | { kind: "ready"; response: AuthoringResponse }
  | { kind: "error"; code: string; previous: AuthoringResponse | null };
type SubmitState =
  | { kind: "idle" }
  | { kind: "submitting" }
  | AuthoringSavedState
  | { kind: "stale"; code: string }
  | { kind: "error"; code: string };

const ERROR_CODE = /^[a-z][a-z0-9_]{0,79}$/;
const WRITE_CODES = new Set([
  "design_package_missing",
  "design_package_stale",
  "design_package_unauthorized",
  "design_package_unsafe",
  "design_package_oversized",
  "design_package_invalid",
  "design_package_unavailable",
  "invalid_request",
]);

function responseErrorCode(value: unknown): string | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  const error = (value as Record<string, unknown>).error;
  if (typeof error !== "object" || error === null || Array.isArray(error)) return null;
  const code = (error as Record<string, unknown>).code;
  return typeof code === "string" && ERROR_CODE.test(code) && WRITE_CODES.has(code) ? code : null;
}

export function AuthoringPanel({ apiBase, packages, text, onPublished, announce }: Props): ReactElement {
  const [state, setState] = useState<State>({ kind: "loading", previous: null });
  const [submit, setSubmit] = useState<SubmitState>({ kind: "idle" });
  const [packageTitle, setPackageTitle] = useState("");
  const [targetPackageId, setTargetPackageId] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [screenTitles, setScreenTitles] = useState<Record<string, string>>({});
  const loadRequests = useRef(new AbortSlot());
  const submitRequests = useRef(new AbortSlot());
  const intent = useRef<AuthoringIntent | null>(null);

  async function load(propagateFailure = false): Promise<void> {
    if (!apiBase) return;
    const controller = loadRequests.current.begin();
    setState((current) => ({
      kind: "loading",
      previous: current.kind === "ready" ? current.response : current.previous,
    }));
    try {
      const response = await fetch(`${apiBase}/gallery/authoring`, {
        credentials: "same-origin",
        signal: controller.signal,
      });
      if (response.status === 401) throw new Error("unauthorized");
      const parsed = parseAuthoringResponse(await readBoundedJson(response, 524_288));
      if (!response.ok) throw new Error(parsed.error?.code ?? "gallery_authoring_unavailable");
      if (!loadRequests.current.isCurrent(controller)) {
        if (propagateFailure) throw new Error("gallery_authoring_refresh_superseded");
        return;
      }
      setState({ kind: "ready", response: parsed });
    } catch (error: unknown) {
      if (error instanceof DOMException && error.name === "AbortError") {
        if (propagateFailure) throw error;
        return;
      }
      if (!loadRequests.current.isCurrent(controller)) {
        if (propagateFailure) throw new Error("gallery_authoring_refresh_superseded");
        return;
      }
      const failure = error instanceof Error ? error : new Error("gallery_authoring_unavailable");
      setState((current) => ({
        kind: "error",
        code: failure.message,
        previous: current.kind === "ready" ? current.response : current.previous,
      }));
      if (propagateFailure) throw failure;
    } finally {
      loadRequests.current.finish(controller);
    }
  }

  useEffect(() => {
    void load(false);
    return () => {
      loadRequests.current.abort();
      submitRequests.current.abort();
    };
  }, [apiBase]);

  function toggle(candidate: AuthoringCandidate): void {
    setSelected((current) => {
      if (current.includes(candidate.candidate_id)) return current.filter((id) => id !== candidate.candidate_id);
      if (current.length >= 64) return current;
      setScreenTitles((titles) => ({ ...titles, [candidate.candidate_id]: titles[candidate.candidate_id] ?? candidate.producer_task_title }));
      return [...current, candidate.candidate_id];
    });
    setSubmit({ kind: "idle" });
  }

  function move(candidateId: string, delta: -1 | 1): void {
    setSelected((current) => {
      const index = current.indexOf(candidateId);
      const destination = index + delta;
      if (index < 0 || destination < 0 || destination >= current.length) return current;
      const next = [...current];
      [next[index], next[destination]] = [next[destination], next[index]];
      return next;
    });
    setSubmit({ kind: "idle" });
  }

  function chooseTarget(value: string): void {
    setTargetPackageId(value);
    const current = packages.find((item) => item.design_package_id === value);
    if (current !== undefined) {
      setPackageTitle(current.title);
      const currentResponse = state.kind === "ready" ? state.response : state.previous;
      const candidates = currentResponse?.candidates ?? [];
      const exact = current.screens.map((screen) => candidates.find((candidate) =>
        candidate.artifact_id === screen.artifact_id &&
        candidate.artifact_revision === screen.artifact_revision &&
        candidate.artifact_content_revision === screen.artifact_content_revision &&
        candidate.producer_task_id === screen.producer_task_id &&
        candidate.producer_task_revision === screen.producer_task_revision));
      if (exact.every((candidate) => candidate !== undefined)) {
        const ids = exact.map((candidate) => candidate.candidate_id);
        setSelected(ids);
        setScreenTitles(Object.fromEntries(ids.map((candidateId, index) => [candidateId, current.screens[index].title])));
      } else {
        setSelected([]);
        setScreenTitles({});
        setSubmit({ kind: "stale", code: "design_package_stale" });
        return;
      }
    } else {
      setPackageTitle("");
      setSelected([]);
      setScreenTitles({});
    }
    setSubmit({ kind: "idle" });
  }

  async function submitPackage(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const authoring = state.kind === "ready" ? state.response : state.previous;
    if (authoring === null || authoring.state !== "ready" || !authoringTitleIsValid(packageTitle) || selected.length < 1) {
      setSubmit({ kind: "error", code: "design_package_invalid" });
      return;
    }
    const byId = new Map(authoring.candidates.map((candidate) => [candidate.candidate_id, candidate]));
    const screens = selected.map((candidateId) => ({
      candidate_id: candidateId,
      title: (screenTitles[candidateId] ?? byId.get(candidateId)?.producer_task_title ?? "").trim(),
    }));
    if (screens.some((screen) => !authoringTitleIsValid(screen.title))) {
      setSubmit({ kind: "error", code: "design_package_invalid" });
      return;
    }
    const target = packages.find((item) => item.design_package_id === targetPackageId);
    if (targetPackageId && target === undefined) {
      setSubmit({ kind: "stale", code: "design_package_stale" });
      return;
    }
    const fingerprint = JSON.stringify([target?.revision ?? "new", packageTitle.trim(), screens]);
    intent.current = retainAuthoringIntent(
      intent.current,
      fingerprint,
      `gallery-authoring-${crypto.randomUUID()}`,
    );
    const body = {
      title: packageTitle.trim(),
      screens,
      ...(target === undefined ? {} : { expected_revision: target.revision }),
      idempotency_key: intent.current.key,
    };
    const path = target === undefined
      ? `${apiBase}/gallery/packages`
      : `${apiBase}/gallery/${encodeURIComponent(target.design_package_id)}/revisions`;
    const controller = submitRequests.current.begin();
    setSubmit({ kind: "submitting" });
    announce(text("authoring_submitting"));
    try {
      const response = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        signal: controller.signal,
        body: JSON.stringify(body),
      });
      const raw = await readBoundedJson(response, 131_072).catch(() => null);
      if (!submitRequests.current.isCurrent(controller)) return;
      if (!response.ok) {
        const code = responseErrorCode(raw) ?? "gallery_authoring_failed";
        setSubmit(code === "design_package_stale" ? { kind: "stale", code } : { kind: "error", code });
        announce(text(code === "design_package_stale" ? "authoring_stale" : "authoring_failed"));
        return;
      }
      const result = parseAuthoringResult(raw);
      setSubmit(authoringSavedState(result, true));
      announce(text(result.state === "published" ? "authoring_published" : "authoring_revised"));
      const settled = await settleAuthoringRefresh(
        result,
        onPublished,
        () => load(true),
      );
      if (!submitRequests.current.isCurrent(controller)) return;
      setSubmit(settled);
      if (settled.kind === "saved_refresh_failed") {
        announce(text(settled.code === "candidate_refresh_failed"
          ? "authoring_saved_candidates_refresh_failed"
          : "authoring_saved_gallery_refresh_failed"));
      }
    } catch (error: unknown) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      if (!submitRequests.current.isCurrent(controller)) return;
      setSubmit({ kind: "error", code: "gallery_authoring_failed" });
      announce(text("authoring_failed"));
    } finally {
      submitRequests.current.finish(controller);
    }
  }

  const currentResponse = state.kind === "ready" ? state.response : state.previous;
  const candidates = currentResponse?.candidates ?? [];
  const byId = new Map(candidates.map((candidate) => [candidate.candidate_id, candidate]));
  const disabled = currentResponse === null || currentResponse.state === "unavailable";
  const savedStatus = submit.kind === "success" || submit.kind === "saved_refresh_failed" ? submit : null;
  const savedCopy = savedStatus === null ? null : authoringSavedCopyKeys(savedStatus);
  return (
    <section aria-labelledby="authoring-title" className="authoring-panel">
      <header><div><p className="gallery-eyebrow">{text("authoring_eyebrow")}</p><h2 id="authoring-title">{text("authoring_title")}</h2></div><button className="icon-button" onClick={() => void load()} type="button">{text("authoring_refresh")}</button></header>
      {state.kind === "loading" ? <p aria-live="polite" role="status">{text("authoring_loading")}</p> : null}
      {state.kind === "error" ? <p aria-live="polite" role="alert">{text("authoring_candidates_refresh_failed")} <code>{state.code}</code></p> : null}
      {currentResponse?.state === "unavailable" ? <div role="status"><p>{text("authoring_unavailable")}</p><code>{currentResponse.error?.code}</code></div> : null}
      {currentResponse?.state === "empty" ? <p role="status">{text("authoring_empty")}</p> : null}
      {savedStatus !== null && savedCopy !== null ? (
        <div aria-live="polite" className={`authoring-status authoring-status-${savedStatus.kind}`} data-saved-revision={savedStatus.revision}>
          <p role={savedStatus.kind === "saved_refresh_failed" ? "alert" : "status"}>
            {text(savedCopy.summary)} <code>{savedStatus.revision}</code>
            {savedStatus.kind === "saved_refresh_failed" ? <> <code>{savedStatus.code}</code></> : null}
          </p>
          {savedCopy.help === null ? null : <p>{text(savedCopy.help)}</p>}
        </div>
      ) : null}
      {currentResponse?.state === "ready" ? (
        <form className="authoring-form" onSubmit={(event) => void submitPackage(event)}>
          <label htmlFor="authoring-target">{text("authoring_target")}</label>
          <select id="authoring-target" onChange={(event) => chooseTarget(event.target.value)} value={targetPackageId}>
            <option value="">{text("authoring_new_package")}</option>
            {packages.map((item) => <option key={item.design_package_id} value={item.design_package_id}>{item.title}</option>)}
          </select>
          <label htmlFor="authoring-package-title">{text("authoring_package_title")}</label>
          <input id="authoring-package-title" maxLength={256} onChange={(event) => { setPackageTitle(event.target.value); setSubmit({ kind: "idle" }); }} required type="text" value={packageTitle} />
          <fieldset><legend>{text("authoring_candidates")}</legend><p>{text("authoring_candidates_help")}</p>
            <ul className="candidate-list">{candidates.map((candidate) => (
              <li key={candidate.candidate_id}><label><input checked={selected.includes(candidate.candidate_id)} onChange={() => toggle(candidate)} type="checkbox" /> <span><strong>{candidate.producer_task_title}</strong><code>{candidate.artifact_id}</code><small>{candidate.media_type} · {candidate.width} × {candidate.height} · {candidate.classification}</small></span></label></li>
            ))}</ul>
          </fieldset>
          {selected.length > 0 ? <fieldset><legend>{text("authoring_order")}</legend><ol className="selected-screen-list">{selected.map((candidateId, index) => {
            const candidate = byId.get(candidateId);
            if (candidate === undefined) return null;
            return <li key={candidateId}><span aria-label={`${text("screen_order")} ${index + 1}`}>{index + 1}</span><label><span>{text("authoring_screen_title")}</span><input maxLength={256} onChange={(event) => { setScreenTitles((current) => ({ ...current, [candidateId]: event.target.value })); setSubmit({ kind: "idle" }); }} required type="text" value={screenTitles[candidateId] ?? candidate.producer_task_title} /></label><div><button aria-label={`${text("authoring_move_up")} ${index + 1}`} disabled={index === 0} onClick={() => move(candidateId, -1)} type="button">↑</button><button aria-label={`${text("authoring_move_down")} ${index + 1}`} disabled={index === selected.length - 1} onClick={() => move(candidateId, 1)} type="button">↓</button></div></li>;
          })}</ol></fieldset> : null}
          <button className="authoring-submit" disabled={disabled || submit.kind === "submitting" || selected.length < 1 || !authoringTitleIsValid(packageTitle)} type="submit">{submit.kind === "submitting" ? text("authoring_submitting") : targetPackageId ? text("authoring_revise") : text("authoring_publish")}</button>
          {submit.kind === "stale" || submit.kind === "error" ? (
            <div aria-live="polite" className={`authoring-status authoring-status-${submit.kind}`}>
              {submit.kind === "stale" ? <p role="alert">{text("authoring_stale_help")} <code>{submit.code}</code></p> : null}
              {submit.kind === "error" ? <p role="alert">{text("authoring_failed_help")} <code>{submit.code}</code></p> : null}
            </div>
          ) : null}
        </form>
      ) : null}
    </section>
  );
}
