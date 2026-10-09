import { ApiProblem } from "./api.js";
import type { ApiError } from "./contracts.js";

/**
 * The operator's remaining launch allowance for one profile.
 *
 * `provider_units` are reserved process launches, not money: nothing in this
 * product measures spend, so nothing here may be shown as a cost. An
 * unreadable or absent budget stays unknown — a count is never invented, and
 * the client never raises a limit.
 */
export const LAUNCH_BUDGET_SCHEMA = "agent_commons.launch-budget.v1";
export const BUDGET_EXHAUSTED_CODE = "operator_budget_exhausted";

export type LaunchBudget = Readonly<{
  profileId: string;
  provider: string;
  unit: "provider_units";
  limit: number;
  used: number;
  remaining: number;
  exhausted: boolean;
}>;

const invalid = (): never => { throw new ApiProblem(502, null); };

function count(row: Record<string, unknown>, key: string): number {
  const value = row[key];
  if (!Number.isSafeInteger(value) || Number(value) < 0 || Number(value) > 1_000_000) return invalid();
  return Number(value);
}

function bounded(value: unknown, pattern: RegExp): string {
  if (typeof value !== "string" || !pattern.test(value)) return invalid();
  return value;
}

export function parseLaunchBudget(value: unknown, expectedProfileId?: string): LaunchBudget {
  if (!value || typeof value !== "object" || Array.isArray(value)) return invalid();
  const row = value as Record<string, unknown>;
  if (row.schema !== LAUNCH_BUDGET_SCHEMA || row.unit !== "provider_units") return invalid();
  const profileId = bounded(row.profile_id, /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/);
  if (expectedProfileId !== undefined && profileId !== expectedProfileId) return invalid();
  const limit = count(row, "limit");
  const used = count(row, "used");
  const remaining = count(row, "remaining");
  if (typeof row.exhausted !== "boolean") return invalid();
  // The server owns the arithmetic; a self-contradictory answer is unreadable
  // rather than quietly corrected into a reassuring number.
  if (used > limit || remaining > limit || used + remaining !== limit || row.exhausted !== (remaining === 0)) return invalid();
  return {
    profileId,
    provider: bounded(row.provider, /^[a-z][a-z0-9_-]{0,63}$/),
    unit: "provider_units",
    limit,
    used,
    remaining,
    exhausted: row.exhausted
  };
}

/**
 * The budget a refused launch reported. The precheck answers 409 with
 * `operator_budget_exhausted` before any attempt or child session exists, so
 * this is a refusal to show beside the control, never a run.
 */
export function launchBudgetFromError(error: ApiError | null | undefined): LaunchBudget | null {
  if (!error || error.code !== BUDGET_EXHAUSTED_CODE) return null;
  const budget = (error as { budget?: unknown }).budget;
  try {
    return parseLaunchBudget(budget);
  } catch {
    // The refusal is real even when its detail is unreadable; the caller shows
    // the typed refusal without a fabricated count.
    return null;
  }
}

export function budgetExhaustedRefusal(error: unknown): boolean {
  return error instanceof ApiProblem && error.status === 409 && error.apiError?.code === BUDGET_EXHAUSTED_CODE;
}

/** What a profile's budget is at any moment, including "not readable". */
export type LaunchBudgetState =
  | { kind: "unknown" }
  | { kind: "loading" }
  | { kind: "ready"; budget: LaunchBudget }
  | { kind: "unavailable" };

export function budgetOf(state: LaunchBudgetState): LaunchBudget | null {
  return state.kind === "ready" ? state.budget : null;
}
