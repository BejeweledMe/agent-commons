import type { ProviderAvailability } from "./contracts.js";
export type EnvironmentState = "present" | "missing" | "unavailable" | "invalid";
export type ProjectEnvironment = Readonly<{
  state: "observed" | "attention" | "unknown"; buildVerified: false;
  manifests: readonly { name: string; state: EnvironmentState }[];
  lockfiles: readonly { name: string; state: EnvironmentState }[];
  dependencies: readonly { name: string; state: EnvironmentState }[];
  tools: readonly { name: string; availability: "available" | "unavailable"; required: string | null; observed: string | null; matches: boolean | null }[];
}>;
const fail = (): never => { throw new Error("project_environment_unavailable"); };
const row = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : fail();
function observations(value: unknown, names: readonly string[], key = "name", invalid = false): { name: string; state: EnvironmentState }[] {
  if (!Array.isArray(value) || value.length > names.length) return fail();
  const seen = new Set<string>();
  return value.map((item) => { const r = row(item), name = String(r[key]);
    if (!names.includes(name) || seen.has(name) || !(invalid ? ["present", "missing", "unavailable", "invalid"] : ["present", "missing", "unavailable"]).includes(String(r.state))) return fail();
    seen.add(name); return { name, state: r.state as EnvironmentState };
  });
}
export function parseProjectEnvironment(value: unknown): ProjectEnvironment {
  const r = row(value);
  if (r.schema !== "agent_commons.project-environment.v1" || r.build_verified !== false || !["observed", "attention", "unknown"].includes(String(r.state))
    || !Array.isArray(r.tools) || r.tools.length > 2 || !Array.isArray(r.limitations) || r.limitations.length !== 2 || !r.limitations.includes("presence_only") || !r.limitations.includes("root_manifests_only")) return fail();
  const version = (v: unknown): string | null => v === null ? null : typeof v === "string" && /^\d{1,4}(?:\.\d{1,4}){0,2}$/.test(v) ? v : fail();
  return {
    state: r.state as ProjectEnvironment["state"], buildVerified: false,
    manifests: observations(r.manifests, ["package.json", "pyproject.toml", "requirements.txt"], "name", true),
    lockfiles: observations(r.lockfiles, ["package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock", "bun.lockb", "uv.lock", "poetry.lock", "Pipfile.lock"]),
    dependencies: observations(r.dependencies, ["node", "python"], "ecosystem"),
    tools: r.tools.map((item) => { const tool = row(item);
      if (!["node", "python"].includes(String(tool.name)) || !["available", "unavailable"].includes(String(tool.availability)) || (tool.version_matches !== null && typeof tool.version_matches !== "boolean")) return fail();
      return { name: String(tool.name), availability: tool.availability as "available" | "unavailable", required: version(tool.required_version), observed: version(tool.observed_version), matches: tool.version_matches as boolean | null };
    })
  };
}
const PROFILES = new Set(["codex-builder", "codex-independent-reviewer", "claude-builder", "claude-independent-reviewer", "grok-builder", "grok-independent-reviewer"]);
/** Fixed profile identifiers only. This is an explicit CLI action, never an HTTP launch. */
export function qualificationCommand(availability: Pick<ProviderAvailability, "profileId" | "refusal">): string | null {
  return PROFILES.has(availability.profileId) && availability.refusal?.remediation.some((action) => ["run_provider_canary", "rerun_provider_canary"].includes(action))
    ? `uv run agent-commons --state-root "/absolute/path/to/panel-state-root" broker canary --profile ${availability.profileId} --profile-config "/absolute/path/to/panel-runtime.yaml" --confirm-provider-run --wall-time-seconds 300` : null;
}
