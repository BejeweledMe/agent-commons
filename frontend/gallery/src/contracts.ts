export type PreviewState = "ready" | "stale" | "unavailable";

export type GalleryScreen = {
  screen_id: string;
  ordinal: number;
  title: string;
  artifact_id: string;
  artifact_revision: string;
  artifact_content_revision: string;
  producer_task_id: string;
  producer_task_revision: string;
  producer_session_id: string;
  classification: "public" | "internal";
  media_type: "image/png" | "image/jpeg";
  preview_state: PreviewState;
  preview_reason: string | null;
  preview_eligible: boolean;
  width: number | null;
  height: number | null;
};

export type GalleryPackage = {
  design_package_id: string;
  revision: string;
  title: string;
  producer_session_id: string;
  recorded_at: string | null;
  freshness: "fresh" | "stale";
  screen_count: number;
  screens: GalleryScreen[];
};

export type GalleryError = {
  code: GalleryErrorCode;
  message: string;
  safe_next_actions: string[];
};

export type GalleryResponse = {
  schema: "agent_commons.gallery.v1";
  state: "loading" | "empty" | "ready" | "stale" | "error";
  freshness: "fresh" | "stale" | null;
  read_at: string | null;
  packages: GalleryPackage[];
  error: GalleryError | null;
};

export type GalleryErrorCode =
  | "gallery_invalid_id"
  | "gallery_package_not_found"
  | "gallery_projection_unavailable"
  | "gallery_bounds_exceeded";

export type FeedbackSuccess = { threadId: string; revision: string };

export type AuthoringCandidate = {
  candidate_id: string;
  artifact_id: string;
  artifact_revision: string;
  artifact_content_revision: string;
  producer_task_id: string;
  producer_task_revision: string;
  producer_task_title: string;
  classification: "public" | "internal";
  media_type: "image/png" | "image/jpeg";
  width: number;
  height: number;
};

export type AuthoringError = {
  code: string;
  message: string;
  safe_next_actions: string[];
};

export type AuthoringResponse = {
  schema: "agent_commons.gallery-authoring.v1";
  state: "empty" | "ready" | "unavailable";
  writes_enabled: boolean;
  candidates: AuthoringCandidate[];
  error: AuthoringError | null;
};

export type AuthoringResult = {
  schema: "agent_commons.gallery-authoring-result.v1";
  state: "published" | "revised";
  design_package_id: string;
  revision: string;
};

export type AuthoringIntent = { fingerprint: string; key: string };
export type AuthoringRefreshFailureCode = "gallery_refresh_failed" | "candidate_refresh_failed";
export type AuthoringSavedState =
  | { kind: "success"; revision: string }
  | { kind: "saved_refresh_failed"; revision: string; code: AuthoringRefreshFailureCode };

export const MAX_GALLERY_PACKAGES = 64;
export const MAX_GALLERY_SCREENS = 64;
export const MAX_FEEDBACK_BYTES = 8_192;
export const MAX_GALLERY_RESPONSE_BYTES = 1_048_576;
export const MAX_AUTHORING_CANDIDATES = 256;

const MAX_TITLE_CHARS = 256;
const MAX_SHORT_TEXT_BYTES = 1_024;
const MAX_ERROR_MESSAGE_BYTES = 2_048;
const MAX_SAFE_ACTIONS = 4;
const MAX_PREVIEW_PIXELS = 16_000_000;
const TYPED_ID = /^[a-z][a-z0-9_]*\.[0-9A-HJKMNP-TV-Z]{26}$/;
const CONTENT_REVISION = /^sha256:[a-f0-9]{64}$/;
const ERROR_CODE_PATTERN = /^[a-z][a-z0-9_]{0,79}$/;
const RFC3339 = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$/;
const GALLERY_STATES = new Set(["loading", "empty", "ready", "stale", "error"]);
const FRESHNESS_STATES = new Set(["fresh", "stale"]);
const PREVIEW_STATES = new Set(["ready", "stale", "unavailable"]);
const CLASSIFICATIONS = new Set(["public", "internal"]);
const MEDIA_TYPES = new Set(["image/png", "image/jpeg"]);
const GALLERY_ERROR_CODES = new Set<GalleryErrorCode>([
  "gallery_invalid_id",
  "gallery_package_not_found",
  "gallery_projection_unavailable",
  "gallery_bounds_exceeded",
]);
const PREVIEW_REASONS = new Set([
  "artifact_revision_changed",
  "producer_task_revision_changed",
  "artifact_binding_changed",
  "verified_preview_changed",
  "producer_provenance_missing",
  "artifact_preview_unavailable",
  "artifact_preview_invalid_id",
  "artifact_preview_not_found",
  "artifact_preview_manifest_invalid",
  "artifact_preview_classification_blocked",
  "artifact_preview_unsupported_media_type",
  "artifact_preview_missing_source",
  "artifact_preview_symlink_source",
  "artifact_preview_non_regular_source",
  "artifact_preview_stale_source",
  "artifact_preview_oversize",
  "artifact_preview_invalid_image",
  "artifact_preview_pixel_limit",
]);

