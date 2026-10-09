import { useEffect, useRef, useState, type ReactElement } from "react";
import type { WorkApi } from "../api.js";
import type { ProviderAvailability } from "../contracts.js";
import { translate, type Locale, type MessageKey } from "../i18n.js";
import { parseProjectEnvironment, qualificationCommand, type ProjectEnvironment as Observation } from "../projectEnvironment.js";
export function ProjectEnvironment({ api, locale }: { api: WorkApi; locale: Locale }): ReactElement {
  const [state, setState] = useState<{ kind: "idle" | "loading" | "failed" } | { kind: "ready"; value: Observation }>({ kind: "idle" });
  const request = useRef<AbortController | null>(null);
  useEffect(() => { setState({ kind: "idle" }); return () => request.current?.abort(); }, [api]);
  const text = (key: MessageKey) => translate(locale, key);
  async function read(): Promise<void> {
    request.current?.abort(); const controller = new AbortController(); request.current = controller; setState({ kind: "loading" });
    try { const value = parseProjectEnvironment(await api.readProjectEnvironment(controller.signal)); if (!controller.signal.aborted) setState({ kind: "ready", value }); }
    catch { if (!controller.signal.aborted) setState({ kind: "failed" }); }
  }
  return <section className="settings-panel"><h3>{text("environment_title")}</h3><p>{text("environment_advisory")}</p>
    <button className="button button-secondary" type="button" disabled={state.kind === "loading"} onClick={() => void read()}>{text(state.kind === "loading" ? "working" : "environment_check")}</button>
    {state.kind === "failed" ? <p role="alert">{text("environment_failed")}</p> : state.kind === "ready" ? <div>
      <p role="status">{text(`environment_${state.value.state}`)}</p>
      {(["manifests", "lockfiles", "dependencies"] as const).map((section) => <div key={section}><h4>{text(`environment_${section}`)}</h4><ul>{state.value[section].map((item) => <li key={item.name}><code>{item.name}</code>: {text(`environment_${item.state}`)}</li>)}</ul></div>)}
      <h4>{text("environment_tools")}</h4><ul>{state.value.tools.map((tool) => <li key={tool.name}>{tool.name}: {tool.observed ?? text("environment_unavailable")} · {text("environment_required")}: {tool.required ?? "—"}{tool.matches === false ? ` · ${text("environment_mismatch")}` : ""}</li>)}</ul>
    </div> : null}
  </section>;
}
export function ProviderQualificationAction({ availability, locale }: { availability: ProviderAvailability; locale: Locale }): ReactElement | null {
  const command = qualificationCommand(availability); if (command === null) return null;
  return <details className="provider-readiness-technical"><summary>{translate(locale, "qualification_action")}</summary><p>{translate(locale, "qualification_cost")}</p><p>{translate(locale, "qualification_distinct")}</p><p>{translate(locale, "qualification_paths")}</p><pre className="run-diagnostic-text">{command}</pre></details>;
}
