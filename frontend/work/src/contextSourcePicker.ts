import type { ContextSourceCatalog, ContextSourceOption, RevisionBoundRef } from "./contracts";

export type SourceSelectionState = "current" | "stale" | "unavailable" | "unconfirmed";

export type SourceDisplayMemory = {
  pickerId: string;
  key: string;
  decisionOnly: boolean;
  label: string | null;
};

/** Remember display metadata for this selection only; it never resolves a ref. */
export function updateSourceDisplayMemory(
  previous: SourceDisplayMemory | null,
  pickerId: string,
  key: string,
  decisionOnly: boolean,
  catalog: ContextSourceCatalog | null
): SourceDisplayMemory {
  const sameSelection = previous?.pickerId === pickerId && previous.key === key
    && previous.decisionOnly === decisionOnly;
  const observed = sourceChoices(catalog, decisionOnly, "").find((source) => exactSourceKey(source.ref) === key);
  const label = observed?.label ?? (sameSelection ? previous.label : null);
  if (sameSelection && previous.label === label) return previous;
  return { pickerId, key, decisionOnly, label };
}

export function exactSourceKey(ref: RevisionBoundRef): string {
  return `${ref.id}@${ref.revision}`;
}

export function sourceSelectionState(
  key: string,
  catalog: ContextSourceCatalog | null
): SourceSelectionState {
  if (catalog === null) return "unconfirmed";
  if (catalog.sources.some((source) => exactSourceKey(source.ref) === key)) return "current";
  const id = key.split("@")[0];
  if (catalog.sources.some((source) => source.ref.id === id)) return "stale";
  return catalog.truncated ? "unconfirmed" : "unavailable";
}

export function sourceChoices(
  catalog: ContextSourceCatalog | null,
  decisionOnly: boolean,
  query: string
): readonly ContextSourceOption[] {
  const needle = query.trim().toLocaleLowerCase();
  return (catalog?.sources ?? []).filter((source) =>
    (source.ref.kind === "decision") === decisionOnly
    && (!needle || `${source.label} ${exactSourceKey(source.ref)}`.toLocaleLowerCase().includes(needle))
  );
}

/** Resolve only a catalog-observed exact key, never another revision of its ID. */
export function chooseExactSource(
  key: string,
  catalog: ContextSourceCatalog | null,
  decisionOnly: boolean
): RevisionBoundRef | null {
  return sourceChoices(catalog, decisionOnly, "").find((source) => exactSourceKey(source.ref) === key)?.ref ?? null;
}
