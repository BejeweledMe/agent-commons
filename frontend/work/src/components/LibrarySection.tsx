import { type KeyboardEvent, type ReactElement, useRef } from "react";

import type { WorkApi } from "../api.js";
import type { MessageKey } from "../i18n";
import { ContextPacksSection } from "./ContextPacksSection.js";
import { StarterPacksSection } from "./StarterPacksSection.js";

export type LibraryTab = "templates" | "context" | "design";
type Props = {
  tab: LibraryTab;
  onTabChange: (tab: LibraryTab) => void;
  api: WorkApi;
  text: (key: MessageKey) => string;
  writesEnabled: boolean;
  onApplied: () => Promise<void>;
  onChooseTeam: () => void;
};
const tabs: readonly LibraryTab[] = ["templates", "context", "design"];

export function LibrarySection({ tab, onTabChange, api, text, writesEnabled, onApplied, onChooseTeam }: Props): ReactElement {
  const buttons = useRef<Partial<Record<LibraryTab, HTMLButtonElement | null>>>({});
  const navigate = (event: KeyboardEvent, current: LibraryTab): void => {
    const index = tabs.indexOf(current);
    const next = event.key === "ArrowRight" ? tabs[(index + 1) % tabs.length]
      : event.key === "ArrowLeft" ? tabs[(index + tabs.length - 1) % tabs.length]
        : event.key === "Home" ? tabs[0] : event.key === "End" ? tabs[tabs.length - 1] : null;
    if (next !== null) { event.preventDefault(); onTabChange(next); buttons.current[next]?.focus(); }
  };
  return (
    <div className="library-section">
      <div aria-label={text("library_title")} className="library-tabs" role="tablist">
        {tabs.map((value) => (
          <button aria-controls={`library-panel-${value}`} aria-selected={tab === value} className="button button-secondary" id={`library-tab-${value}`} key={value} onClick={() => onTabChange(value)} onKeyDown={(event) => navigate(event, value)} ref={(element) => { buttons.current[value] = element; }} role="tab" tabIndex={tab === value ? 0 : -1} type="button">
            {text(`library_${value}`)}
          </button>
        ))}
      </div>
      <div aria-labelledby="library-tab-templates" className="library-panel" hidden={tab !== "templates"} id="library-panel-templates" role="tabpanel">
        <StarterPacksSection api={api} onApplied={onApplied} onChooseTeam={onChooseTeam} text={text} writesEnabled={writesEnabled} />
      </div>
      <div aria-labelledby="library-tab-context" className="library-panel" hidden={tab !== "context"} id="library-panel-context" role="tabpanel">
        <ContextPacksSection api={api} text={text} writesEnabled={writesEnabled} />
      </div>
      <div aria-labelledby="library-tab-design" className="library-panel" hidden={tab !== "design"} id="library-panel-design" role="tabpanel">
        <h2>{text("library_design")}</h2>
        <p className="lead-copy">{text("library_design_intro")}</p>
        <a className="button button-primary" href="/gallery">{text("library_open_gallery")}</a>
      </div>
    </div>
  );
}
