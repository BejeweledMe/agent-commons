import type { ReactElement } from "react";
import type { MessageKey } from "../i18n.js";

/** Approximate a seven-line preview at the readable 70-character measure. */
export function briefNeedsExpansion(value: string): boolean {
  return value.split(/\r?\n/).reduce((lines, line) => lines + Math.max(1, Math.ceil(Array.from(line).length / 70)), 0) > 7;
}

/** Uncontrolled native disclosure: same-task refresh preserves its open state. */
export function TaskBrief({ description, text }: { description: string; text: (key: MessageKey) => string }): ReactElement {
  if (!briefNeedsExpansion(description)) return <section className="task-brief"><h3>{text("inspector_goal")}</h3><p className="inspector-prose task-brief-text">{description}</p></section>;
  return <section className="task-brief">
    <details className="task-brief-disclosure">
      <summary><h3>{text("inspector_goal")}</h3><span className="task-brief-expand">{text("inspector_brief_expand")}</span><span className="task-brief-collapse">{text("inspector_brief_collapse")}</span></summary>
      <p className="inspector-prose task-brief-text">{description}</p>
    </details>
    <p className="inspector-prose task-brief-text task-brief-preview">{description}</p>
  </section>;
}
