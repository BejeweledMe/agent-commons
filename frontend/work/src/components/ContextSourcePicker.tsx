import { type ReactElement, useState } from "react";

import type { ContextSourceCatalog } from "../contracts";
import { chooseExactSource, exactSourceKey, sourceChoices, sourceSelectionState, updateSourceDisplayMemory } from "../contextSourcePicker.js";
import type { MessageKey } from "../i18n";

type Props = {
  id: string;
  label: string;
  value: string;
  catalog: ContextSourceCatalog | null;
  decisionOnly?: boolean;
  onChange: (key: string) => void;
  text: (key: MessageKey) => string;
};

export function ContextSourcePicker({ id, label, value, catalog, decisionOnly = false, onChange, text }: Props): ReactElement {
  const [query, setQuery] = useState("");
  const [displayMemory, setDisplayMemory] = useState(() => updateSourceDisplayMemory(null, id, value, decisionOnly, catalog));
  const display = updateSourceDisplayMemory(displayMemory, id, value, decisionOnly, catalog);
  // Adjust this component's display state before committing a changed selection.
  if (display !== displayMemory) setDisplayMemory(display);
  const options = sourceChoices(catalog, decisionOnly, query);
  const selected = sourceChoices(catalog, decisionOnly, "").find((option) => exactSourceKey(option.ref) === value);
  const state = value ? sourceSelectionState(value, catalog) : null;
  const selectedVisible = options.some((option) => exactSourceKey(option.ref) === value);
  // Distinguish duplicate titles without making every choice an opaque reference.
  const optionLabel = (option: (typeof options)[number]): string =>
    options.some((other) => other !== option && other.label === option.label)
      ? `${option.label} — ${exactSourceKey(option.ref)}` : option.label;
  return (
    <div className="context-source-picker">
      <label htmlFor={`${id}-search`}>{text("context_source_search")}</label>
      <input className="context-source-search" id={`${id}-search`} onChange={(event) => setQuery(event.target.value)} type="search" value={query} />
      <label htmlFor={id}>{label}</label>
      <select
        aria-describedby={value ? `${id}-status` : undefined}
        aria-invalid={state === "stale" || state === "unavailable" ? true : undefined}
        className="context-source-selection"
        id={id}
        onChange={(event) => {
          const key = event.target.value;
          if (!key || chooseExactSource(key, catalog, decisionOnly) !== null) onChange(key);
        }}
        value={value}
      >
        <option value="">{text("context_source_select")}</option>
        {value && !selectedVisible ? (
          <option disabled={selected === undefined} value={value}>{display.label ?? text(`context_source_${state ?? "unconfirmed"}`)}</option>
        ) : null}
        {options.map((option) => (
          <option key={exactSourceKey(option.ref)} value={exactSourceKey(option.ref)}>
            {optionLabel(option)}
          </option>
        ))}
      </select>
      {options.length === 0 ? <p className="small-copy">{text("context_source_no_matches")}</p> : null}
      {state !== null ? (
        <p className="small-copy context-source-status" id={`${id}-status`} role="status">
          {text(`context_source_${state}`)}
        </p>
      ) : null}
      {value ? (
        <details className="context-source-reference">
          <summary>{text("library_technical_details")}</summary>
          <code>{value}</code>
        </details>
      ) : null}
    </div>
  );
}
