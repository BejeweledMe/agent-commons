import { type FormEvent, type ReactElement, useEffect, useRef, useState } from "react";

import { ApiProblem, type WorkApi } from "../api.js";
import { ContextPackRetryIdentity } from "../contextPackEditorState.js";
import { ContextPackDraftValidationError, validateContextPackDraft } from "../contextPackDraftValidation.js";
import {
  type ContextPackForm, type ContextSaveOperation, draftFromForm, emptyContextForm,
  formFromDraft, parseAdvancedDraft, rowKey, snapshotSaveOperation
} from "../contextPackFormState.js";
import type { ContextPackCatalog, ContextPackDetail, ContextSourceCatalog } from "../contracts";
import type { MessageKey } from "../i18n";
import { ContextSourcePicker } from "./ContextSourcePicker.js";

type Props = { api: WorkApi; text: (key: MessageKey) => string; writesEnabled: boolean };
type LoadState =
  | { kind: "loading" }
  | { kind: "ready"; catalog: ContextPackCatalog }
  | { kind: "error"; code: string };
type SourceState =
  | { kind: "loading" }
  | { kind: "ready"; catalog: ContextSourceCatalog }
  | { kind: "error"; code: string };
type SaveState =
  | { kind: "idle" | "saving" | "stale" }
  | { kind: "success"; revision: string }
  | { kind: "validation"; code: MessageKey }
  | { kind: "error"; code: string; uncertain: boolean };

function problemCode(error: unknown): string {
  return error instanceof ApiProblem ? error.apiError?.code ?? "request_unavailable" : "request_unavailable";
}

export function ContextPackNewButton({ locked, text, onNew }: {
  locked: boolean;
  text: (key: MessageKey) => string;
  onNew: () => void;
}): ReactElement {
  return <button className="button button-secondary" disabled={locked} onClick={() => {
    if (!locked) onNew();
  }} type="button">{text("context_packs_new")}</button>;
}

