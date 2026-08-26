import type { ReactElement } from "react";

import type { Failure } from "../contracts";
import type { MessageKey } from "../i18n";

type FailurePanelProps = {
  failure: Failure;
  text: (key: MessageKey) => string;
  onRetry: () => void;
};

export function FailurePanel({ failure, text, onRetry }: FailurePanelProps): ReactElement {
  return (
    <section className="failure-panel" aria-live="assertive" aria-labelledby="failure-title" role="alert">
      <p className="eyebrow">{text("status_code")}: <code>{failure.code}</code></p>
      <h2 id="failure-title">{failure.title}</h2>
      <p>{failure.nextStep}</p>
      {failure.safeNextActions.length > 0 ? (
        <div>
          <h3>{text("safe_next_actions")}</h3>
          <ul>
            {failure.safeNextActions.map((action) => <li key={action}>{action}</li>)}
          </ul>
        </div>
      ) : null}
      {failure.canRetry ? (
        <button className="button button-primary" onClick={onRetry} type="button">
          {text("retry")}
        </button>
      ) : null}
    </section>
  );
}