const ROOT_FIELDS = ["schema", "state", "freshness", "read_at", "packages", "error"];
const PACKAGE_FIELDS = [
  "design_package_id",
  "revision",
  "title",
  "producer_session_id",
  "recorded_at",
  "freshness",
  "screen_count",
  "screens",
];
const SCREEN_FIELDS = [
  "screen_id",
  "ordinal",
  "title",
  "artifact_id",
  "artifact_revision",
  "artifact_content_revision",
  "producer_task_id",
  "producer_task_revision",
  "producer_session_id",
  "classification",
  "media_type",
  "preview_state",
  "preview_reason",
  "preview_eligible",
  "width",
  "height",
];
const ERROR_FIELDS = ["code", "message", "safe_next_actions"];
const AUTHORING_FIELDS = ["schema", "state", "writes_enabled", "candidates", "error"];
const AUTHORING_CANDIDATE_FIELDS = [
  "candidate_id",
  "artifact_id",
  "artifact_revision",
  "artifact_content_revision",
  "producer_task_id",
  "producer_task_revision",
  "producer_task_title",
  "classification",
  "media_type",
  "width",
  "height",
];

function fail(): never {
  throw new Error("gallery_contract_invalid");
}

function record(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) fail();
  return value as Record<string, unknown>;
}

function exactFields(value: Record<string, unknown>, expected: string[]): void {
  const actual = Object.keys(value).sort();
  const wanted = [...expected].sort();
  if (actual.length !== wanted.length || actual.some((key, index) => key !== wanted[index])) fail();
}

function utf8Length(value: string): number {
  return new TextEncoder().encode(value).byteLength;
}

function boundedText(value: unknown, maxChars: number, maxBytes = MAX_SHORT_TEXT_BYTES): string {
  if (
    typeof value !== "string" ||
    [...value].length < 1 ||
    [...value].length > maxChars ||
    value.trim() !== value ||
    utf8Length(value) > maxBytes ||
    /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/.test(value)
  ) fail();
  return value;
}

export function applyDocumentLocale(root: Pick<HTMLElement, "lang">, locale: "en" | "ru"): void {
  root.lang = locale;
}

function typedId(value: unknown, prefix: string): string {
  if (typeof value !== "string" || !TYPED_ID.test(value) || !value.startsWith(`${prefix}.`)) fail();
  return value;
}

function timestamp(value: unknown, nullable: boolean): string | null {
  if (value === null && nullable) return null;
  if (typeof value !== "string" || value.length > 40 || !RFC3339.test(value) || Number.isNaN(Date.parse(value))) fail();
  return value;
}

function positiveDimension(value: unknown): number | null {
  if (value === null) return null;
  if (!Number.isSafeInteger(value) || (value as number) < 1 || (value as number) > MAX_PREVIEW_PIXELS) fail();
  return value as number;
}