export function ContextPacksSection({ api, text, writesEnabled }: Props): ReactElement {
  const [load, setLoad] = useState<LoadState>({ kind: "loading" });
  const [sources, setSources] = useState<SourceState>({ kind: "loading" });
  const [save, setSave] = useState<SaveState>({ kind: "idle" });
  const [editor, setEditor] = useState<ContextPackForm>(emptyContextForm);
  const [identity, setIdentity] = useState<{ id: string; revision: string } | null>(null);
  const [mode, setMode] = useState<"structured" | "advanced">("structured");
  const [advanced, setAdvanced] = useState("");
  const [reload, setReload] = useState(0);
  const [sourceReload, setSourceReload] = useState(0);
  const [reading, setReading] = useState(false);
  const retryIdentity = useRef(new ContextPackRetryIdentity());
  const pending = useRef<ContextSaveOperation | null>(null);
  const detailRequest = useRef<AbortController | null>(null);
  const detailGeneration = useRef(0);
  const mounted = useRef(true);
  const uncertain = save.kind === "error" && save.uncertain;
  const locked = !writesEnabled || save.kind === "saving" || reading || uncertain;
  const sourceCatalog = sources.kind === "ready" ? sources.catalog : null;

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; detailRequest.current?.abort(); };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setLoad({ kind: "loading" });
    api.loadContextPacks(controller.signal).then(
      (catalog) => { if (!controller.signal.aborted) setLoad({ kind: "ready", catalog }); },
      (error: unknown) => { if (!controller.signal.aborted) setLoad({ kind: "error", code: problemCode(error) }); }
    );
    return () => controller.abort();
  }, [api, reload]);

  useEffect(() => {
    const controller = new AbortController();
    setSources({ kind: "loading" });
    api.loadContextSources(controller.signal).then(
      (catalog) => { if (!controller.signal.aborted) setSources({ kind: "ready", catalog }); },
      (error: unknown) => { if (!controller.signal.aborted) setSources({ kind: "error", code: problemCode(error) }); }
    );
    return () => controller.abort();
  }, [api, sourceReload]);

  const showDetail = (detail: ContextPackDetail): void => {
    setIdentity({ id: detail.contextPackId, revision: detail.revision });
    const form = formFromDraft(detail);
    setEditor(form);
    setAdvanced(JSON.stringify(draftFromForm(form, false), null, 2));
  };

  const newPack = (): void => {
    // The ref also guards a click queued before the saving render committed.
    if (pending.current !== null) return;
    detailRequest.current?.abort();
    detailGeneration.current += 1;
    setReading(false);
    retryIdentity.current.reset();
    pending.current = null;
    setEditor(emptyContextForm());
    setIdentity(null);
    setMode("structured");
    setAdvanced("");
    setSave({ kind: "idle" });
  };

  const edit = (contextPackId: string): void => {
    detailRequest.current?.abort();
    const controller = new AbortController();
    const generation = ++detailGeneration.current;
    detailRequest.current = controller;
    setReading(true);
    api.loadContextPack(contextPackId, controller.signal).then(
      (detail) => {
        if (controller.signal.aborted || generation !== detailGeneration.current) return;
        retryIdentity.current.reset();
        pending.current = null;
        showDetail(detail);
        setSave({ kind: "idle" });
        setReading(false);
      },
      (error: unknown) => {
        if (controller.signal.aborted || generation !== detailGeneration.current) return;
        setReading(false);
        setSave({ kind: "error", code: problemCode(error), uncertain: false });
      }
    );
  };

  const validationError = (error: unknown): void => setSave({
    kind: "validation",
    code: error instanceof ContextPackDraftValidationError ? error.code : "context_packs_validation_format"
  });

  const switchMode = (next: "structured" | "advanced"): void => {
    if (next === mode) return;
    try {
      if (next === "advanced") setAdvanced(JSON.stringify(draftFromForm(editor, false), null, 2));
      else setEditor(formFromDraft(parseAdvancedDraft(advanced, false)));
      setMode(next);
      setSave({ kind: "idle" });
    } catch (error: unknown) { validationError(error); }
  };

  const performSave = (operation: ContextSaveOperation): void => {
    pending.current = operation;
    setSave({ kind: "saving" });
    const signal = new AbortController().signal;
    const request = operation.contextPackId === null || operation.expectedRevision === null
      ? api.publishContextPack(operation.draft, operation.idempotencyKey, signal)
      : api.reviseContextPack(operation.contextPackId, operation.expectedRevision, operation.draft, operation.idempotencyKey, signal);
    request.then(
      (detail) => {
        pending.current = null;
        retryIdentity.current.reset();
        if (!mounted.current) return;
        showDetail(detail);
        setSave({ kind: "success", revision: detail.revision });
        setReload((value) => value + 1);
      },
      (error: unknown) => {
        if (!mounted.current) return;
        const code = problemCode(error);
        const unknownOutcome = !(error instanceof ApiProblem && error.status >= 400 && error.status < 500);
        if (!unknownOutcome) pending.current = null;
        setSave(code === "context_pack_stale"
          ? { kind: "stale" }
          : { kind: "error", code, uncertain: unknownOutcome });
      }
    );
  };

  const submit = (event: FormEvent): void => {
    event.preventDefault();
    if (locked) return;
    try {
      const draft = validateContextPackDraft(mode === "advanced" ? parseAdvancedDraft(advanced) : draftFromForm(editor));
      const signature = JSON.stringify({ draft, id: identity?.id ?? null, revision: identity?.revision ?? null });
      performSave(snapshotSaveOperation({
        draft, contextPackId: identity?.id ?? null, expectedRevision: identity?.revision ?? null,
        idempotencyKey: retryIdentity.current.forOperation(signature)
      }));
    } catch (error: unknown) { validationError(error); }
  };

  return (
    <section aria-labelledby="context-packs-title" className="context-packs-section">
      <header className="context-packs-header">
        <h2 id="context-packs-title">{text("context_packs_title")}</h2>
        <ContextPackNewButton locked={save.kind === "saving" || uncertain} onNew={newPack} text={text} />
      </header>
      <p className="lead-copy">{text("context_packs_intro")}</p>
      <details><summary>{text("library_technical_details")}</summary><p className="small-copy">{text("context_packs_context_notice")}</p></details>
      {load.kind === "loading" || reading ? <p role="status">{text("context_packs_loading")}</p> : null}
      {load.kind === "error" ? (
        <div className="notice" role="status">
          <p>{text("context_packs_load_error")} <code>{load.code}</code></p>
          <button className="button button-secondary" onClick={() => setReload((value) => value + 1)} type="button">{text("context_packs_retry")}</button>
        </div>
      ) : null}
      {load.kind === "ready" ? (
        <>
          {load.catalog.truncated ? <p className="notice notice-warning" role="status">{text("context_packs_truncated")}</p> : null}
          {load.catalog.packs.length === 0 ? <p className="small-copy">{text("context_packs_empty")}</p> : (
            <ul className="context-pack-list">
              {load.catalog.packs.map((pack) => (
                <li key={pack.contextPackId}>
                  <button className="context-pack-choice" disabled={save.kind === "saving" || uncertain} onClick={() => edit(pack.contextPackId)} type="button">
                    <span>{pack.summary}</span>
                    <code>{pack.contextPackId} @ {pack.revision}</code>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </>
      ) : null}
      <form className="context-pack-form" onSubmit={submit}>
        <fieldset disabled={locked}>
          <legend>{identity === null ? text("context_packs_publish_title") : text("context_packs_revise_title")}</legend>
          {identity !== null ? <p className="small-copy">{text("context_packs_expected_revision")}: <code>{identity.revision}</code></p> : null}
          <div className="context-editor-mode">
            <button aria-pressed={mode === "structured"} className="button button-secondary" onClick={() => switchMode("structured")} type="button">{text("context_source_structured")}</button>
            <button aria-pressed={mode === "advanced"} className="button button-secondary" onClick={() => switchMode("advanced")} type="button">{text("context_source_advanced")}</button>
          </div>
          <div hidden={mode !== "advanced"}>
            <label htmlFor="context-pack-advanced">{text("context_source_advanced")}</label>
            <p className="small-copy" id="context-pack-advanced-help">{text("context_source_advanced_help")}</p>
            <textarea aria-describedby="context-pack-advanced-help" id="context-pack-advanced" onChange={(event) => setAdvanced(event.target.value)} rows={20} value={advanced} />
          </div>
          <div className="context-editor-fields" hidden={mode !== "structured"}>
            <label htmlFor="context-pack-summary">{text("context_packs_summary")}</label>
            <textarea aria-describedby="context-packs-bounds-help" id="context-pack-summary" onChange={(event) => setEditor({ ...editor, summary: event.target.value })} rows={4} value={editor.summary} />
            <p className="small-copy" id="context-packs-bounds-help">{text("context_packs_bounds_help")}</p>
            <h3>{text("context_packs_facts")}</h3>
            {sources.kind === "loading" ? <p role="status">{text("context_source_loading")}</p> : null}
            {sources.kind === "error" ? <p className="notice" role="status">{text("context_source_error")} <code>{sources.code}</code></p> : null}
            {sourceCatalog?.truncated ? <p className="notice notice-warning" role="status">{text("context_source_truncated")}</p> : null}
            <div className="context-source-tools">
            <button className="button button-secondary" onClick={() => setSourceReload((value) => value + 1)} type="button">{text("context_source_retry")}</button>
            <p className="small-copy">{text("context_source_refresh_help")}</p>
            </div>
            {editor.facts.map((fact, index) => (
              <fieldset className="context-fact-row" key={fact.key}>
                <legend>{text("context_source_statement")} {index + 1}</legend>
                <label htmlFor={`${fact.key}-statement`}>{text("context_source_statement")}</label>
                <textarea id={`${fact.key}-statement`} onChange={(event) => setEditor({ ...editor, facts: editor.facts.map((row) => row.key === fact.key ? { ...row, statement: event.target.value } : row) })} rows={2} value={fact.statement} />
                {fact.sources.map((source, sourceIndex) => (
                  <div className="context-source-row" key={`${fact.key}-${sourceIndex}`}>
                    <ContextSourcePicker catalog={sourceCatalog} id={`${fact.key}-source-${sourceIndex}`} label={`${text("context_source_sources")} ${sourceIndex + 1}`} onChange={(value) => setEditor({ ...editor, facts: editor.facts.map((row) => row.key === fact.key ? { ...row, sources: row.sources.map((old, position) => position === sourceIndex ? value : old) } : row) })} text={text} value={source} />
                    <button className="button button-secondary" onClick={() => setEditor({ ...editor, facts: editor.facts.map((row) => row.key === fact.key ? { ...row, sources: row.sources.filter((_, position) => position !== sourceIndex) } : row) })} type="button">{text("context_source_remove")}</button>
                  </div>
                ))}
                <button className="button button-secondary" disabled={fact.sources.length >= 8} onClick={() => setEditor({ ...editor, facts: editor.facts.map((row) => row.key === fact.key ? { ...row, sources: [...row.sources, ""] } : row) })} type="button">{text("context_source_add")}</button>
                <button className="button button-secondary" onClick={() => setEditor({ ...editor, facts: editor.facts.filter((row) => row.key !== fact.key) })} type="button">{text("context_source_remove_fact")}</button>
              </fieldset>
            ))}
            <button className="button button-secondary" disabled={editor.facts.length >= 64} onClick={() => setEditor({ ...editor, facts: [...editor.facts, { key: rowKey(), statement: "", sources: [""] }] })} type="button">{text("context_source_add_fact")}</button>
            <h3>{text("context_packs_decisions")}</h3>
            {editor.decisions.map((decision, index) => (
              <div className="context-source-row" key={decision.key}>
                <ContextSourcePicker catalog={sourceCatalog} decisionOnly id={decision.key} label={`${text("context_packs_decisions")} ${index + 1}`} onChange={(value) => setEditor({ ...editor, decisions: editor.decisions.map((row) => row.key === decision.key ? { ...row, value } : row) })} text={text} value={decision.value} />
                <button className="button button-secondary" onClick={() => setEditor({ ...editor, decisions: editor.decisions.filter((row) => row.key !== decision.key) })} type="button">{text("context_source_remove_decision")}</button>
              </div>
            ))}
            <button className="button button-secondary" disabled={editor.decisions.length >= 32} onClick={() => setEditor({ ...editor, decisions: [...editor.decisions, { key: rowKey(), value: "" }] })} type="button">{text("context_source_add_decision")}</button>
            <h3>{text("context_packs_questions")}</h3>
            {editor.questions.map((question, index) => (
              <div className="context-question-row" key={question.key}>
                <label htmlFor={question.key}>{text("context_packs_questions")} {index + 1}</label>
                <textarea id={question.key} onChange={(event) => setEditor({ ...editor, questions: editor.questions.map((row) => row.key === question.key ? { ...row, value: event.target.value } : row) })} rows={2} value={question.value} />
                <button className="button button-secondary" onClick={() => setEditor({ ...editor, questions: editor.questions.filter((row) => row.key !== question.key) })} type="button">{text("context_source_remove_question")}</button>
              </div>
            ))}
            <button className="button button-secondary" disabled={editor.questions.length >= 32} onClick={() => setEditor({ ...editor, questions: [...editor.questions, { key: rowKey(), value: "" }] })} type="button">{text("context_source_add_question")}</button>
          </div>
          <button className="button button-primary" type="submit">{save.kind === "saving" ? text("working") : identity === null ? text("context_packs_publish") : text("context_packs_revise")}</button>
        </fieldset>
      </form>
      <div aria-live="polite" role="status">
        {save.kind === "success" ? <p className="notice">{text("context_packs_success")} <code>{save.revision}</code></p> : null}
        {save.kind === "stale" ? <div className="notice notice-warning"><p>{text("context_packs_stale")}</p>{identity !== null ? <button className="button button-secondary" onClick={() => edit(identity.id)} type="button">{text("context_packs_reload_current")}</button> : null}</div> : null}
        {save.kind === "validation" ? <p className="notice notice-warning">{text(save.code)}</p> : null}
        {save.kind === "error" ? <p className="notice">{text("context_packs_save_error")} <code>{save.code}</code></p> : null}
        {uncertain ? <div className="notice notice-warning"><p>{text("context_source_uncertain_save")}</p><button className="button button-secondary" disabled={!writesEnabled} onClick={() => { if (pending.current !== null) performSave(pending.current); }} type="button">{text("context_source_retry_save")}</button></div> : null}
      </div>
    </section>
  );
}
