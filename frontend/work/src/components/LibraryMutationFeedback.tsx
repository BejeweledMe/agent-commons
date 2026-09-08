import { type ReactElement, useSyncExternalStore } from "react";
import type { MessageKey } from "../i18n.js";
import type { LibraryMutation } from "../libraryAuthoringState.js";
export function LibraryMutationFeedback({ mutation, text, editable, onRefresh }: { mutation: LibraryMutation; text: (key: MessageKey) => string; editable: boolean; onRefresh: () => void }): ReactElement | null {
  const value = useSyncExternalStore(mutation.subscribe, mutation.snapshot, mutation.snapshot);
  if (value.status === "idle") return null;
  if (value.status === "saving") return <p role="status">{text("working")}</p>;
  if (value.status === "saved") return <p className="notice" role="status">{text("library_metadata_saved")}</p>;
  return <div className="notice" role="alert"><p>{text(value.status === "uncertain" ? "library_save_uncertain" : "library_save_refused")}</p><div className="button-row"><button type="button" className="button button-secondary" disabled={!editable} onClick={() => void mutation.retry()}>{text("blueprints_retry")}</button>{value.status === "refused" ? <button type="button" className="button button-secondary" onClick={onRefresh}>{text("refresh_status")}</button> : null}</div></div>;
}
