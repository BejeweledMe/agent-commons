import { useEffect, useId, useRef, useSyncExternalStore } from "react";
import type { Locale } from "../i18n.js";
import type { ProjectInspection } from "../projectWorkspace.js";
import { ProjectCreation, targetPath, type CreateMode, type FolderPurpose, type FolderSelection } from "../projectCreation.js";
import { projectCreationText } from "../projectCreationStrings.js";

type Props = { creation: ProjectCreation; locale: Locale; writable: boolean; onPickFolder?: (purpose: FolderPurpose, signal: AbortSignal) => Promise<FolderSelection>; onInspect: (input: { mode: CreateMode; path: string; name: string }) => Promise<ProjectInspection>; onCreate: (inspection: ProjectInspection) => Promise<void>; onClose: () => void };
export function ProjectCreateDialog({ creation, locale, writable, onPickFolder, onInspect, onCreate, onClose }: Props) {
  const value = useSyncExternalStore(creation.subscribe, creation.snapshot, creation.snapshot), text = projectCreationText(locale);
  const dialog = useRef<HTMLDialogElement>(null), heading = useId(), nameId = useId(), pathId = useId(), hintId = useId();
  const disabled = !writable || value.busy !== null || value.uncertain;
  useEffect(() => { if (value.open && !dialog.current?.open) dialog.current?.showModal(); if (!value.open && dialog.current?.open) dialog.current.close(); }, [value.open]);
  const close = () => { creation.close(); onClose(); };
  return <dialog ref={dialog} className="project-dialog project-create-dialog" aria-labelledby={heading} onCancel={(event) => { event.preventDefault(); close(); }}>
    <form noValidate onSubmit={(event) => { event.preventDefault(); if (!disabled) void creation.inspect(onInspect); }}>
      <div className="project-dialog-header"><h2 id={heading}>{text.title}</h2><button className="icon-close" aria-label={text.close} onClick={close} type="button">×</button></div>
      {!value.inspection ? <>
        <fieldset className="project-create-modes" disabled={disabled}><legend className="sr-only">{text.title}</legend>{(["new", "existing"] as const).map((mode) => <label key={mode} className={value.mode === mode ? "is-selected" : ""}><input type="radio" name="create-project-mode" checked={value.mode === mode} onChange={() => creation.setMode(mode)} />{text[mode]}</label>)}</fieldset>
        <p className="small-copy">{text[value.mode === "new" ? "newHelp" : "existingHelp"]}</p>
        <div className="project-folder-choice"><span>{text[value.mode === "new" ? "parent" : "folder"]}</span><button className="button button-secondary" type="button" disabled={disabled} onClick={() => void creation.pick(onPickFolder ?? (() => Promise.reject(new Error("picker_unavailable"))))}>{text[value.busy === "picker" ? "choosing" : value.mode === "new" ? "chooseParent" : "chooseFolder"]}</button><code title={value.location}>{value.location || text.emptyLocation}</code></div>
        {value.manual ? <div className="project-create-field"><label htmlFor={pathId}>{text.pathLabel}</label><input id={pathId} type="text" autoComplete="off" spellCheck={false} disabled={disabled} value={value.location} onChange={(event) => creation.setLocation(event.target.value)} /></div> : <button type="button" className="notice-link project-manual-path" disabled={disabled} onClick={() => creation.setManual()}>{text.manual}</button>}
        <div className="project-create-field"><label htmlFor={nameId}>{text.name}</label><input id={nameId} type="text" autoComplete="off" disabled={disabled} value={value.name} onChange={(event) => creation.setName(event.target.value)} aria-describedby={hintId} /></div>
        <p id={hintId} className="project-target-preview"><span>{text.preview}</span><code>{value.location && value.name.trim() ? targetPath(value) : "—"}</code></p>
        <div className="project-create-footer"><button type="submit" className="button button-primary" disabled={disabled}>{value.busy === "inspect" ? text.checking : text.check}</button></div>
      </> : <>
        <div className="project-create-summary"><span className="small-copy">{text.ready}</span><h3>{value.inspection.name}</h3><code>{targetPath(value)}</code></div>
        {value.mode === "existing" && value.inspection.initializationRequired ? <div className="project-initialize-consent"><label><input type="checkbox" disabled={disabled} checked={value.consent} onChange={(event) => creation.setConsent(event.target.checked)} />{text.initialize}</label><p className="small-copy">{text.initializeHelp}</p></div> : <p className="small-copy">{value.mode === "new" ? text.newReady : text.readyHelp}</p>}
        <div className="project-create-footer"><button type="button" className="button button-secondary" disabled={disabled} onClick={() => creation.back()}>{text.back}</button><button type="button" className="button button-primary" disabled={!writable || !creation.canCreate} onClick={() => void creation.create(onCreate)}>{value.busy === "create" ? text.creating : value.uncertain ? text.retry : value.mode === "new" ? text.create : text.connect}</button></div>
      </>}
      {value.problem ? <p className="field-error" role="alert">{text[value.problem === "name" ? "nameError" : value.problem]}</p> : null}
    </form>
  </dialog>;
}
