import type { WorkerPickerState } from "./components/WorkerPicker.js";

/**
 * The one hire gate: only a worker the current READY eligibility read called
 * eligible may be submitted. A legacy preset carries a recorded profile, but it
 * is not exempt -- its profile must be confirmed for the preset's own
 * specialization first, so idle, loading, unavailable, unknown and ineligible
 * all block the hire.
 */
export function hireWorkerAllowed(profileId: string, workerState: WorkerPickerState): boolean {
  return profileId !== ""
    && workerState.kind === "ready"
    && workerState.workers.some((worker) => worker.profileId === profileId && worker.eligibility === "eligible");
}
