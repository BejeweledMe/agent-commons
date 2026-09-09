import type { ProviderAvailability } from "./contracts.js";
import type { MessageKey } from "./i18n.js";

/**
 * Presentation-only readings of setup and provider state. Every mapping is
 * closed: an unknown canonical value reads as unknown, never as ready.
 */

export type RailSetupState = "configured" | "needs_setup" | "not_repository" | "unknown";

const RAIL_STATES: Readonly<Record<string, RailSetupState>> = {
  setup_not_a_repository: "not_repository",
  setup_uninitialized: "needs_setup",
  setup_unconfigured: "needs_setup",
  setup_configured: "configured"
};

const SETUP_LABELS: Readonly<Record<string, MessageKey>> = {
  setup_not_a_repository: "setup_not_repository",
  setup_uninitialized: "setup_uninitialized",
  setup_unconfigured: "setup_unconfigured",
  setup_configured: "setup_configured"
};

export function railSetupState(state: string): RailSetupState {
  return RAIL_STATES[state] ?? "unknown";
}

/** The adjacent text carries the state; the dot only duplicates it. */
export function railSetupLabelKey(state: string): MessageKey {
  return SETUP_LABELS[state] ?? "not_configured";
}

export type ReadinessTone = "ok" | "attention" | "unknown";
export type ReadinessStage = "installation" | "initialization" | "check_run" | "sign_in";

export type ReadinessLine = Readonly<{
  stage: ReadinessStage;
  labelKey: MessageKey;
  valueKey: MessageKey;
  tone: ReadinessTone;
  /** The canonical value behind the line, shown only under technical details. */
  code: string;
}>;

type ReadinessInput = Pick<
  ProviderAvailability,
  "installationState" | "initializationState" | "qualification" | "authentication"
>;

const STAGE_LABELS: Readonly<Record<ReadinessStage, MessageKey>> = {
  installation: "provider_availability_install_label",
  initialization: "provider_availability_init_label",
  check_run: "provider_availability_qualification_label",
  sign_in: "provider_availability_auth_label"
};

function installation(state: ReadinessInput["installationState"]): { valueKey: MessageKey; tone: ReadinessTone } {
  return state === "installed"
    ? { valueKey: "provider_readiness_installed", tone: "ok" }
    : { valueKey: "provider_readiness_not_installed", tone: "attention" };
}

function initialization(state: ReadinessInput["initializationState"]): { valueKey: MessageKey; tone: ReadinessTone } {
  if (state === "ready") return { valueKey: "provider_readiness_initialized", tone: "ok" };
  if (state === "failed") return { valueKey: "provider_readiness_initialization_failed", tone: "attention" };
  if (state === "passed_unqualified") return { valueKey: "provider_readiness_initialization_incomplete", tone: "attention" };
  return { valueKey: "provider_readiness_unchecked", tone: "unknown" };
}

function checkRun(qualification: ReadinessInput["qualification"]): { valueKey: MessageKey; tone: ReadinessTone } {
  if (qualification.state === "failed") return { valueKey: "provider_readiness_check_failed", tone: "attention" };
  if (qualification.state === "required") return { valueKey: "provider_readiness_check_required", tone: "attention" };
  return qualification.freshness === "current"
    ? { valueKey: "provider_readiness_check_passed", tone: "ok" }
    : { valueKey: "provider_readiness_check_outdated", tone: "attention" };
}

function signIn(authentication: ReadinessInput["authentication"]): { valueKey: MessageKey; tone: ReadinessTone } {
  if (authentication.state === "not_checked") return { valueKey: "provider_readiness_unchecked", tone: "unknown" };
  if (authentication.state === "unsupported") return { valueKey: "provider_readiness_sign_in_not_required", tone: "ok" };
  if (authentication.state === "authentication_required") return { valueKey: "provider_readiness_sign_in_required", tone: "attention" };
  if (authentication.state === "ready" && authentication.freshness === "fresh") {
    return { valueKey: "provider_readiness_signed_in", tone: "ok" };
  }
  return { valueKey: "provider_readiness_sign_in_unconfirmed", tone: "attention" };
}

/** One readable line per stage, in the order a run needs them. */
export function providerReadinessLines(availability: ReadinessInput): readonly ReadinessLine[] {
  const stages: readonly ReadinessLine[] = [
    { stage: "installation", labelKey: STAGE_LABELS.installation, code: availability.installationState, ...installation(availability.installationState) },
    { stage: "initialization", labelKey: STAGE_LABELS.initialization, code: availability.initializationState, ...initialization(availability.initializationState) },
    { stage: "check_run", labelKey: STAGE_LABELS.check_run, code: `${availability.qualification.state}/${availability.qualification.freshness}`, ...checkRun(availability.qualification) },
    { stage: "sign_in", labelKey: STAGE_LABELS.sign_in, code: `${availability.authentication.state}/${availability.authentication.freshness}`, ...signIn(availability.authentication) }
  ];
  return stages;
}
