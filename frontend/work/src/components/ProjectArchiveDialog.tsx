import { type ReactElement, useEffect, useId, useRef, useSyncExternalStore } from "react";

import type { MessageKey } from "../i18n.js";
import type { ArchiveRequest, ProjectArchiveConfirmation } from "../projectArchive.js";

type Props = {
  confirmation: ProjectArchiveConfirmation;
  disabled: boolean;
  text: (key: MessageKey) => string;
  onArchive: (request: ArchiveRequest) => Promise<boolean>;
  onClose: (projectId: string) => void;
};

/** Archiving asks first; restoring stays a direct, reversible action. */
export function ProjectArchiveDialog({ confirmation, disabled, text, onArchive, onClose }: Props): ReactElement {
  const value = useSyncExternalStore(confirmation.subscribe, confirmation.snapshot, confirmation.snapshot);
  const dialog = useRef<HTMLDialogElement>(null);
  const cancelButton = useRef<HTMLButtonElement>(null);
  const heading = useId(), description = useId();
  const request = value.request;
  useEffect(() => {
    if (value.open && !dialog.current?.open) { dialog.current?.showModal(); cancelButton.current?.focus(); }
    if (!value.open && dialog.current?.open) dialog.current.close();
  }, [value.open]);
  const dismiss = (): void => {
    const projectId = request?.projectId ?? null;
    confirmation.cancel();
    if (projectId !== null) onClose(projectId);
  };
  const archive = (): void => {
    const target = request;
    if (target === null) return;
    void confirmation.confirm(onArchive).then((archived) => { if (archived) onClose(target.projectId); });
  };
  return <dialog ref={dialog} aria-describedby={description} aria-labelledby={heading}
    className="project-dialog project-archive-dialog"
    onCancel={(event) => { event.preventDefault(); dismiss(); }}>
    <div className="project-archive-body">
      <h2 id={heading}>{text("project_archive_title")}</h2>
      <p className="project-archive-name">{request?.name ?? ""}</p>
      {request?.path
        ? <p className="project-archive-path"><code>{request.path}</code></p>
        : <p className="project-archive-path small-copy">{text("project_archive_path_unknown")}</p>}
      <p id={description}>{text("project_archive_explains")}</p>
      <div className="button-row">
        <button ref={cancelButton} className="button button-secondary button-inline" disabled={value.busy}
          onClick={dismiss} type="button">{text("project_archive_cancel")}</button>
        <button className="button button-primary button-inline" disabled={disabled || value.busy}
          onClick={archive} type="button">{value.busy ? text("working") : text("project_archive_confirm")}</button>
      </div>
    </div>
  </dialog>;
}
