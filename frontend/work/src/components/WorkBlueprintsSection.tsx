import { type FormEvent, type ReactElement, useEffect, useRef, useState } from "react";
import { ApiProblem } from "../api.js";
import type { Profile } from "../contracts.js";
import { blueprintBriefIsValid, type LibraryApi } from "../libraryApi.js";
import { selectBlueprintProfile, type BlueprintRuntimeChoice } from "../libraryEditorState.js";
import type { Locale, MessageKey } from "../i18n.js";
import type { BlueprintApplication, BlueprintApplyInput, BlueprintBinding, WorkBlueprint } from "../libraryTypes.js";

type ApplyIntent = Readonly<{ id: string; input: BlueprintApplyInput; key: string }>;
export function WorkBlueprintsSection({ api, profiles, locale, text, writesEnabled, onApplied, onCount }: {
  api: LibraryApi; profiles: readonly Profile[]; locale: Locale; text: (key: MessageKey) => string;
  writesEnabled: boolean; onApplied: (application: BlueprintApplication) => void; onCount?: (count: number) => void;
}): ReactElement {
  const [blueprints, setBlueprints] = useState<readonly WorkBlueprint[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [selected, setSelected] = useState<WorkBlueprint | null>(null);
  const [title, setTitle] = useState("");
  const [brief, setBrief] = useState("");
  const [bindings, setBindings] = useState<BlueprintBinding[]>([]);
  const [defaultRuntime, setDefaultRuntime] = useState<BlueprintRuntimeChoice>({ profile_id: "", model: null });
  const defaultProfile = defaultRuntime.profile_id;
  const defaultModel = defaultRuntime.model ?? "";
  const [status, setStatus] = useState<"idle" | "applying" | "refused" | "uncertain">("idle");
  const [intent, setIntent] = useState<ApplyIntent | null>(null);
  const [result, setResult] = useState<BlueprintApplication | null>(null);
  const [invalid, setInvalid] = useState(false);
  const [code, setCode] = useState("");
  const executing = useRef(false);
  const locked = status === "applying" || status === "uncertain";
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setLoadError(false);
    void api.blueprints(controller.signal).then((items) => { if (!controller.signal.aborted) { setBlueprints(items); setLoading(false); onCount?.(items.length); } }).catch(() => { if (!controller.signal.aborted) { setLoadError(true); setLoading(false); } });
    return () => controller.abort();
  }, [api, refresh, onCount]);
  function choose(blueprint: WorkBlueprint): void {
    if (locked) return;
    setSelected(blueprint); setTitle(blueprint.name[locale]); setBindings(blueprint.slots.map((slot) => ({ slot_id: slot.id, name: slot.name, profile_id: "", model: null })));
    setStatus("idle"); setIntent(null); setResult(null); setInvalid(false);
  }
  function updateBinding(index: number, change: Partial<BlueprintBinding>): void {
    setBindings((current) => current.map((binding, i) => i === index
      ? change.profile_id === undefined ? { ...binding, ...change } : selectBlueprintProfile({ ...binding, ...change }, change.profile_id)
      : binding)); setStatus("idle"); setIntent(null);
  }
  async function execute(operation: ApplyIntent): Promise<void> {
    if (executing.current) return;
    executing.current = true;
    setStatus("applying"); setCode("");
    try { const application = await api.applyBlueprint(operation.id, operation.input, operation.key, new AbortController().signal); setResult(application); setIntent(null); setStatus("idle"); onApplied(application); }
    catch (error: unknown) { const problem = error instanceof ApiProblem ? error : null; setIntent(operation); setStatus(problem && problem.status >= 400 && problem.status < 500 ? "refused" : "uncertain"); setCode(problem?.apiError?.code ?? "request_unavailable"); }
    finally { executing.current = false; }
  }
  function submit(event: FormEvent): void {
    event.preventDefault(); if (!selected || locked || executing.current || !writesEnabled) return;
    const valid = title.trim() && title.length <= 128 && blueprintBriefIsValid(brief) && bindings.length === selected.slots.length
      && bindings.every((binding) => binding.name.trim() && binding.name.length <= 128 && profiles.some((profile) => profile.id === binding.profile_id && profile.configured !== false)
        && (binding.model === null || /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/.test(binding.model.trim())));
    setInvalid(!valid); if (!valid) return;
    const operation: ApplyIntent = { id: selected.id, key: crypto.randomUUID(), input: structuredClone({ expected_version: selected.version, title: title.trim(), brief: brief.trim(), locale, bindings: bindings.map((binding) => ({ ...binding, name: binding.name.trim(), model: binding.model?.trim() || null })) }) };
    setIntent(operation); void execute(operation);
  }
  const current = !selected || blueprints.some((blueprint) => blueprint.id === selected.id && blueprint.version === selected.version);
  const options = profiles.map((profile) => <option key={profile.id} value={profile.id} disabled={profile.configured === false}>{profile.label}</option>);
  return <section className="work-blueprints" aria-label={text("blueprints_title")}>
    <div className="composer-heading"><div><h2>{text("blueprints_title")}</h2><p className="lead-copy">{text("blueprints_intro")}</p></div><button type="button" className="button button-secondary" onClick={() => setRefresh((value) => value + 1)}>{text("refresh_status")}</button></div>
    {loading ? <p role="status">{text("library_loading")}</p> : null}{loadError ? <p className="field-error" role="alert">{text("blueprints_load_error")}</p> : null}
    <div className="blueprint-choice-grid">{blueprints.map((blueprint) => <button className="blueprint-choice" type="button" key={blueprint.id} disabled={locked} aria-pressed={selected?.id === blueprint.id} onClick={() => choose(blueprint)}><strong>{blueprint.name[locale]}</strong><span>{blueprint.description[locale]}</span><span className="small-copy">{blueprint.slots.length} {text("blueprints_roles_count")} · {blueprint.tasks.length} {text("blueprints_tasks_count")}</span></button>)}</div>
    {selected ? <div className="blueprint-composer"><h3>{selected.name[locale]}</h3><p>{text("blueprints_preview_help")}</p>
      <ol className="blueprint-task-preview">{selected.tasks.map((task) => <li key={task.id}><strong>{task.title[locale]}</strong><p className="small-copy">{selected.slots.find((slot) => slot.id === task.slot_id)?.name}</p>{task.depends_on.length ? <p className="small-copy">{text("blueprints_after")}: {task.depends_on.map((id) => selected.tasks.find((item) => item.id === id)?.title[locale] ?? id).join(" · ")}</p> : <p className="small-copy">{text("blueprints_start_node")}</p>}</li>)}</ol>
      {!current ? <p className="field-error" role="alert">{text("library_selection_unavailable")}</p> : null}
      <form onSubmit={submit} noValidate><fieldset disabled={!writesEnabled || locked || !current || result !== null}>
        <label htmlFor="blueprint-project-title">{text("blueprints_project_title")}</label><input id="blueprint-project-title" value={title} maxLength={128} onChange={(event) => { setTitle(event.currentTarget.value); setStatus("idle"); setIntent(null); }} />
        <label htmlFor="blueprint-project-brief">{text("blueprints_project_brief")}</label><textarea id="blueprint-project-brief" required rows={5} aria-describedby="blueprint-brief-help" aria-invalid={invalid && !blueprintBriefIsValid(brief)} value={brief} onChange={(event) => { setBrief(event.currentTarget.value); setStatus("idle"); setIntent(null); }} /><p className="small-copy" id="blueprint-brief-help">{text("blueprints_brief_help")} · {[...brief.trim()].length}/4000</p>
        <div className="blueprint-defaults"><h4>{text("blueprints_default_runtime")}</h4><label htmlFor="blueprint-default-profile">{text("role_profile")}</label><select id="blueprint-default-profile" value={defaultProfile} onChange={(event) => { const profileId = event.currentTarget.value; setDefaultRuntime((current) => selectBlueprintProfile(current, profileId)); }}><option value="">{text("select_profile")}</option>{options}</select><label htmlFor="blueprint-default-model">{text("role_model")}</label><input id="blueprint-default-model" value={defaultModel} onChange={(event) => { const model = event.currentTarget.value; setDefaultRuntime((current) => ({ ...current, model: model || null })); }} /><p className="small-copy">{text("blueprints_model_help")}</p><button type="button" className="button button-secondary" disabled={!defaultProfile} onClick={() => { setBindings((current) => current.map((binding) => ({ ...binding, profile_id: defaultProfile, model: defaultModel.trim() || null }))); setStatus("idle"); setIntent(null); }}>{text("blueprints_fill_runtime")}</button></div>
        {bindings.map((binding, index) => <div className="blueprint-binding" key={binding.slot_id}><h4>{selected.slots.find((slot) => slot.id === binding.slot_id)?.name}</h4><p className="small-copy"><code>{selected.slots.find((slot) => slot.id === binding.slot_id)?.role_ref.id}</code></p><label htmlFor={`blueprint-name-${index}`}>{text("role_name")}</label><input id={`blueprint-name-${index}`} value={binding.name} maxLength={128} onChange={(event) => updateBinding(index, { name: event.currentTarget.value })} /><label htmlFor={`blueprint-profile-${index}`}>{text("role_profile")}</label><select id={`blueprint-profile-${index}`} value={binding.profile_id} onChange={(event) => updateBinding(index, { profile_id: event.currentTarget.value })}><option value="">{text("select_profile")}</option>{options}</select><label htmlFor={`blueprint-model-${index}`}>{text("role_model")}</label><input id={`blueprint-model-${index}`} value={binding.model ?? ""} onChange={(event) => updateBinding(index, { model: event.currentTarget.value || null })} /><p className="small-copy">{text("blueprints_model_help")}</p></div>)}
        {invalid ? <p className="field-error" role="alert">{text("blueprints_invalid")}</p> : null}<p className="small-copy">{text("blueprints_apply_help")}</p><button type="submit" className="button button-primary">{text(status === "applying" ? "working" : "blueprints_apply")}</button>
      </fieldset></form>
      {status === "refused" || status === "uncertain" ? <div className="notice" role="alert"><p>{text(status === "uncertain" ? "blueprints_uncertain" : "blueprints_refused")}</p><code>{code}</code>{intent ? <button type="button" className="button button-secondary" disabled={!writesEnabled} onClick={() => void execute(intent)}>{text("blueprints_retry")}</button> : null}</div> : null}
      {result ? <div className="notice" role="status"><p>{text("blueprints_created")}: {result.roles.length} {text("blueprints_roles_count")} · {result.tasks.length} {text("blueprints_tasks_count")}</p><button type="button" className="button button-primary" onClick={() => onApplied(result)}>{text("blueprints_open_work")}</button></div> : null}
    </div> : null}
  </section>;
}