function parseScreen(value: unknown): GalleryScreen {
  const item = record(value);
  exactFields(item, SCREEN_FIELDS);
  const previewState = item.preview_state;
  if (typeof previewState !== "string" || !PREVIEW_STATES.has(previewState)) fail();
  const previewReason = item.preview_reason;
  if (!(previewReason === null || (typeof previewReason === "string" && PREVIEW_REASONS.has(previewReason)))) fail();
  const width = positiveDimension(item.width);
  const height = positiveDimension(item.height);
  const ready = previewState === "ready";
  if (
    item.preview_eligible !== ready ||
    (ready && (previewReason !== null || width === null || height === null)) ||
    (!ready && (previewReason === null || width !== null || height !== null)) ||
    (width !== null && height !== null && width * height > MAX_PREVIEW_PIXELS)
  ) fail();
  if (!Number.isSafeInteger(item.ordinal) || (item.ordinal as number) < 1 || (item.ordinal as number) > MAX_GALLERY_SCREENS) fail();
  if (typeof item.classification !== "string" || !CLASSIFICATIONS.has(item.classification)) fail();
  if (typeof item.media_type !== "string" || !MEDIA_TYPES.has(item.media_type)) fail();
  if (typeof item.artifact_content_revision !== "string" || !CONTENT_REVISION.test(item.artifact_content_revision)) fail();
  const producerSession = item.producer_session_id === "unknown"
    ? "unknown"
    : typedId(item.producer_session_id, "session");
  if ((producerSession === "unknown") !== (previewReason === "producer_provenance_missing")) fail();
  return {
    screen_id: typedId(item.screen_id, "screen"),
    ordinal: item.ordinal as number,
    title: boundedText(item.title, MAX_TITLE_CHARS),
    artifact_id: typedId(item.artifact_id, "artifact"),
    artifact_revision: typedId(item.artifact_revision, "evt"),
    artifact_content_revision: item.artifact_content_revision,
    producer_task_id: typedId(item.producer_task_id, "task"),
    producer_task_revision: typedId(item.producer_task_revision, "evt"),
    producer_session_id: producerSession,
    classification: item.classification as "public" | "internal",
    media_type: item.media_type as "image/png" | "image/jpeg",
    preview_state: previewState as PreviewState,
    preview_reason: previewReason,
    preview_eligible: ready,
    width,
    height,
  };
}

function parsePackage(value: unknown): GalleryPackage {
  const item = record(value);
  exactFields(item, PACKAGE_FIELDS);
  if (!Array.isArray(item.screens) || item.screens.length < 1 || item.screens.length > MAX_GALLERY_SCREENS) fail();
  const screens = item.screens.map(parseScreen);
  if (!Number.isSafeInteger(item.screen_count) || item.screen_count !== screens.length) fail();
  if (screens.some((screen, index) => screen.ordinal !== index + 1)) fail();
  if (new Set(screens.map((screen) => screen.screen_id)).size !== screens.length) fail();
  if (typeof item.freshness !== "string" || !FRESHNESS_STATES.has(item.freshness)) fail();
  const expectedFreshness = screens.every((screen) => screen.preview_state === "ready") ? "fresh" : "stale";
  if (item.freshness !== expectedFreshness) fail();
  return {
    design_package_id: typedId(item.design_package_id, "design_package"),
    revision: typedId(item.revision, "evt"),
    title: boundedText(item.title, MAX_TITLE_CHARS),
    producer_session_id: typedId(item.producer_session_id, "session"),
    recorded_at: timestamp(item.recorded_at, true),
    freshness: item.freshness as "fresh" | "stale",
    screen_count: screens.length,
    screens,
  };
}

function parseError(value: unknown): GalleryError {
  const item = record(value);
  exactFields(item, ERROR_FIELDS);
  if (typeof item.code !== "string" || !GALLERY_ERROR_CODES.has(item.code as GalleryErrorCode)) fail();
  if (!Array.isArray(item.safe_next_actions) || item.safe_next_actions.length > MAX_SAFE_ACTIONS) fail();
  const actions = item.safe_next_actions.map((action) => boundedText(action, 256));
  return {
    code: item.code as GalleryErrorCode,
    message: boundedText(item.message, 1_024, MAX_ERROR_MESSAGE_BYTES),
    safe_next_actions: actions,
  };
}

