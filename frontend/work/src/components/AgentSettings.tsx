import { useEffect, useState, useSyncExternalStore, type ReactElement } from "react";
import { AgentEditorSession, agentBudgetIssue, agentChanges, agentMethodFork, agentTabDirty, type AgentOperation, type Grants } from "../agentSettings.js";
import type { RoleOption } from "../contracts.js";
import type { MessageKey } from "../i18n.js";
import { libraryRefKey, type LibraryRef } from "../libraryTypes.js";
import { Modal, ModalTabs } from "./Modal.js";
import { IconButton } from "./Icon.js";

export type AgentTab = "general" | "skills" | "permissions" | "connections";
type Props = { session: AgentEditorSession; initialTab: AgentTab; text: (key: MessageKey) => string; roles: readonly RoleOption[]; writesEnabled: boolean; onClose: () => void; onChanged: () => void; };
export function AgentSettings({ session, initialTab, text, roles, writesEnabled, onClose, onChanged }: Props): ReactElement {
  useSyncExternalStore(session.subscribe, session.snapshot, session.snapshot);
  const [tab, setTab] = useState<AgentTab>(initialTab);
  const [chooseSkills, setChooseSkills] = useState(false);
  const [skillSearch, setSkillSearch] = useState("");
  const reason = session.reason, target = session.linkTarget, linkKind = session.linkKind;
  const setReason = (value: string): void => { session.reason = value; session.publish(); };
  const setTarget = (value: string): void => { session.linkTarget = value; session.publish(); };
  const setLinkKind = (value: "ask" | "handoff_work"): void => { session.linkKind = value; session.publish(); };
  useEffect(() => { if (!session.detail) void session.load(); }, [session]);
  const d = session.detail, draft = session.draft, methods = session.methods;
  const disabled = !writesEnabled || session.locked || d?.state !== "active" || session.state === "loading";
  const budgetIssue = draft ? agentBudgetIssue(draft) : null;
  const name = (id: string | null): string => id === null ? text("agent_no_supervisor") : roles.find((r) => r.id === id)?.name ?? text("agent_other_agent");
  async function run(operation: AgentOperation): Promise<void> { await session.start(operation); if (operation.confirmed || session.operation?.confirmed) onChanged(); }
  async function retry(): Promise<void> { await session.retry(); if (session.operation?.confirmed) onChanged(); }
  function configure(): void {
    if (!d || !draft || disabled || budgetIssue) return;
    const changes = agentChanges(d, draft); if (!Object.keys(changes).length) return;
    void run({ path: `/agents/${d.id}/reconfigure`, body: { expected_revision: d.revision, changes, reason: reason.trim() || text("agent_change_reason"), ...(d.context_mode === "fresh" && draft.context_mode === "accumulated" ? { isolation_downgrade_reason: reason.trim() } : {}) }, key: crypto.randomUUID(), confirmed: false });
  }
  function saveMethods(): void {
    if (!d?.specialization_ref || !methods || !session.methodsDirty || methodsInvalid || disabled || d.retirement_blockers.length || !session.catalog?.editingEnabled) return;
    const key = crypto.randomUUID();
    void run({ path: `/agents/${d.id}/reconfigure`, body: { expected_revision: d.revision, reason: reason.trim() || text("agent_methods_reason") }, key: `${key}-bind`, library: { key: `${key}-library`, input: agentMethodFork(d.id, d.specialization_ref, methods, key) }, confirmed: false });
  }
  function updateMethods(change: Partial<NonNullable<typeof methods>>): void { if (methods && !disabled) { session.methods = { ...methods, ...change }; if (session.state === "saved") session.state = "ready"; session.publish(); } }
  const methodOptions = [...(session.catalog?.skills ?? [])];
  // Exact historical refs may no longer be catalog heads. Keep them selectable.
  for (const item of d?.specialization?.methods ?? []) if (!methodOptions.some((s) => libraryRefKey(s.ref) === libraryRefKey(item.ref))) methodOptions.push({ ref: item.ref, name: item.name, description: "" });
  const refFor = (key: string): LibraryRef | undefined => methodOptions.find((s) => libraryRefKey(s.ref) === key)?.ref;
  const methodsInvalid = methods !== null && (methods.core_skills.length > 8 || methods.conditional_skills.some((route) => !route.when.trim()) || (() => {
    const selected = [methods.entry_skill, ...methods.core_skills, ...methods.conditional_skills.map((route) => route.ref)];
    const versions = new Map<string, string>();
    for (const ref of selected) { const key = libraryRefKey(ref); if (versions.has(ref.id) && versions.get(ref.id) !== key) return true; versions.set(ref.id, key); }
    return false;
  })());
  const dirtyTabs = d && draft ? agentTabDirty(d, draft, session.methodsDirty) : null;
  const savingMethods = tab === "skills" && d?.specialization_ref != null;
  const scopeDirty = savingMethods ? session.methodsDirty : session.configDirty;
  const saveDisabled = disabled || !scopeDirty || (savingMethods
    ? methodsInvalid || !methods || session.methodsUnavailable || (d?.retirement_blockers.length ?? 0) > 0 || !session.catalog?.editingEnabled
    : budgetIssue !== null || d?.context_mode === "fresh" && draft?.context_mode === "accumulated" && !reason.trim());
  const options = methodOptions.map((s) => <option key={libraryRefKey(s.ref)} value={libraryRefKey(s.ref)}>{s.name}</option>);
  return <Modal title={d?.name ?? text("agent_settings")} closeLabel={text("shell_close")} onClose={onClose} className="agent-settings" size="medium"
    toolbar={<ModalTabs label={text("agent_settings")} value={tab} onChange={setTab} tabs={(["general", "skills", "permissions", "connections"] as const).map((id) => ({ id, label: `${text(`agent_tab_${id}`)}${dirtyTabs?.[id] ? ` · ${text("agent_tab_dirty")}` : ""}` }))} />}
    footer={d && draft ? <div className="agent-settings-footer">
      <details className="agent-reason" open={d.context_mode === "fresh" && draft.context_mode === "accumulated" || undefined}><summary>{text("agent_reason")}</summary><label className="agent-setting-field"><span className="sr-only">{text("agent_reason")}</span><input value={reason} disabled={disabled} onChange={(e) => setReason(e.target.value)} /></label></details>
      {tab !== "connections" ? <p className="small-copy">{text(savingMethods ? "agent_methods_scope" : "agent_config_scope")}</p> : null}
      <div className="agent-settings-actions">{tab !== "connections" ? <button type="button" className="button button-primary" disabled={saveDisabled} title={!scopeDirty ? text("agent_nothing_to_save") : undefined} onClick={savingMethods ? saveMethods : configure}>{text(savingMethods ? "agent_save_methods" : "agent_save_config")}</button> : null}
        <button type="button" className="button button-secondary" disabled={session.locked} title={text("agent_reload_keep_help")} onClick={() => void session.load(true)}>{text("agent_reload_keep")}</button>
      </div>
    </div> : undefined}>

    <div role="status" aria-live="polite">
      {session.state === "loading" || session.state === "saving" ? <p>{text(session.state === "loading" ? "agent_loading" : "working")}</p> : null}
      {session.state === "saved" ? <p className="notice">{text(session.hasDraftChanges ? "agent_saved_with_drafts" : "agent_saved")}</p> : null}
      {["failed", "uncertain", "saved_refresh_failed"].includes(session.state) ? <div className="notice" role="alert"><p>{text(session.state === "uncertain" ? "agent_uncertain" : session.state === "saved_refresh_failed" ? "agent_saved_refresh_failed" : "agent_failed")}</p>
        {session.operation ? <button type="button" className="button button-secondary" onClick={() => void retry()}>{text(session.operation.confirmed ? "refresh_status" : "agent_retry_same")}</button> : <button type="button" className="button button-secondary" onClick={() => void session.load()}>{text("refresh_status")}</button>}
        {session.state === "failed" && session.operation ? <button type="button" className="button button-secondary" onClick={() => session.editAfterRefusal()}>{text("agent_edit_refused")}</button> : null}
        <details><summary>{text("shell_technical_details")}</summary><code>{session.errorCode}</code></details>
      </div> : null}
    </div>
    {d && draft ? <div role="tabpanel" id={`modal-panel-${tab}`} aria-labelledby={`modal-tab-${tab}`}>
      {tab === "general" ? <>
        <div className="agent-settings-grid">
          <label>{text("role_name")}<input value={draft.name} maxLength={200} disabled={disabled} onChange={(e) => session.update({ name: e.target.value })} /></label>
          <label>{text("agent_supervisor")}<select disabled={disabled} value={draft.supervisor_agent_id ?? ""} onChange={(e) => session.update({ supervisor_agent_id: e.target.value || null })}><option value="">{text("agent_no_supervisor")}</option>{draft.supervisor_agent_id && !roles.some((r) => r.id === draft.supervisor_agent_id) ? <option value={draft.supervisor_agent_id}>{text("agent_current_supervisor_unavailable")}</option> : null}{roles.filter((r) => r.id !== d.id).map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</select></label>
          <label>{text("agent_context")}<select disabled={disabled} value={draft.context_mode} onChange={(e) => session.update({ context_mode: e.target.value as "fresh" | "accumulated" })}><option value="fresh">{text("agent_context_fresh_short")}</option><option value="accumulated">{text("agent_context_accumulated_short")}</option></select></label>
          <div><span className="small-copy">{text("agent_model_fixed")}</span><p>{d.model ?? text("agent_profile_default")} · {d.profile_id}</p></div>
        </div>
        <p className="small-copy">{text("agent_supervisor_help")}</p>
        <dl className="agent-facts"><div><dt>{text("agent_creator")}</dt><dd>{d.created_by_agent_id ? name(d.created_by_agent_id) : text("agent_human")}</dd></div><div><dt>{text("agent_children")}</dt><dd>{d.organization_children.map((c) => c.name).join(", ") || text("agent_none")}</dd></div></dl>
        {d.context_mode === "fresh" && draft.context_mode === "accumulated" ? <p className="notice">{text("agent_context_warning")}</p> : null}
      </> : null}
      {tab === "permissions" ? <>
        <p className="small-copy">{text("agent_grants_help")}</p>
        <div className="agent-settings-grid">{(["create_roles", "retire_roles", "open_links"] as const).map((key) => <label key={key}>{text(`agent_grant_${key}`)}<select value={draft.grants[key]} disabled={disabled} onChange={(e) => session.update({ grants: { ...draft.grants, [key]: e.target.value as Grants[typeof key] } })}>{(["deny", "ask", "auto"] as const).map((level) => <option key={level} value={level}>{text(`agent_grant_${level}`)}</option>)}</select><span className="small-copy">{text("agent_effective")}: {text(`agent_grant_${d.effective_grants[key]}`)}</span></label>)}
          <label>{text("agent_turnover")}<input type="number" min={0} max={1024} value={draft.turnover_budget ?? ""} disabled={disabled} onChange={(e) => session.update({ turnover_budget: e.target.value === "" ? null : Number(e.target.value) })} /></label>
        </div>
        {budgetIssue ? <p className="notice" role="alert">{text(budgetIssue)}</p> : null}
        <h3>{text("agent_tools")}</h3><p className="small-copy">{text("agent_tools_help")}</p>
        <label><input type="checkbox" checked={draft.tool_allowlist.length === 0} disabled={disabled} onChange={(e) => session.update({ tool_allowlist: e.target.checked ? [] : [...d.profile_tools] })} /> {text("agent_profile_tools")}</label>
        {draft.tool_allowlist.length > 0 ? <ul className="agent-checks">{[...new Set([...d.profile_tools, ...draft.tool_allowlist])].map((tool) => <li key={tool}><label><input type="checkbox" disabled={disabled || draft.tool_allowlist.length === 1 && draft.tool_allowlist.includes(tool) || tool === "commons_read_skill" && d.specialization_ref !== null} checked={draft.tool_allowlist.includes(tool)} onChange={(e) => session.update({ tool_allowlist: e.target.checked ? [...draft.tool_allowlist, tool] : draft.tool_allowlist.filter((v) => v !== tool) })} /><code>{tool}</code></label></li>)}</ul> : null}
      </> : null}
      {tab === "skills" ? d.specialization_ref ? <>
        <p>{d.specialization?.description}</p>{methodsInvalid ? <p className="notice">{text("agent_methods_invalid")}</p> : null}<p className="small-copy">{text("agent_methods_help")}</p>
        {d.retirement_blockers.length > 0 ? <p className="notice">{text("agent_methods_busy")}</p> : null}
        {session.methodsUnavailable || !methods ? <p className="notice">{text("agent_methods_unavailable")}</p> : <fieldset disabled={disabled || d.retirement_blockers.length > 0 || !session.catalog?.editingEnabled}>
          <label className="agent-setting-field">{text("agent_entry_skill")}<select value={libraryRefKey(methods.entry_skill)} onChange={(e) => { const ref = refFor(e.target.value); if (ref) updateMethods({ entry_skill: ref }); }}>{options}</select></label>
          <div className="agent-skill-heading"><h3>{text("agent_core_skills")}</h3><button type="button" className="button button-secondary" aria-expanded={chooseSkills} onClick={() => setChooseSkills(!chooseSkills)}>{text(chooseSkills ? "agent_selected_skills" : "agent_choose_skills")}</button></div>{chooseSkills ? <label className="agent-setting-field"><span className="sr-only">{text("agent_search_skills")}</span><input type="search" placeholder={text("agent_search_skills")} value={skillSearch} onChange={(e) => setSkillSearch(e.target.value)} /></label> : null}<ul className="agent-checks">{methodOptions.filter((skill) => chooseSkills ? skill.name.toLowerCase().includes(skillSearch.trim().toLowerCase()) : methods.core_skills.some((ref) => libraryRefKey(ref) === libraryRefKey(skill.ref))).map((skill) => { const key = libraryRefKey(skill.ref); return <li key={key}><label><input type="checkbox" checked={methods.core_skills.some((ref) => libraryRefKey(ref) === key)} onChange={(e) => updateMethods({ core_skills: e.target.checked ? [...methods.core_skills, skill.ref] : methods.core_skills.filter((ref) => libraryRefKey(ref) !== key) })} />{skill.name}</label></li>; })}</ul>
          <h3>{text("agent_conditional_skills")}</h3><ul className="agent-methods-list">{methods.conditional_skills.map((route, index) => <li className="agent-method-route" key={index}><select aria-label={text("agent_skill")} value={libraryRefKey(route.ref)} onChange={(e) => { const ref = refFor(e.target.value); if (ref) updateMethods({ conditional_skills: methods.conditional_skills.map((v, i) => i === index ? { ...v, ref } : v) }); }}>{options}</select><textarea rows={3} aria-label={text("agent_skill_when")} value={route.when} maxLength={4000} onChange={(e) => updateMethods({ conditional_skills: methods.conditional_skills.map((v, i) => i === index ? { ...v, when: e.target.value } : v) })} /><IconButton icon="close" label={text("agent_remove_skill")} onClick={() => updateMethods({ conditional_skills: methods.conditional_skills.filter((_, i) => i !== index) })} /></li>)}</ul>
          <button type="button" className="button button-secondary" disabled={methods.conditional_skills.length >= 32 || !methodOptions.length} onClick={() => updateMethods({ conditional_skills: [...methods.conditional_skills, { ref: methodOptions[0].ref, when: "" }] })}>{text("agent_add_skill")}</button>
        </fieldset>}
        <details className="inspector-technical"><summary>{text("agent_exact_methods")}</summary><p><code>{libraryRefKey(d.specialization_ref)}</code></p>{d.specialization?.methods.map((m, i) => <p key={i}>{m.name} · <code>{libraryRefKey(m.ref)}</code></p>)}</details>
      </> : <ul className="agent-checks">{[...new Set([...d.profile_skills, ...draft.skills])].map((skill) => <li key={skill}><label><input type="checkbox" disabled={disabled} checked={draft.skills.includes(skill)} onChange={(e) => session.update({ skills: e.target.checked ? [...draft.skills, skill] : draft.skills.filter((v) => v !== skill) })} />{skill}</label></li>)}</ul> : null}
      {tab === "connections" ? <>
        <div className="agent-relationship-help"><span>{text("agent_supervisor")}: {name(d.supervisor_agent_id)}</span><button type="button" className="button button-secondary" onClick={() => setTab("general")}>{text("agent_edit_supervisor")}</button></div><p className="small-copy">{text("agent_link_help")}</p>{d.links.length === 0 ? <p className="small-copy">{text("agent_no_links")}</p> : null}<ul className="agent-links">{d.links.map((link) => <li key={link.id}><span>{name(link.from_agent_id)} → {name(link.to_agent_id)} · {text(link.allowed_action === "ask" ? "agent_link_ask" : "agent_link_handoff")}</span><IconButton icon="close" label={text("agent_close_link")} disabled={disabled} onClick={() => void run({ path: `/agent-links/${link.id}/close`, body: { expected_revision: link.revision, reason: reason.trim() || text("agent_change_reason") }, key: crypto.randomUUID(), confirmed: false })} /></li>)}</ul>
        <h3>{text("agent_open_link")}</h3><div className="agent-settings-grid"><label>{text("agent_link_target")}<select value={target} disabled={disabled} onChange={(e) => setTarget(e.target.value)}><option value="">{text("agent_choose")}</option>{roles.filter((r) => r.id !== d.id).map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</select></label><label>{text("agent_link_type")}<select value={linkKind} disabled={disabled} onChange={(e) => setLinkKind(e.target.value as typeof linkKind)}><option value="ask">{text("agent_link_ask")}</option><option value="handoff_work">{text("agent_link_handoff")}</option></select></label></div>
        {linkKind === "handoff_work" ? <p className="notice">{text("agent_link_handoff_warning")}</p> : null}
        <button type="button" className="button button-secondary" disabled={disabled || !target} onClick={() => void run({ path: "/agent-links", body: { from_agent_id: d.id, to_agent_id: target, allowed_action: linkKind, reason: reason.trim() || text("agent_change_reason") }, key: crypto.randomUUID(), confirmed: false })}>{text("agent_open_link")}</button>
      </> : null}

    </div> : null}
  </Modal>;
}
