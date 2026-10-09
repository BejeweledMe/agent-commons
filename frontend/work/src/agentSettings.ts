import { ApiProblem, requestOutcome, type WorkApi } from "./api.js";
import { LibraryApi, parseLibraryRef } from "./libraryApi.js";
import type { LibraryRef, LibrarySave, RoleContent, ServiceLibrary } from "./libraryTypes.js";

export type Grants = Record<"create_roles" | "retire_roles" | "open_links", "deny" | "ask" | "auto">;
export type AgentLink = { id: string; revision: string; from_agent_id: string; to_agent_id: string; allowed_action: "ask" | "handoff_work"; state: string };
export type AgentDetails = {
  schema: "agent_commons.agent-details.v1"; id: string; revision: string; name: string; profile_id: string; model: string | null; state: string;
  context_mode: "fresh" | "accumulated"; lifetime: { kind: string; task_id?: string }; grants: Grants; effective_grants: Grants;
  skills: string[]; tool_allowlist: string[]; turnover_budget: number | null; supervisor_agent_id: string | null; created_by_agent_id: string | null;
  specialization_ref: LibraryRef | null; specialization_state: "none" | "ready" | "unavailable";
  specialization: { ref: LibraryRef; name: string; description: string; methods: { ref: LibraryRef; name: string; kind: string; when: string | null }[] } | null;
  profile_tools: string[]; profile_skills: string[]; live_delegations: string[]; retirement_blockers: string[];
  organization_children: { id: string; name: string }[]; links: AgentLink[]; origin: string; approval: string;
};
const bad = (): never => { throw new ApiProblem(502, { code: "agent_details_invalid", message: "", safeNextActions: [] }); };
const obj = (v: unknown): Record<string, unknown> => v !== null && typeof v === "object" && !Array.isArray(v) ? v as Record<string, unknown> : bad();
const str = (v: unknown): string => typeof v === "string" && !v.includes("[truncated") ? v : bad();
const strings = (v: unknown): string[] => Array.isArray(v) ? v.map(str) : bad();
const nullable = (v: unknown): string | null => v === null ? null : str(v);
const array = (v: unknown): unknown[] => Array.isArray(v) ? v : bad();
function grants(v: unknown): Grants {
  const x = obj(v), result = {} as Grants;
  for (const key of ["create_roles", "retire_roles", "open_links"] as const) { const value = x[key]; if (value !== "deny" && value !== "ask" && value !== "auto") return bad(); result[key] = value; }
  return result;
}
/** Full closed editor buffers only. Never use the lossy generic entity reader. */
export function parseAgentDetails(value: unknown, expectedId: string): AgentDetails {
  if (new TextEncoder().encode(JSON.stringify(value)).length > 512 * 1024) return bad();
  const x = obj(value);
  if (x.schema !== "agent_commons.agent-details.v1" || x.id !== expectedId || !["fresh", "accumulated"].includes(String(x.context_mode)) || !["none", "ready", "unavailable"].includes(String(x.specialization_state))) return bad();
  const specialization = x.specialization === null ? null : obj(x.specialization);
  return {
    schema: "agent_commons.agent-details.v1", id: str(x.id), revision: str(x.revision), name: str(x.name), profile_id: str(x.profile_id), model: nullable(x.model), state: str(x.state),
    context_mode: x.context_mode as "fresh" | "accumulated", lifetime: { kind: str(obj(x.lifetime).kind), ...(obj(x.lifetime).task_id === undefined ? {} : { task_id: str(obj(x.lifetime).task_id) }) },
    grants: grants(x.grants), effective_grants: grants(x.effective_grants), skills: strings(x.skills), tool_allowlist: strings(x.tool_allowlist),
    turnover_budget: x.turnover_budget === null ? null : typeof x.turnover_budget === "number" && Number.isSafeInteger(x.turnover_budget) ? x.turnover_budget : bad(),
    supervisor_agent_id: nullable(x.supervisor_agent_id), created_by_agent_id: nullable(x.created_by_agent_id), origin: str(x.origin), approval: str(x.approval),
    specialization_ref: x.specialization_ref === null ? null : parseLibraryRef(x.specialization_ref, "role"), specialization_state: x.specialization_state as AgentDetails["specialization_state"],
    specialization: specialization === null ? null : { ref: parseLibraryRef(specialization.ref, "role"), name: str(specialization.name), description: str(specialization.description), methods: array(specialization.methods).map((v) => { const m = obj(v); return { ref: parseLibraryRef(m.ref, "skill"), name: str(m.name), kind: str(m.kind), when: nullable(m.when) }; }) },
    profile_tools: strings(x.profile_tools), profile_skills: strings(x.profile_skills), live_delegations: strings(x.live_delegations), retirement_blockers: strings(x.retirement_blockers),
    organization_children: array(x.organization_children).map((v) => { const c = obj(v); return { id: str(c.id), name: str(c.name) }; }),
    links: array(x.links).map((v) => { const l = obj(v); if (l.allowed_action !== "ask" && l.allowed_action !== "handoff_work") return bad(); return { id: str(l.id), revision: str(l.revision), from_agent_id: str(l.from_agent_id), to_agent_id: str(l.to_agent_id), allowed_action: l.allowed_action, state: str(l.state) }; })
  };
}
export type AgentDraft = Pick<AgentDetails, "name" | "context_mode" | "supervisor_agent_id" | "grants" | "tool_allowlist" | "skills" | "turnover_budget">;
export function agentDraft(detail: AgentDetails): AgentDraft { return structuredClone({ name: detail.name, context_mode: detail.context_mode, supervisor_agent_id: detail.supervisor_agent_id, grants: detail.grants, tool_allowlist: detail.tool_allowlist, skills: detail.skills, turnover_budget: detail.turnover_budget }); }
export function agentChanges(detail: AgentDetails, draft: AgentDraft): Record<string, unknown> {
  const initial = agentDraft(detail), changes: Record<string, unknown> = {};
  for (const key of Object.keys(initial) as (keyof AgentDraft)[]) if (JSON.stringify(initial[key]) !== JSON.stringify(draft[key])) changes[key] = draft[key];
  return changes;
}
/**
 * UX-05: the two honest save scopes of this surface and the tabs they cover.
 *
 * `configuration` is one reconfigure request over the draft fields below;
 * `methods` is a new version of a specialized role's own method list. A dirty
 * marker has to name the tab whose fields actually changed, so the map is
 * explicit here rather than guessed from the active tab. The union covers every
 * `AgentDraft` key, so no pending edit can go unmarked.
 */
