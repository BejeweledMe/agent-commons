import type { ReactElement, ReactNode } from "react";
import type { StopReason } from "../contracts";
import type { MessageKey } from "../i18n";
import { stopReasonAction, stopReasonSentence } from "../stopReason.js";

type Text = (key: MessageKey) => string;

/**
 * The server's typed reason, rendered as one plain sentence and one next
 * action. The code chooses the sentence, the bounded `reason` is quoted as the
 * server's own words, and an action token outside the closed glossary is shown
 * verbatim in `<code>` rather than guessed at. The bounded provider diagnostic
 * never reaches this layer: it stays in a closed disclosure beside its run.
 */
export function StopReasonNotice({ reason, text, control = null, className = "", showSentence = true,
  role = undefined }: {
  reason: StopReason;
  text: Text;
  /** The one control this surface can offer for the action, when it has it. */
  control?: ReactNode;
  className?: string;
  /** `false` where the surface already states the sentence, so it is not said twice. */
  showSentence?: boolean;
  /** Set where this notice is the live region for the observed state. */
  role?: "status";
}): ReactElement {
  const action = stopReasonAction(reason.nextAction);
  return <div className={`stop-reason ${className}`.trim()} role={role} data-code={reason.code}
    data-next-action={reason.nextAction === "" ? "none" : reason.nextAction}>
    {showSentence ? <p className="stop-reason-sentence">{text(stopReasonSentence(reason.code))}</p> : null}
    <p className="small-copy stop-reason-recorded">{text("stop_reason_recorded")} {reason.reason}</p>
    <p className="stop-reason-action">
      <span className="decision-label">{text("stop_reason_next")}: </span>
      {action.key !== null ? <span>{text(action.key)}</span>
        : reason.nextAction === "" ? <span>{text("stop_action_unnamed")}</span>
          : <span>{text("stop_action_unrecognized")} <code>{reason.nextAction}</code></span>}
    </p>
    {control}
  </div>;
}
