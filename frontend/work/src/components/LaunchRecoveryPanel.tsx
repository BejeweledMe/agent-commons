import type { ReactElement } from "react";
import type { LaunchIntent } from "../launchIntentState.js";
import type { MessageKey } from "../i18n";

export function LaunchRecoveryPanel({ intent, visible, busy, uncertain, failureCode, onRestore, onRetry, onEdit, text }: {
  intent: LaunchIntent;
  visible: boolean;
  busy: boolean;
  uncertain: boolean;
  failureCode: string | null;
  onRestore: () => void;
  onRetry: () => void;
  onEdit: () => void;
  text: (key: MessageKey) => string;
}): ReactElement {
  return <section className="launch-recovery notice" aria-labelledby="launch-recovery-title" role="status">
    <h2 id="launch-recovery-title">{text("shell_launch_recovery")}</h2>
    <p>{text("tracker_task_label")}: <strong>{intent.taskTitle}</strong></p>
    <p>{text("tracker_role_label")}: <strong>{intent.roleName}</strong> <code>{intent.profileId}</code></p>
    <p>{text(uncertain ? "shell_launch_uncertain" : "shell_launch_original")}</p>
    <details><summary>{text("shell_technical_details")}</summary>
      <dl><div><dt>{text("tracker_task_label")}</dt><dd><code>{intent.input.taskId}</code></dd></div>
        <div><dt>{text("tracker_role_label")}</dt><dd><code>{intent.input.agentId}</code></dd></div>
        <div><dt>{text("context_mode")}</dt><dd>{intent.draft.contextPackKey ? <code>{intent.draft.contextPackKey}</code> : text("context_fresh")}</dd></div>
        <div><dt>{text("select_design_package")}</dt><dd><code>{intent.draft.designPackageKey || "—"}</code></dd></div>
        {failureCode ? <div><dt>{text("shell_failure_code")}</dt><dd><code>{failureCode}</code></dd></div> : null}
      </dl>
    </details>
    <div className="button-row">
      {visible ? <button className="button button-secondary" disabled={busy} type="button" onClick={onRetry}>{text("shell_retry_original")}</button>
        : <button className="button button-secondary" disabled={busy} type="button" onClick={onRestore}>{text("shell_restore_launch")}</button>}
      {!uncertain && failureCode !== null ? <button className="button button-secondary" disabled={busy} type="button" onClick={onEdit}>{text("shell_edit_launch")}</button> : null}
    </div>
  </section>;
}