export type AgentSettingsTab = "general" | "skills" | "permissions" | "connections";
export const AGENT_TAB_FIELDS: Readonly<Record<AgentSettingsTab, readonly (keyof AgentDraft)[]>> = {
  general: ["name", "supervisor_agent_id", "context_mode"],
  permissions: ["grants", "tool_allowlist", "turnover_budget"],
  skills: ["skills"],
  connections: []
};
export function agentTabDirty(
  detail: AgentDetails, draft: AgentDraft, methodsDirty: boolean
): Readonly<Record<AgentSettingsTab, boolean>> {
  const changes = agentChanges(detail, draft);
  const changed = (tab: AgentSettingsTab): boolean => AGENT_TAB_FIELDS[tab].some((field) => field in changes);
  return {
    general: changed("general"),
    permissions: changed("permissions"),
    // With a specialization this tab edits the role's method list; without one
    // it edits exactly the legacy profile skill list in the draft.
    skills: detail.specialization_ref !== null ? methodsDirty : changed("skills"),
    // Links are server operations of their own: there is no draft to be dirty.
    connections: false
  };
}
/** Explain invalid staff permissions before sending a request the server must refuse. */
export function agentBudgetIssue(draft: AgentDraft): "agent_budget_required" | "agent_budget_invalid" | null {
  const budget = draft.turnover_budget;
  if (budget === null) return draft.grants.create_roles !== "deny" || draft.grants.retire_roles !== "deny" ? "agent_budget_required" : null;
  return !Number.isInteger(budget) || budget < 0 || budget > 1024 ? "agent_budget_invalid" : null;
}
/** First edit forks a private identity; later edits retain its exact version and reviewer suffix. */
export function agentMethodFork(agentId: string, base: LibraryRef, content: RoleContent, nonce: string): LibrarySave {
  const prefix = `agent-${agentId.slice(-12).toLowerCase()}-`;
  if (base.source === "custom" && base.id.startsWith(prefix)) return { kind: "role", id: base.id, expected_version: base.version, content: structuredClone(content) };
  const id = `agent-${agentId.slice(-12).toLowerCase()}-${nonce.replace(/[^a-z0-9]/gi, "").slice(0, 12).toLowerCase()}${base.id.endsWith("-reviewer") ? "-reviewer" : ""}`;
  return { kind: "role", id, expected_version: null, fork_ref: base, content: structuredClone(content) };
}
export type AgentOperation = { path: string; body: Record<string, unknown>; key: string; library?: { input: LibrarySave; key: string; savedRef?: LibraryRef }; confirmed: boolean; rebased?: boolean };
export type EditorState = "idle" | "loading" | "saving" | "ready" | "saved" | "failed" | "uncertain" | "saved_refresh_failed";
/** RAM-owned operation/draft lifetime survives closing a modal. Confirmed POSTs never replay during refresh. */
export class AgentEditorSession {
  reason = "";
  linkTarget = "";
  linkKind: "ask" | "handoff_work" = "ask";
  detail: AgentDetails | null = null;
  draft: AgentDraft | null = null;
  methods: RoleContent | null = null;
  catalog: ServiceLibrary | null = null;
  operation: AgentOperation | null = null;
  state: EditorState = "idle";
  errorCode = "";
  methodsUnavailable = false;
  private listeners = new Set<() => void>();
  private version = 0;
  private loading: Promise<void> | null = null;
  private methodsBaseline: RoleContent | null = null;
  private methodsRef: LibraryRef | null = null;
  readonly library: LibraryApi;
  constructor(readonly api: WorkApi, readonly id: string) { this.library = new LibraryApi(api); }
  subscribe = (listener: () => void): (() => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  snapshot = (): number => this.version;
  publish(): void { this.version++; for (const listener of this.listeners) listener(); }
  update(changes: Partial<AgentDraft>): void { if (this.draft && !this.locked) { this.draft = { ...this.draft, ...changes }; if (this.state === "saved") this.state = "ready"; this.publish(); } }
  get locked(): boolean { return this.state === "saving" || this.operation !== null && !this.operation.confirmed; }
  /**
   * UX-05 no-op guards. Each save scope answers for itself whether anything
   * actually changed, so a clean surface can neither issue a reconfigure
   * request nor fork a new library version of an unchanged method list.
   */
  get configDirty(): boolean {
    return !!(this.detail && this.draft && Object.keys(agentChanges(this.detail, this.draft)).length);
  }
  get methodsDirty(): boolean {
    return this.methods !== null && JSON.stringify(this.methods) !== JSON.stringify(this.methodsBaseline);
  }
  get hasDraftChanges(): boolean { return this.configDirty || this.methodsDirty; }
  load(preserveDraft = true): Promise<void> {
    if (this.loading) return this.loading;
    this.loading = this.read(preserveDraft).finally(() => { this.loading = null; }); return this.loading;
  }
  private async read(preserveDraft: boolean): Promise<void> {
    this.state = "loading"; this.publish();
    try {
      const detail = parseAgentDetails(await this.api.requestData(`/work/agents/${encodeURIComponent(this.id)}`, { signal: new AbortController().signal }), this.id);
      const edits = preserveDraft && this.detail && this.draft ? agentChanges(this.detail, this.draft) : {};
      const committed = this.operation?.confirmed && !this.operation.rebased ? this.operation : null;
      // A link commits no editor fields. A settings save acknowledges only its
      // exact submitted values; later or unrelated edits remain private drafts.
      if (committed && !committed.library && committed.path.startsWith("/agents/")) {
        for (const [key, value] of Object.entries(committed.body.changes ?? {})) {
          if (this.draft && JSON.stringify(this.draft[key as keyof AgentDraft]) === JSON.stringify(value)) delete edits[key];
        }
      }
      this.detail = detail;
      this.draft = { ...agentDraft(detail), ...edits };
      if (committed?.library) this.methodsBaseline = structuredClone(committed.library.input.content as RoleContent);
      const methodsDirty = this.methods !== null && JSON.stringify(this.methods) !== JSON.stringify(this.methodsBaseline);
      const methodsChanged = JSON.stringify(detail.specialization_ref) !== JSON.stringify(this.methodsRef);
      if (detail.specialization_ref && (!preserveDraft || !this.methods || !methodsDirty && methodsChanged)) {
        try {
          const [entry, catalog] = await Promise.all([this.library.detail(detail.specialization_ref, new AbortController().signal), this.library.load(new AbortController().signal)]);
          if (!("entry_skill" in entry.content)) throw new Error("role required");
          this.methods = structuredClone(entry.content); this.methodsBaseline = structuredClone(entry.content); this.methodsRef = detail.specialization_ref;
          this.catalog = catalog; this.methodsUnavailable = false;
        } catch { this.methodsUnavailable = true; }
      }
      if (committed) committed.rebased = true;
      this.state = this.operation?.confirmed ? "saved" : "ready";
    } catch (error) { this.state = this.operation?.confirmed ? "saved_refresh_failed" : "failed"; this.errorCode = error instanceof ApiProblem ? error.apiError?.code ?? "unknown" : "unknown"; }
    this.publish();
  }
  start(operation: AgentOperation): Promise<void> {
    if (this.locked) return Promise.resolve();
    // A clean editor must not create a library version or send an empty patch.
    // Retry bypasses this check and retains the original frozen operation.
    if (operation.library && JSON.stringify(operation.library.input.content) === JSON.stringify(this.methodsBaseline)) return Promise.resolve();
    if (!operation.library && operation.path === `/agents/${this.id}/reconfigure`
      && operation.body.changes && Object.keys(operation.body.changes).length === 0) return Promise.resolve();
    this.operation = structuredClone(operation); return this.retry();
  }
  async retry(): Promise<void> {
    const operation = this.operation; if (!operation || this.state === "saving") return;
    if (operation.confirmed) { await this.load(true); return; }
    this.state = "saving"; this.publish();
    try {
      if (operation.library && !operation.library.savedRef) {
        operation.library.savedRef = (await this.library.save(operation.library.input, operation.library.key, new AbortController().signal)).ref;
      }
      const body = operation.library ? { ...operation.body, changes: { specialization_ref: operation.library.savedRef } } : operation.body;
      const result = await this.api.writeAgentSettings(operation.path, { ...body, idempotency_key: operation.key }, new AbortController().signal);
      const receipt = obj(result), entity = obj(receipt.entity_ref);
      if (typeof receipt.event_id !== "string" || typeof entity.id !== "string" ||
        (operation.path.startsWith("/agents/") && (entity.kind !== "agent" || entity.id !== this.id)) ||
        (operation.path.startsWith("/agent-links") && entity.kind !== "agent_link")) bad();
      operation.confirmed = true; this.state = "saved"; this.publish();
    } catch (error) {
      this.state = requestOutcome(error) === "uncertain" ? "uncertain" : "failed";
      this.errorCode = error instanceof ApiProblem ? error.apiError?.code ?? "unknown" : "unknown"; this.publish(); return;
    }
    await this.load(true);
  }
  /** Explicitly abandon a definite refusal, preserving the unsaved draft for editing. */
  editAfterRefusal(): void { if (this.state !== "failed") return; this.operation = null; this.state = "ready"; this.publish(); }
}
