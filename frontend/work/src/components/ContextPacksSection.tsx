import { type FormEvent, type ReactElement, useEffect, useRef, useState } from "react";

import { ApiProblem, type WorkApi } from "../api";
import { ContextPackRetryIdentity } from "../contextPackEditorState";
import {
  ContextPackDraftValidationError,
  type ContextPackDraftValidationCode,
  validateContextPackDraft
} from "../contextPackDraftValidation.js";
import type {
  ContextPackCatalog,
  ContextPackDetail,
  ContextPackDraft,
  ContextPackReferenceKind,
  RevisionBoundRef
} from "../contracts";
import type { MessageKey } from "../i18n";

type Props = {
  api: WorkApi;
  text: (key: MessageKey) => string;
  writesEnabled: boolean;
};

type LoadState =
  | { kind: "loading" }
  | { kind: "ready"; catalog: ContextPackCatalog }
  | { kind: "error"; code: string };

type SaveState =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "success"; revision: string }
  | { kind: "stale" }
  | { kind: "validation"; code: ContextPackDraftValidationCode | "context_packs_validation_format" }
  | { kind: "error"; code: string };

type Editor = {
  contextPackId: string | null;
  expectedRevision: string | null;
  summary: string;
  facts: string;
  decisions: string;
  questions: string;
};

const emptyEditor: Editor = {
  contextPackId: null,
  expectedRevision: null,
  summary: "",
  facts: "",
  decisions: "",
  questions: ""
};
const ID = "[0-9A-HJKMNP-TV-Z]{26}";
const SOURCE = new RegExp(`^(artifact|finding|task|thread|verification)\\.(${ID})@evt\\.(${ID})$`);
const DECISION = new RegExp(`^decision\\.(${ID})@evt\\.(${ID})$`);

function parseSource(value: string): RevisionBoundRef {
  const match = SOURCE.exec(value.trim());
  if (match === null) {
    throw new Error("source_ref");
  }
  return {
    kind: match[1] as ContextPackReferenceKind,
    id: `${match[1]}.${match[2]}`,
    revision: `evt.${match[3]}`
  };
}

function parseDraft(editor: Editor): ContextPackDraft {
  const summary = editor.summary.trim();
  if (!summary) {
    throw new Error("summary");
  }
  const facts = editor.facts.split("\n").map((line) => line.trim()).filter(Boolean).map((line) => {
    const delimiter = line.indexOf(" :: ");
    if (delimiter < 1) {
      throw new Error("facts");
    }
    const statement = line.slice(0, delimiter).trim();
    const sourceRefs = line.slice(delimiter + 4).split(",").map(parseSource);
    if (!statement || sourceRefs.length === 0) {
      throw new Error("facts");
    }
    return { statement, sourceRefs };
  });
  const decisionRefs = editor.decisions.split("\n").map((line) => line.trim()).filter(Boolean).map((line) => {
    const match = DECISION.exec(line);
    if (match === null) {
      throw new Error("decisions");
    }
    return { kind: "decision" as const, id: `decision.${match[1]}`, revision: `evt.${match[2]}` };
  });
  const openQuestions = editor.questions.split("\n").map((line) => line.trim()).filter(Boolean);
  return validateContextPackDraft({ summary, facts, decisionRefs, openQuestions });
}

function referenceText(ref: RevisionBoundRef): string {
  return `${ref.id}@${ref.revision}`;
}

function editorFrom(detail: ContextPackDetail): Editor {
  return {
    contextPackId: detail.contextPackId,
    expectedRevision: detail.revision,
    summary: detail.summary,
    facts: detail.facts.map((fact) =>
      `${fact.statement} :: ${fact.sourceRefs.map(referenceText).join(",")}`
    ).join("\n"),
    decisions: detail.decisionRefs.map(referenceText).join("\n"),
    questions: detail.openQuestions.join("\n")
  };
}

function problemCode(error: unknown): string {
  return error instanceof ApiProblem ? error.apiError?.code ?? "request_unavailable" : "request_unavailable";
}

