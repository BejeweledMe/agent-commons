import { type KeyboardEvent, type ReactElement, useRef, useState } from "react";

import type { WorkApi } from "../api.js";
import type { Locale, MessageKey } from "../i18n";
import { ContextPacksSection } from "./ContextPacksSection.js";
import { StarterPacksSection } from "./StarterPacksSection.js";
import { ServiceLibrarySection } from "./ServiceLibrarySection.js";
import type { LibraryApi } from "../libraryApi.js";
import type { BlueprintApplication, LibraryRole, LibraryState } from "../libraryTypes.js";
import type { Profile } from "../contracts.js";
import { WorkBlueprintsSection } from "./WorkBlueprintsSection.js";

export type LibraryTab = "roles" | "skills" | "blueprints" | "context";
type Props = {
  tab: LibraryTab;
  onTabChange: (tab: LibraryTab) => void;
  api: WorkApi;
  text: (key: MessageKey) => string;
  writesEnabled: boolean;
  onApplied: () => Promise<void>;
  onChooseTeam: () => void;
  libraryApi?: LibraryApi;
  libraryState?: LibraryState;
  onRefreshLibrary?: () => void;
  onChooseRole?: (role: LibraryRole) => void;
  profiles?: readonly Profile[];
  locale?: Locale;
  projectId?: string | null;
  onBlueprintApplied?: (application: BlueprintApplication) => void;
};
const tabs: readonly LibraryTab[] = ["roles", "skills", "blueprints", "context"];

export function LibrarySection({ tab, onTabChange, api, text, writesEnabled, onApplied, onChooseTeam, libraryApi, libraryState, onRefreshLibrary, onChooseRole, profiles, locale, projectId, onBlueprintApplied }: Props): ReactElement {
  const buttons = useRef<Partial<Record<LibraryTab, HTMLButtonElement | null>>>({});
  const [blueprintCount, setBlueprintCount] = useState<number | null>(null);
  const catalog = libraryState?.kind === "loading" ? null : libraryState?.catalog;
  const counts: Partial<Record<LibraryTab, number | null>> = { roles: catalog?.roles.length, skills: catalog?.skills.length, blueprints: blueprintCount };
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
            {text(`library_${value}`)}{counts[value] != null ? <span className="library-tab-count">{counts[value]}</span> : null}
          </button>
        ))}
      </div>
      {(["roles", "skills"] as const).map((value) => <div key={value} aria-labelledby={`library-tab-${value}`} className="library-panel" hidden={tab !== value} id={`library-panel-${value}`} role="tabpanel">
        {libraryApi && libraryState && onRefreshLibrary && onChooseRole ? <ServiceLibrarySection locale={locale ?? "en"} projectId={projectId} kind={value === "roles" ? "role" : "skill"} api={libraryApi} state={libraryState} onRefresh={onRefreshLibrary} onChooseRole={onChooseRole} text={text} writesEnabled={writesEnabled} /> : null}
      </div>)}
      <div aria-labelledby="library-tab-blueprints" className="library-panel" hidden={tab !== "blueprints"} id="library-panel-blueprints" role="tabpanel">
        {libraryApi && onBlueprintApplied ? <WorkBlueprintsSection catalog={catalog ?? undefined} api={libraryApi} profiles={profiles ?? []} locale={locale ?? "en"} projectId={projectId} text={text} writesEnabled={writesEnabled} onApplied={onBlueprintApplied} onCount={setBlueprintCount} /> : null}
        <details className="role-legacy-presets"><summary>{text("role_legacy_presets")}</summary><StarterPacksSection api={api} onApplied={onApplied} onChooseTeam={onChooseTeam} text={text} writesEnabled={writesEnabled} /></details>
      </div>
      <div aria-labelledby="library-tab-context" className="library-panel" hidden={tab !== "context"} id="library-panel-context" role="tabpanel">
        <ContextPacksSection api={api} projectId={projectId} text={text} writesEnabled={writesEnabled} />
      </div>

    </div>
  );
}
