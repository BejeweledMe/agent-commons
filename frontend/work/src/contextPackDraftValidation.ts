import type { ContextPackDraft, RevisionBoundRef } from "./contracts";

export type ContextPackDraftValidationCode =
  | "context_packs_validation_summary_required"
  | "context_packs_validation_summary_limit"
  | "context_packs_validation_facts_limit"
  | "context_packs_validation_fact_limit"
  | "context_packs_validation_sources_limit"
  | "context_packs_validation_decisions_limit"
  | "context_packs_validation_questions_limit"
  | "context_packs_validation_question_limit"
  | "context_packs_validation_duplicate_reference"
  | "context_packs_validation_invalid_text"
  | "context_packs_validation_oversized";

const MAX_SUMMARY_CODE_POINTS = 4096;
const MAX_FACTS = 64;
const MAX_FACT_CODE_POINTS = 1024;
const MAX_SOURCES_PER_FACT = 8;
const MAX_DECISION_REFS = 32;
const MAX_OPEN_QUESTIONS = 32;
const MAX_OPEN_QUESTION_CODE_POINTS = 1024;
const MAX_SEMANTIC_BYTES = 65_536;
const ID = "[0-9A-HJKMNP-TV-Z]{26}";
const SOURCE_ID = new RegExp(`^(artifact|finding|task|thread|verification)\\.${ID}$`);
const DECISION_ID = new RegExp(`^decision\\.${ID}$`);
const EVENT_ID = new RegExp(`^evt\\.${ID}$`);

export class ContextPackDraftValidationError extends Error {
  readonly code: ContextPackDraftValidationCode;

  constructor(code: ContextPackDraftValidationCode) {
    super(code);
    this.name = "ContextPackDraftValidationError";
    this.code = code;
  }
}

function refuse(code: ContextPackDraftValidationCode): never {
  throw new ContextPackDraftValidationError(code);
}

function isWellFormedUnicode(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    const unit = value.charCodeAt(index);
    if (unit >= 0xd800 && unit <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff)) {
        return false;
      }
      index += 1;
    } else if (unit >= 0xdc00 && unit <= 0xdfff) {
      return false;
    }
  }
  return true;
}

function validateText(
  value: string,
  maximum: number,
  limitCode: ContextPackDraftValidationCode
): void {
  if (!isWellFormedUnicode(value)) {
    refuse("context_packs_validation_invalid_text");
  }
  if (value.length === 0 || Array.from(value).length > maximum) {
    refuse(limitCode);
  }
}

function referenceKey(reference: RevisionBoundRef): string {
  return `${reference.kind}:${reference.id}:${reference.revision}`;
}

function validateReference(reference: RevisionBoundRef, decisionOnly: boolean): void {
  const identifierMatches = decisionOnly
    ? reference.kind === "decision" && DECISION_ID.test(reference.id)
    : reference.kind !== "decision" && SOURCE_ID.test(reference.id);
  if (
    !identifierMatches
    || !reference.id.startsWith(`${reference.kind}.`)
    || !EVENT_ID.test(reference.revision)
  ) {
    refuse(decisionOnly
      ? "context_packs_validation_decisions_limit"
      : "context_packs_validation_sources_limit");
  }
}

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map(canonicalJson).join(",")}]`;
  }
  if (typeof value === "object" && value !== null) {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record).sort().map((key) =>
      `${JSON.stringify(key)}:${canonicalJson(record[key])}`
    ).join(",")}}`;
  }
  return JSON.stringify(value);
}

function semanticWire(draft: ContextPackDraft): Record<string, unknown> {
  const referenceWire = (reference: RevisionBoundRef): Record<string, unknown> => ({
    ref: { kind: reference.kind, id: reference.id },
    revision: reference.revision
  });
  return {
    summary: draft.summary,
    facts: draft.facts.map((fact) => ({
      statement: fact.statement,
      source_refs: fact.sourceRefs.map(referenceWire)
    })),
    decision_refs: draft.decisionRefs.map(referenceWire),
    open_questions: [...draft.openQuestions]
  };
}

export function contextPackDraftCanonicalBytes(draft: ContextPackDraft): number {
  return new TextEncoder().encode(canonicalJson(semanticWire(draft))).byteLength;
}

export function validateContextPackDraft(draft: ContextPackDraft): ContextPackDraft {
  if (!isWellFormedUnicode(draft.summary)) {
    refuse("context_packs_validation_invalid_text");
  }
  if (draft.summary.length === 0) {
    refuse("context_packs_validation_summary_required");
  }
  if (Array.from(draft.summary).length > MAX_SUMMARY_CODE_POINTS) {
    refuse("context_packs_validation_summary_limit");
  }
  if (draft.facts.length > MAX_FACTS) {
    refuse("context_packs_validation_facts_limit");
  }
  for (const fact of draft.facts) {
    validateText(fact.statement, MAX_FACT_CODE_POINTS, "context_packs_validation_fact_limit");
    if (fact.sourceRefs.length === 0 || fact.sourceRefs.length > MAX_SOURCES_PER_FACT) {
      refuse("context_packs_validation_sources_limit");
    }
    const sourceKeys = new Set<string>();
    for (const reference of fact.sourceRefs) {
      validateReference(reference, false);
      const key = referenceKey(reference);
      if (sourceKeys.has(key)) {
        refuse("context_packs_validation_duplicate_reference");
      }
      sourceKeys.add(key);
    }
  }
  if (draft.decisionRefs.length > MAX_DECISION_REFS) {
    refuse("context_packs_validation_decisions_limit");
  }
  const decisionKeys = new Set<string>();
  for (const reference of draft.decisionRefs) {
    validateReference(reference, true);
    const key = referenceKey(reference);
    if (decisionKeys.has(key)) {
      refuse("context_packs_validation_duplicate_reference");
    }
    decisionKeys.add(key);
  }
  if (draft.openQuestions.length > MAX_OPEN_QUESTIONS) {
    refuse("context_packs_validation_questions_limit");
  }
  for (const question of draft.openQuestions) {
    validateText(
      question,
      MAX_OPEN_QUESTION_CODE_POINTS,
      "context_packs_validation_question_limit"
    );
  }
  if (contextPackDraftCanonicalBytes(draft) > MAX_SEMANTIC_BYTES) {
    refuse("context_packs_validation_oversized");
  }
  return draft;
}