export function ContextPacksSection({ api, text, writesEnabled }: Props): ReactElement {
  const [load, setLoad] = useState<LoadState>({ kind: "loading" });
  const [save, setSave] = useState<SaveState>({ kind: "idle" });
  const [editor, setEditor] = useState<Editor>(emptyEditor);
  const [reload, setReload] = useState(0);
  const retryIdentity = useRef(new ContextPackRetryIdentity());

  useEffect(() => {
    const controller = new AbortController();
    setLoad({ kind: "loading" });
    api.loadContextPacks(controller.signal).then(
      (catalog) => setLoad({ kind: "ready", catalog }),
      (error: unknown) => {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          setLoad({ kind: "error", code: problemCode(error) });
        }
      }
    );
    return () => controller.abort();
  }, [api, reload]);

  const edit = (contextPackId: string): void => {
    const controller = new AbortController();
    setSave({ kind: "saving" });
    api.loadContextPack(contextPackId, controller.signal).then(
      (detail) => {
        setEditor(editorFrom(detail));
        setSave({ kind: "idle" });
      },
      (error: unknown) => setSave({ kind: "error", code: problemCode(error) })
    );
  };

  const submit = (event: FormEvent): void => {
    event.preventDefault();
    let draft: ContextPackDraft;
    try {
      draft = parseDraft(editor);
    } catch (error: unknown) {
      setSave({
        kind: "validation",
        code: error instanceof ContextPackDraftValidationError
          ? error.code
          : "context_packs_validation_format"
      });
      return;
    }
    const signature = JSON.stringify({ draft, id: editor.contextPackId, revision: editor.expectedRevision });
    const idempotencyKey = retryIdentity.current.forOperation(signature);
    const controller = new AbortController();
    setSave({ kind: "saving" });
    const operation = editor.contextPackId === null || editor.expectedRevision === null
      ? api.publishContextPack(draft, idempotencyKey, controller.signal)
      : api.reviseContextPack(
        editor.contextPackId,
        editor.expectedRevision,
        draft,
        idempotencyKey,
        controller.signal
      );
    operation.then(
      (detail) => {
        setEditor(editorFrom(detail));
        setSave({ kind: "success", revision: detail.revision });
        setReload((value) => value + 1);
      },
      (error: unknown) => {
        const code = problemCode(error);
        setSave(code === "context_pack_stale" ? { kind: "stale" } : { kind: "error", code });
      }
    );
  };

  return (
    <section aria-labelledby="context-packs-title" className="context-packs-section">
      <header className="context-packs-header">
        <div>
          <p className="eyebrow">{text("context_packs_eyebrow")}</p>
          <h2 id="context-packs-title">{text("context_packs_title")}</h2>
        </div>
        <button className="button button-secondary" onClick={() => { retryIdentity.current.reset(); setEditor(emptyEditor); setSave({ kind: "idle" }); }} type="button">
          {text("context_packs_new")}
        </button>
      </header>
      <p className="small-copy">{text("context_packs_intro")}</p>
      <p className="small-copy">{text("context_packs_context_notice")}</p>
      {load.kind === "loading" ? <p aria-live="polite" role="status">{text("context_packs_loading")}</p> : null}
      {load.kind === "error" ? (
        <div aria-live="polite" className="notice" role="status">
          <p>{text("context_packs_load_error")}</p>
          <p className="small-copy">{text("status_code")}: <code>{load.code}</code></p>
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
                  <button className="context-pack-choice" onClick={() => edit(pack.contextPackId)} type="button">
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
        <fieldset disabled={!writesEnabled || save.kind === "saving"}>
          <legend>{editor.contextPackId === null ? text("context_packs_publish_title") : text("context_packs_revise_title")}</legend>
          {editor.expectedRevision !== null ? <p className="small-copy">{text("context_packs_expected_revision")}: <code>{editor.expectedRevision}</code></p> : null}
          <label htmlFor="context-pack-summary">{text("context_packs_summary")}</label>
          <textarea aria-describedby="context-packs-bounds-help" id="context-pack-summary" onChange={(event) => setEditor({ ...editor, summary: event.target.value })} required rows={4} value={editor.summary} />
          <p className="small-copy" id="context-packs-bounds-help">{text("context_packs_bounds_help")}</p>
          <label htmlFor="context-pack-facts">{text("context_packs_facts")}</label>
          <textarea aria-describedby="context-pack-facts-help" id="context-pack-facts" onChange={(event) => setEditor({ ...editor, facts: event.target.value })} rows={5} value={editor.facts} />
          <p className="small-copy" id="context-pack-facts-help">{text("context_packs_facts_help")}</p>
          <label htmlFor="context-pack-decisions">{text("context_packs_decisions")}</label>
          <textarea aria-describedby="context-pack-decisions-help" id="context-pack-decisions" onChange={(event) => setEditor({ ...editor, decisions: event.target.value })} rows={3} value={editor.decisions} />
          <p className="small-copy" id="context-pack-decisions-help">{text("context_packs_decisions_help")}</p>
          <label htmlFor="context-pack-questions">{text("context_packs_questions")}</label>
          <textarea aria-describedby="context-pack-questions-help" id="context-pack-questions" onChange={(event) => setEditor({ ...editor, questions: event.target.value })} rows={4} value={editor.questions} />
          <p className="small-copy" id="context-pack-questions-help">{text("context_packs_questions_help")}</p>
          <button className="button button-primary" type="submit">{save.kind === "saving" ? text("working") : editor.contextPackId === null ? text("context_packs_publish") : text("context_packs_revise")}</button>
        </fieldset>
      </form>
      <div aria-live="polite" role="status">
        {save.kind === "success" ? <p className="notice">{text("context_packs_success")} <code>{save.revision}</code></p> : null}
        {save.kind === "stale" ? (
          <div className="notice notice-warning">
            <p>{text("context_packs_stale")}</p>
            {editor.contextPackId !== null ? (
              <button className="button button-secondary" onClick={() => edit(editor.contextPackId as string)} type="button">{text("context_packs_reload_current")}</button>
            ) : null}
          </div>
        ) : null}
        {save.kind === "validation" ? <p className="notice notice-warning">{text(save.code)}</p> : null}
        {save.kind === "error" ? <p className="notice">{text("context_packs_save_error")} <code>{save.code}</code></p> : null}
      </div>
    </section>
  );
}
