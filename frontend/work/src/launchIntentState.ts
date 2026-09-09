import type { MessageKey } from "./i18n.js";

// The run time limit is chosen per attempt, in minutes, and sent as seconds.
// These bounds mirror the server contract (60…3600 seconds) so the form can
// refuse locally; the server bound stays authoritative on every request.
export const MIN_RUN_LIMIT_MINUTES = 1;
export const MAX_RUN_LIMIT_MINUTES = 60;
export const DEFAULT_RUN_LIMIT_MINUTES = 10;

export type RunDraft = {
  agentId: string;
  taskId: string;
  contextPackKey: string;
  designPackageKey: string;
  limitMinutes: string;
};

export type LaunchRequest = {
  agentId: string;
  taskId: string;
  contextPackId: string | null;
  contextPackRevision: string | null;
  designPackageId: string | null;
  designPackageRevision: string | null;
  wallTimeSeconds: number;
};

export type LaunchIntent = Readonly<{
  key: string;
  taskTitle: string;
  roleName: string;
  profileId: string | null;
  draft: Readonly<RunDraft>;
  input: Readonly<LaunchRequest>;
}>;

// The typed draft value, not the control, decides whether a request may leave:
// HTML min/max/step are a convenience the user can bypass. An empty field, a
// fraction, a sign, an exponent or an out-of-range count all read as no value.
export function runLimitSeconds(minutes: string): number | null {
  if (!/^\d+$/.test(minutes.trim())) return null;
  const value = Number(minutes.trim());
  if (value < MIN_RUN_LIMIT_MINUTES || value > MAX_RUN_LIMIT_MINUTES) return null;
  return value * 60;
}

// A run row states the limit the delegation actually recorded, or says it was
// not provided. A missing, malformed or non-positive field is never turned into
// the create-form default or the server default.
export type AttemptLimitReading =
  | { kind: "absent" }
  | { kind: "minutes"; value: number }
  | { kind: "seconds"; value: number };

export function attemptLimitReading(wallTimeSeconds: number | null): AttemptLimitReading {
  if (wallTimeSeconds === null || !Number.isInteger(wallTimeSeconds) || wallTimeSeconds <= 0) {
    return { kind: "absent" };
  }
  return wallTimeSeconds % 60 === 0
    ? { kind: "minutes", value: wallTimeSeconds / 60 }
    : { kind: "seconds", value: wallTimeSeconds };
}

export function attemptLimitText(wallTimeSeconds: number | null, text: (key: MessageKey) => string): string {
  const reading = attemptLimitReading(wallTimeSeconds);
  return reading.kind === "absent"
    ? text("run_limit_absent")
    : `${reading.value} ${text(reading.kind === "minutes" ? "run_limit_minutes" : "run_limit_seconds")}`;
}

export function freezeLaunchIntent(intent: LaunchIntent): LaunchIntent {
  return Object.freeze({ ...intent, draft: Object.freeze({ ...intent.draft }), input: Object.freeze({ ...intent.input }) });
}

export function launchIntentIsVisible(intent: LaunchIntent, selectedTaskId: string | null, draft: RunDraft): boolean {
  return selectedTaskId === intent.input.taskId
    && draft.taskId === intent.draft.taskId && draft.agentId === intent.draft.agentId
    && draft.contextPackKey === intent.draft.contextPackKey && draft.designPackageKey === intent.draft.designPackageKey
    && draft.limitMinutes === intent.draft.limitMinutes;
}

export function restoreLaunchDraft(intent: LaunchIntent): RunDraft {
  return { ...intent.draft };
}