export function parseGalleryResponse(value: unknown): GalleryResponse {
  const item = record(value);
  exactFields(item, ROOT_FIELDS);
  if (item.schema !== "agent_commons.gallery.v1" || typeof item.state !== "string" || !GALLERY_STATES.has(item.state)) fail();
  if (!Array.isArray(item.packages) || item.packages.length > MAX_GALLERY_PACKAGES) fail();
  const packages = item.packages.map(parsePackage);
  if (packages.reduce((total, value) => total + value.screen_count, 0) > MAX_GALLERY_SCREENS) fail();
  const error = item.error === null ? null : parseError(item.error);
  const readAt = timestamp(item.read_at, true);
  const freshness = item.freshness;
  if (!(freshness === null || freshness === "fresh" || freshness === "stale")) fail();
  const state = item.state as GalleryResponse["state"];
  const allFresh = packages.every((value) => value.freshness === "fresh");
  const consistent =
    (state === "loading" && freshness === null && readAt === null && packages.length === 0 && error === null) ||
    (state === "empty" && freshness === "fresh" && readAt !== null && packages.length === 0 && error === null) ||
    (state === "ready" && freshness === "fresh" && readAt !== null && packages.length > 0 && allFresh && error === null) ||
    (state === "stale" && freshness === "stale" && readAt !== null && packages.length > 0 && !allFresh && error === null) ||
    (state === "error" && freshness === null && readAt !== null && packages.length === 0 && error !== null);
  if (!consistent) fail();
  return { schema: "agent_commons.gallery.v1", state, freshness, read_at: readAt, packages, error };
}

export async function readBoundedJson(response: Response, maximum = MAX_GALLERY_RESPONSE_BYTES): Promise<unknown> {
  const declared = response.headers.get("Content-Length");
  if (declared !== null && (!/^\d+$/.test(declared) || Number(declared) > maximum)) fail();
  const text = await response.text();
  if (utf8Length(text) > maximum) fail();
  try {
    return JSON.parse(text) as unknown;
  } catch {
    fail();
  }
}

export function parseFeedbackSuccess(value: unknown): FeedbackSuccess {
  const item = record(value);
  const entityRef = record(item.entity_ref);
  if (entityRef.kind !== "thread") throw new Error("feedback_contract_invalid");
  try {
    return { threadId: typedId(entityRef.id, "thread"), revision: typedId(item.revision, "evt") };
  } catch {
    throw new Error("feedback_contract_invalid");
  }
}

function parseAuthoringCandidate(value: unknown): AuthoringCandidate {
  const item = record(value);
  exactFields(item, AUTHORING_CANDIDATE_FIELDS);
  if (typeof item.candidate_id !== "string" || !item.candidate_id.startsWith("candidate.") || !TYPED_ID.test(item.candidate_id)) fail();
  if (typeof item.artifact_content_revision !== "string" || !CONTENT_REVISION.test(item.artifact_content_revision)) fail();
  if (typeof item.classification !== "string" || !CLASSIFICATIONS.has(item.classification)) fail();
  if (typeof item.media_type !== "string" || !MEDIA_TYPES.has(item.media_type)) fail();
  const width = positiveDimension(item.width);
  const height = positiveDimension(item.height);
  if (width === null || height === null || width * height > MAX_PREVIEW_PIXELS) fail();
  return {
    candidate_id: item.candidate_id,
    artifact_id: typedId(item.artifact_id, "artifact"),
    artifact_revision: typedId(item.artifact_revision, "evt"),
    artifact_content_revision: item.artifact_content_revision,
    producer_task_id: typedId(item.producer_task_id, "task"),
    producer_task_revision: typedId(item.producer_task_revision, "evt"),
    producer_task_title: boundedText(item.producer_task_title, MAX_TITLE_CHARS),
    classification: item.classification as "public" | "internal",
    media_type: item.media_type as "image/png" | "image/jpeg",
    width,
    height,
  };
}

export function parseAuthoringResponse(value: unknown): AuthoringResponse {
  const item = record(value);
  exactFields(item, AUTHORING_FIELDS);
  if (item.schema !== "agent_commons.gallery-authoring.v1") fail();
  if (!Array.isArray(item.candidates) || item.candidates.length > MAX_AUTHORING_CANDIDATES) fail();
  const candidates = item.candidates.map(parseAuthoringCandidate);
  if (new Set(candidates.map((candidate) => candidate.candidate_id)).size !== candidates.length) fail();
  const state = item.state;
  if (state !== "empty" && state !== "ready" && state !== "unavailable") fail();
  const error = item.error === null ? null : (() => {
    const parsed = record(item.error);
    exactFields(parsed, ERROR_FIELDS);
    if (typeof parsed.code !== "string" || !ERROR_CODE_PATTERN.test(parsed.code)) fail();
    if (!Array.isArray(parsed.safe_next_actions) || parsed.safe_next_actions.length > MAX_SAFE_ACTIONS) fail();
    return {
      code: parsed.code,
      message: boundedText(parsed.message, 1_024, MAX_ERROR_MESSAGE_BYTES),
      safe_next_actions: parsed.safe_next_actions.map((action) => boundedText(action, 256)),
    };
  })();
  const writesEnabled = item.writes_enabled;
  const consistent =
    (state === "ready" && writesEnabled === true && candidates.length > 0 && error === null) ||
    (state === "empty" && writesEnabled === true && candidates.length === 0 && error === null) ||
    (state === "unavailable" && writesEnabled === false && candidates.length === 0 && error !== null);
  if (!consistent) fail();
  return {
    schema: "agent_commons.gallery-authoring.v1",
    state,
    writes_enabled: writesEnabled,
    candidates,
    error,
  };
}

