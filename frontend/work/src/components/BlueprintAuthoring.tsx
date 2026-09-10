import { type ReactElement, useEffect, useRef, useState, useSyncExternalStore } from "react";
import type { LibraryApi } from "../libraryApi.js";
import type { MessageKey } from "../i18n.js";
import type { ServiceLibrary, WorkBlueprint } from "../libraryTypes.js";
import { blueprintDraft, blueprintSaveInput, libraryDraftMemory, libraryMutation, type BlueprintDraft } from "../libraryAuthoringState.js";
import { BlueprintDefinitionEditor } from "./BlueprintDefinitionEditor.js";
import { LibraryMutationFeedback } from "./LibraryMutationFeedback.js";
export function BlueprintAuthoring({ api, catalog, selected, plans, text, editable, applying, projectId, onRefresh }: { api: LibraryApi; catalog?: ServiceLibrary; selected: WorkBlueprint | null; plans: readonly WorkBlueprint[]; text: (key: MessageKey) => string; editable: boolean; applying: boolean; projectId?: string | null; onRefresh: () => void }): ReactElement {
  const memoryKey = `blueprint-definition:${projectId ?? "legacy"}`;
  const mutation = libraryMutation(memoryKey);
  const status = useSyncExternalStore(mutation.subscribe, mutation.snapshot, mutation.snapshot);
  const [draft, setDraft] = useState<BlueprintDraft | null>(() => libraryDraftMemory.get(memoryKey) as BlueprintDraft | null ?? null);
  const opener = useRef<HTMLButtonElement | null>(null);
  const locked = applying || status.status === "saving" || status.status === "uncertain";
  const allowed = editable && Boolean(catalog?.editingEnabled);
  const set = (value: BlueprintDraft | null): void => { setDraft(value); libraryDraftMemory.set(memoryKey, value); mutation.reset(); if (value === null) window.setTimeout(() => opener.current?.focus(), 0); };
  useEffect(() => { if (status.status === "saved") { setDraft(null); libraryDraftMemory.set(memoryKey, null); window.setTimeout(() => opener.current?.focus(), 0); onRefresh(); } }, [status]);
  return <div className="blueprint-authoring"><div className="button-row"><button type="button" className="button button-primary" disabled={!allowed || locked || draft !== null} onClick={(event) => { opener.current = event.currentTarget; set(blueprintDraft()); }}>{text("library_new_blueprint")}</button>{selected && selected.contentAvailable !== false ? <><button className="button button-secondary" type="button" disabled={!allowed || locked || draft !== null} onClick={(event) => { opener.current = event.currentTarget; set(blueprintDraft(selected, true)); }}>{text("library_blueprint_copy")}</button>{selected.source === "custom" ? <><button className="button button-secondary" type="button" disabled={!allowed || locked || draft !== null} onClick={(event) => { opener.current = event.currentTarget; set(blueprintDraft(selected)); }}>{text("library_edit_custom")}</button><button className="button button-secondary" type="button" disabled={!allowed || locked || draft !== null} onClick={() => { if (!allowed || locked) return; void mutation.start({ kind: "archive", id: selected.id, input: { expected_version: selected.version, archived: !selected.archived } }, api); }}>{text(selected.archived ? "library_restore_blueprint" : "library_archive_blueprint")}</button></> : null}</> : null}</div>
    <LibraryMutationFeedback mutation={mutation} text={text} editable={allowed && !applying} onRefresh={onRefresh} />
    {draft && catalog ? <BlueprintDefinitionEditor draft={draft} onChange={set} catalog={catalog} plans={plans} text={text} locked={locked || !allowed} onClose={() => set(null)} onSave={() => { if (!locked && allowed) void mutation.start({ kind: "blueprint", input: blueprintSaveInput(draft) }, api); }} /> : null}
  </div>;
}
