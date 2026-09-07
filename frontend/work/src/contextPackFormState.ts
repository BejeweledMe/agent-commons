import type { ContextPackDraft, RevisionBoundRef } from "./contracts";
import { validateContextPackDraft } from "./contextPackDraftValidation.js";
import { exactSourceKey } from "./contextSourcePicker.js";

export type FactRow = { key: string; statement: string; sources: readonly string[] };
export type TextRow = { key: string; value: string };
export type ContextPackForm = {
  summary: string;
  facts: readonly FactRow[];
  decisions: readonly TextRow[];
  questions: readonly TextRow[];
};

let nextRow = 0;
export function rowKey(): string { return `context-row-${++nextRow}`; }
export function emptyContextForm(): ContextPackForm {
  return { summary: "", facts: [], decisions: [], questions: [] };
}

const ID = "[0-9A-HJKMNP-TV-Z]{26}";
const EXACT = new RegExp(`^(artifact|finding|task|thread|verification|decision)\\.(${ID})@evt\\.(${ID})$`);

export function parseExactReference(value: string, decisionOnly: boolean): RevisionBoundRef {
  const match = EXACT.exec(value);
  if (match === null || (match[1] === "decision") !== decisionOnly) throw new Error("reference_format");
  return { kind: match[1] as RevisionBoundRef["kind"], id: `${match[1]}.${match[2]}`, revision: `evt.${match[3]}` };
}

export function formFromDraft(draft: ContextPackDraft): ContextPackForm {
  return {
    summary: draft.summary,
    facts: draft.facts.map((fact) => ({
      key: rowKey(), statement: fact.statement,
      sources: fact.sourceRefs.length === 0 ? [""] : fact.sourceRefs.map(exactSourceKey)
    })),
    decisions: draft.decisionRefs.map((ref) => ({ key: rowKey(), value: exactSourceKey(ref) })),
    questions: draft.openQuestions.map((value) => ({ key: rowKey(), value }))
  };
}

export function draftFromForm(form: ContextPackForm, validate = true): ContextPackDraft {
  const draft: ContextPackDraft = {
    summary: form.summary,
    facts: form.facts.map((fact) => ({
      statement: fact.statement,
      sourceRefs: fact.sources.filter(Boolean).map((value) => parseExactReference(value, false))
    })),
    decisionRefs: form.decisions.filter((row) => row.value !== "").map((row) => parseExactReference(row.value, true)),
    openQuestions: form.questions.map((row) => row.value)
  };
  if (validate && form.decisions.some((row) => !row.value)) throw new Error("reference_format");
  return validate ? validateContextPackDraft(draft) : draft;
}

function objectWithKeys(value: unknown, keys: readonly string[]): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).length === keys.length && keys.every((key) => key in value);
}

export function parseAdvancedDraft(value: string, validate = true): ContextPackDraft {
  const parsed: unknown = JSON.parse(value);
  if (!objectWithKeys(parsed, ["summary", "facts", "decisionRefs", "openQuestions"])
    || typeof parsed.summary !== "string" || !Array.isArray(parsed.facts)
    || !Array.isArray(parsed.decisionRefs) || !Array.isArray(parsed.openQuestions)
    || !parsed.openQuestions.every((question) => typeof question === "string")) throw new Error("draft_format");
  const readRef = (value: unknown, decisionOnly: boolean): RevisionBoundRef => {
    if (!objectWithKeys(value, ["kind", "id", "revision"]) || typeof value.id !== "string"
      || typeof value.revision !== "string") throw new Error("reference_format");
    const ref = parseExactReference(`${value.id}@${value.revision}`, decisionOnly);
    if (ref.kind !== value.kind) throw new Error("reference_format");
    return ref;
  };
  const draft: ContextPackDraft = {
    summary: parsed.summary,
    facts: parsed.facts.map((fact: unknown) => {
      if (!objectWithKeys(fact, ["statement", "sourceRefs"]) || typeof fact.statement !== "string"
        || !Array.isArray(fact.sourceRefs)) throw new Error("draft_format");
      return { statement: fact.statement, sourceRefs: fact.sourceRefs.map((ref) => readRef(ref, false)) };
    }),
    decisionRefs: parsed.decisionRefs.map((ref) => readRef(ref, true)),
    openQuestions: parsed.openQuestions as string[]
  };
  return validate ? validateContextPackDraft(draft) : draft;
}

export type ContextSaveOperation = {
  draft: ContextPackDraft;
  contextPackId: string | null;
  expectedRevision: string | null;
  idempotencyKey: string;
};

/** Keep an uncertain write independent from subsequent mutable form objects. */
export function snapshotSaveOperation(operation: ContextSaveOperation): ContextSaveOperation {
  return JSON.parse(JSON.stringify(operation)) as ContextSaveOperation;
}