export function parseAuthoringResult(value: unknown): AuthoringResult {
  const item = record(value);
  exactFields(item, ["schema", "state", "design_package_id", "revision"]);
  if (item.schema !== "agent_commons.gallery-authoring-result.v1") fail();
  if (item.state !== "published" && item.state !== "revised") fail();
  return {
    schema: "agent_commons.gallery-authoring-result.v1",
    state: item.state,
    design_package_id: typedId(item.design_package_id, "design_package"),
    revision: typedId(item.revision, "evt"),
  };
}

export function authoringTitleIsValid(value: string): boolean {
  const normalized = value.trim();
  return normalized.length > 0 && [...normalized].length <= MAX_TITLE_CHARS && utf8Length(normalized) <= MAX_SHORT_TEXT_BYTES;
}

export function retainAuthoringIntent(
  current: AuthoringIntent | null,
  fingerprint: string,
  newKey: string,
): AuthoringIntent {
  return current?.fingerprint === fingerprint ? current : { fingerprint, key: newKey };
}

export function authoringSavedState(
  result: AuthoringResult,
  refreshSucceeded: boolean,
  failureCode: AuthoringRefreshFailureCode = "gallery_refresh_failed",
): AuthoringSavedState {
  return refreshSucceeded
    ? { kind: "success", revision: result.revision }
    : { kind: "saved_refresh_failed", revision: result.revision, code: failureCode };
}

export function authoringSavedCopyKeys(state: AuthoringSavedState): {
  summary: "authoring_saved" | "authoring_saved_gallery_refresh_failed" | "authoring_saved_candidates_refresh_failed";
  help: null | "authoring_saved_gallery_refresh_failed_help" | "authoring_saved_candidates_refresh_failed_help";
} {
  if (state.kind === "success") return { summary: "authoring_saved", help: null };
  return state.code === "candidate_refresh_failed"
    ? { summary: "authoring_saved_candidates_refresh_failed", help: "authoring_saved_candidates_refresh_failed_help" }
    : { summary: "authoring_saved_gallery_refresh_failed", help: "authoring_saved_gallery_refresh_failed_help" };
}

export async function settleAuthoringRefresh(
  result: AuthoringResult,
  refreshGallery: () => Promise<void>,
  refreshCandidates: () => Promise<void>,
): Promise<AuthoringSavedState> {
  try {
    await refreshGallery();
  } catch {
    return authoringSavedState(result, false, "gallery_refresh_failed");
  }
  try {
    await refreshCandidates();
  } catch {
    return authoringSavedState(result, false, "candidate_refresh_failed");
  }
  return authoringSavedState(result, true);
}

export async function parseSuccessfulGalleryRefresh(response: Response): Promise<GalleryResponse> {
  const payload = parseGalleryResponse(await readBoundedJson(response));
  if (!response.ok || payload.state === "error") throw new Error(payload.error?.code ?? "gallery_unavailable");
  return payload;
}

export function feedbackMessageIsValid(value: string): boolean {
  return value.trim().length > 0 && utf8Length(value.trim()) <= MAX_FEEDBACK_BYTES;
}

export class AbortSlot {
  private current: AbortController | null = null;

  begin(): AbortController {
    this.current?.abort();
    const controller = new AbortController();
    this.current = controller;
    return controller;
  }

  isCurrent(controller: AbortController): boolean {
    return this.current === controller && !controller.signal.aborted;
  }

  finish(controller: AbortController): void {
    if (this.current === controller) this.current = null;
  }

  abort(): void {
    this.current?.abort();
    this.current = null;
  }
}
