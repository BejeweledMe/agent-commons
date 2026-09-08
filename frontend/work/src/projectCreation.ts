import { ApiProblem } from "./api.js";
import type { ProjectInspection } from "./projectWorkspace.js";
export type FolderSelection = Readonly<{ status: "cancelled"; path: null; name: null } | { status: "selected"; path: string; name: string }>;
export type FolderPurpose = "parent" | "existing";
export function absoluteFolder(value: unknown): value is string { return typeof value === "string" && new TextEncoder().encode(value).length <= 4096 && !/[\u0000-\u001f\u007f]/.test(value) && (value.startsWith("/") || /^[a-zA-Z]:\\/.test(value)); }
export function folderInput(value: string): boolean {
  const shorthand = new TextEncoder().encode(value).length <= 4096 && !/[\u0000-\u001f\u007f\\]/.test(value) && (value === "~" || (value.startsWith("~/") && !value.startsWith("~//") && !value.slice(2).split("/").includes("..")));
  return shorthand || absoluteFolder(value);
}
export function inferredProjectName(path: string): string { return [...(path.replace(/[\\/]+$/, "").split(/[\\/]/).at(-1) ?? "")].slice(0, 160).join(""); }
export function parseFolderSelection(value: unknown): FolderSelection {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new ApiProblem(502, null);
  const row = value as Record<string, unknown>;
  if (Object.keys(row).sort().join(",") !== "name,path,schema,status" || row.schema !== "agent_commons.project_folder_selection.v1") throw new ApiProblem(502, null);
  if (row.status === "cancelled" && row.path === null && row.name === null) return { status: "cancelled", path: null, name: null };
  if (row.status !== "selected" || !absoluteFolder(row.path) || typeof row.name !== "string" || !row.name.trim() || [...row.name].length > 160 || /[\u0000-\u001f\u007f]/.test(row.name)) throw new ApiProblem(502, null);
  return { status: "selected", path: row.path, name: row.name };
}
export type CreateMode = "new" | "existing";
export type CreationProblem = "path" | "name" | "folderName" | "picker" | "inspect" | "uncertain" | "ambiguous" | "expired" | null;
export type CreationSnapshot = Readonly<{ open: boolean; mode: CreateMode; name: string; location: string; manual: boolean; inspection: ProjectInspection | null; consent: boolean; busy: "picker" | "inspect" | "create" | null; problem: CreationProblem; uncertain: boolean }>;
export function targetPath(value: Pick<CreationSnapshot, "mode" | "location" | "name">): string {
  if (value.mode === "existing") return value.location.trim();
  const parent = value.location.trim(), separator = /^[a-zA-Z]:\\/.test(parent) ? "\\" : "/";
  return `${parent.replace(/[\\/]+$/, "")}${separator}${value.name.trim()}`;
}
export function validateProjectDraft(value: Pick<CreationSnapshot, "mode" | "location" | "name">): CreationProblem {
  const name = value.name.trim();
  if (!name || [...name].length > 160 || /[\u0000-\u001f\u007f]/.test(name)) return "name";
  if (!folderInput(value.location.trim())) return "path";
  if (value.mode === "new" && (name === "." || name === ".." || /[\\/:<>"|?*]/.test(name) || new TextEncoder().encode(name).length > 255)) return "folderName";
  return null;
}
/** Keeps selected paths and uncertain creation in this Work window's RAM only. */
export class ProjectCreation {
  private value: CreationSnapshot = { open: false, mode: "new", name: "", location: "", manual: false, inspection: null, consent: false, busy: null, problem: null, uncertain: false };
  private listeners = new Set<() => void>();
  private request: AbortController | null = null;
  private generation = 0;
  private nameEdited = false;
  snapshot = (): CreationSnapshot => this.value;
  subscribe = (listener: () => void): (() => void) => { this.listeners.add(listener); return () => this.listeners.delete(listener); };
  private set(next: Partial<CreationSnapshot>): void { this.value = { ...this.value, ...next }; for (const listener of this.listeners) listener(); }
  private get editable(): boolean { return !this.value.busy && !this.value.uncertain; }
  show(): void { this.set({ open: true }); }
  close(): void { if (this.value.busy !== "create") { this.generation++; this.request?.abort(); this.set({ busy: null }); } this.set({ open: false }); }
  setMode(mode: CreateMode): void { if (this.editable && mode !== this.value.mode) { this.nameEdited = false; this.set({ mode, name: "", location: "", inspection: null, consent: false, problem: null }); } }
  setName(name: string): void { if (this.editable) { this.nameEdited = true; this.set({ name, inspection: null, consent: false, problem: null }); } }
  setLocation(location: string): void { if (this.editable) this.set({ location, name: this.value.mode === "existing" && !this.nameEdited ? inferredProjectName(location) : this.value.name, inspection: null, consent: false, problem: null }); }
  setManual(): void { if (this.editable) this.set({ manual: true }); }
  setConsent(consent: boolean): void { if (this.editable) this.set({ consent }); }
  back(): void { if (this.editable) this.set({ inspection: null, consent: false, problem: null }); }
  async pick(select: (purpose: FolderPurpose, signal: AbortSignal) => Promise<FolderSelection>): Promise<void> {
    if (!this.editable) return;
    const generation = ++this.generation, controller = new AbortController(); this.request = controller; this.set({ busy: "picker", problem: null });
    try {
      const result = await select(this.value.mode === "new" ? "parent" : "existing", controller.signal);
      if (generation !== this.generation || controller.signal.aborted || result.status === "cancelled") return;
      this.set({ location: result.path, name: this.value.mode === "existing" && !this.nameEdited ? result.name : this.value.name, inspection: null, consent: false });
    } catch { if (generation === this.generation && !controller.signal.aborted) this.set({ manual: true, problem: "picker" }); }
    finally { if (generation === this.generation) this.set({ busy: null }); }
  }
  async inspect(check: (input: { mode: CreateMode; path: string; name: string }) => Promise<ProjectInspection>): Promise<void> {
    if (!this.editable) return;
    const problem = validateProjectDraft(this.value); if (problem) { this.set({ problem }); return; }
    const generation = ++this.generation, input = { mode: this.value.mode, path: targetPath(this.value), name: this.value.name.trim() };
    this.set({ busy: "inspect", problem: null });
    try { const inspection = await check(input); if (generation === this.generation) { if (inspection.mode !== input.mode || inspection.name !== input.name) throw new ApiProblem(502, null); this.set({ inspection, consent: false }); } }
    catch { if (generation === this.generation) this.set({ problem: "inspect" }); }
    finally { if (generation === this.generation) this.set({ busy: null }); }
  }
  get canCreate(): boolean { return !this.value.busy && this.value.inspection !== null && (this.value.uncertain || this.value.mode === "new" || !this.value.inspection.initializationRequired || this.value.consent); }
  async create(commit: (inspection: ProjectInspection) => Promise<void>): Promise<void> {
    if (!this.canCreate) return;
    const inspection = this.value.inspection!, path = targetPath(this.value);
    this.set({ busy: "create", problem: null });
    try {
      await commit(inspection);
      this.nameEdited = false;
      this.set({ open: false, inspection: null, name: "", location: "", consent: false, problem: null, uncertain: false });
    } catch (error) {
      const code = error instanceof ApiProblem ? error.apiError?.code : null;
      if (code === "project_initialization_ambiguous") { this.nameEdited = true; this.set({ inspection: null, mode: "existing", location: path, consent: false, problem: "ambiguous", uncertain: false }); }
      else if (code === "project_inspection_expired") this.set({ inspection: null, consent: false, problem: "expired", uncertain: false });
      else this.set({ uncertain: true, problem: "uncertain" });
    } finally { this.set({ busy: null }); }
  }
}
