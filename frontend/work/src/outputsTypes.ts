export type OutputScope = Readonly<{ kind: "task" | "agent"; id: string }>;
export type OutputVersions = "latest" | "all";
export type OutputState = "unchecked" | "ready" | "stale" | "unavailable";
export type ImageOutput = Readonly<{
  kind: "design_image" | "artifact_image";
  outputId: string; title: string; artifactId: string; contentRevision: string;
  state: OutputState; reason: string | null; mediaType: "image/png" | "image/jpeg";
  latest: boolean; versionCount: number; width: number | null; height: number | null;
  historicalPreviewVerified?: boolean;
}>;
export type OutputList = Readonly<{ scope: OutputScope; versions: OutputVersions; items: readonly (ImageOutput | LiveOutput)[] }>;
export type OutputSummary = Readonly<{ scope: OutputScope; total: number; unchecked: number; stale: number; unavailable: number }>;
export class OutputsError extends Error { constructor() { super("outputs_unavailable"); } }
const ID = "[0-7][0-9A-HJKMNP-TV-Z]{25}";
const SESSION = /^session\.(?:[a-f0-9]{32}|[0-7][0-9A-HJKMNP-TV-Z]{25})$/;
const DIGEST = /^sha256:[a-f0-9]{64}$/;
const REASONS = new Set([
  "package_revision_superseded", "artifact_revision_changed", "producer_task_revision_changed",
  "artifact_binding_changed", "producer_provenance_missing", "output_preview_unsupported",
  "verified_preview_changed", "output_preview_unavailable", "artifact_preview_invalid_id",
  "artifact_preview_not_found", "artifact_preview_manifest_invalid", "artifact_preview_classification_blocked",
  "artifact_preview_unsupported_media_type", "artifact_preview_missing_source", "artifact_preview_symlink_source",
  "artifact_preview_non_regular_source", "artifact_preview_stale_source", "artifact_preview_oversize",
  "artifact_preview_invalid_image", "artifact_preview_pixel_limit",
]);
function fail(): never { throw new OutputsError(); }
function object(value: unknown): Record<string, unknown> { return typeof value === "object" && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : fail(); }
function keys(value: Record<string, unknown>, expected: string): void {
  const names = expected.split(" "); if (Object.keys(value).length !== names.length || names.some((name) => !(name in value))) fail();
}
function id(value: unknown, kind: string): string { return typeof value === "string" && new RegExp(`^${kind}\\.${ID}$`).test(value) ? value : fail(); }
function count(value: unknown): number { return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : fail(); }
function text(value: unknown, limit = 256): string { return typeof value === "string" && value.length > 0 && value.length <= limit && !/[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]/.test(value) ? value : fail(); }
export function scopeKey(scope: OutputScope): string { if (scope.kind !== "task" && scope.kind !== "agent") fail(); return `${scope.kind}:${id(scope.id, scope.kind)}`; }
function parseScope(value: unknown, expected: OutputScope): OutputScope {
  const row = object(value); keys(row, "kind id");
  if (row.kind !== expected.kind || row.id !== expected.id) fail();
  scopeKey(expected); return { kind: expected.kind, id: expected.id };
}
export function parseOutputSummary(value: unknown, expected: OutputScope): OutputSummary {
  const row = object(value); keys(row, "schema scope versions total counts previews_verified");
  if (row.schema !== "agent_commons.outputs-summary.v1" || row.versions !== "latest" || row.previews_verified !== false) fail();
  const counts = object(row.counts); keys(counts, "unchecked stale unavailable");
  const total = count(row.total), unchecked = count(counts.unchecked), stale = count(counts.stale), unavailable = count(counts.unavailable);
  if (total !== unchecked + stale + unavailable) fail();
  return { scope: parseScope(row.scope, expected), total, unchecked, stale, unavailable };
}
export function parseOutputList(value: unknown, expected: OutputScope, versions: OutputVersions): OutputList {
  const row = object(value); keys(row, "schema scope versions items truncated");
  if (row.schema !== "agent_commons.outputs.v1" || row.versions !== versions || row.truncated !== false || !Array.isArray(row.items) || row.items.length > 64) fail();
  const scope = parseScope(row.scope, expected), seen = new Set<string>();
  const items = row.items.map((raw): ImageOutput | LiveOutput => {
    const item = object(raw);
    if (item.kind === "live_preview") {
      const live = parseLive(item, scope, versions);
      if (seen.has(live.outputId)) fail();
      seen.add(live.outputId); return live;
    }
    if (item.kind === "design_image") {
      keys(item, "kind output_id series_id title package_id package_revision screen_id artifact_id artifact_revision content_revision task_id task_revision producer_session_id producer_agent_id producer_delegation_id recorded_at media_type classification state reason latest version_count width height");
      const packageId = id(item.package_id, "design_package"), packageRevision = id(item.package_revision, "evt"), screenId = id(item.screen_id, "screen");
      if (item.output_id !== `${packageId}@${packageRevision}:${screenId}` || item.series_id !== `${packageId}:${screenId}`) fail();
    } else if (item.kind === "artifact_image") {
      keys(item, "kind output_id series_id title artifact_id artifact_revision content_revision task_id task_revision producer_session_id producer_agent_id producer_delegation_id delegation_revision recorded_at media_type classification state reason latest version_count width height historical_preview_verified");
      if (item.output_id !== `${id(item.artifact_id, "artifact")}@${id(item.artifact_revision, "evt")}` || typeof item.series_id !== "string" || !/^generated\.[a-f0-9]{64}$/.test(item.series_id)) fail();
      if (typeof item.historical_preview_verified !== "boolean" || (item.historical_preview_verified && (item.state !== "stale" || item.reason !== "producer_task_revision_changed"))) fail();
      id(item.delegation_revision, "evt"); id(item.producer_agent_id, "agent"); id(item.producer_delegation_id, "delegation");
      if (typeof item.producer_session_id !== "string" || !SESSION.test(item.producer_session_id)) fail();
    } else fail();
    if (typeof item.output_id !== "string" || seen.has(item.output_id)) fail();
    id(item.artifact_revision, "evt"); id(item.task_revision, "evt"); id(item.task_id, "task");
    for (const [field, kind] of [["producer_session_id", "session"], ["producer_agent_id", "agent"], ["producer_delegation_id", "delegation"]]) if (item[field] !== null) {
      if (kind === "session") { if (typeof item[field] !== "string" || !SESSION.test(item[field] as string)) fail(); }
      else id(item[field], kind);
    }
    if ((scope.kind === "task" && item.task_id !== scope.id) || (scope.kind === "agent" && item.producer_agent_id !== scope.id)) fail();
    if (typeof item.content_revision !== "string" || !DIGEST.test(item.content_revision) || !["public", "internal"].includes(item.classification as string)) fail();
    if (item.media_type !== "image/png" && item.media_type !== "image/jpeg") fail();
    if (!["unchecked", "ready", "stale", "unavailable"].includes(item.state as string) || typeof item.latest !== "boolean" || (versions === "latest" && !item.latest)) fail();
    if (item.reason !== null && (typeof item.reason !== "string" || !REASONS.has(item.reason))) fail();
    if (item.recorded_at !== null && (typeof item.recorded_at !== "string" || !Number.isFinite(Date.parse(item.recorded_at)) || item.recorded_at.length > 64)) fail();
    const width = item.width === null ? null : count(item.width), height = item.height === null ? null : count(item.height);
    if (item.state === "ready" && (item.reason !== null || !width || !height || width * height > 16_000_000)) fail();
    if (item.state !== "ready" && (width !== null || height !== null)) fail();
    const versionCount = count(item.version_count); if (!versionCount) fail();
    seen.add(item.output_id as string);
    return { kind: item.kind, outputId: item.output_id as string, title: text(item.title), artifactId: id(item.artifact_id, "artifact"), contentRevision: item.content_revision,
      state: item.state as OutputState, reason: item.reason as string | null, mediaType: item.media_type, latest: item.latest,
      versionCount, width, height, historicalPreviewVerified: item.kind === "artifact_image" && item.historical_preview_verified === true };
  });
  return { scope, versions, items };
}


export type LiveOutput = Readonly<{
  kind: "live_preview"; outputId: string; title: string; url: string | null; origin: string;
  publishedAt: number; expiresAt: number; reportedState: "starting" | "ready" | "unavailable";
  state: "starting" | "reported_ready" | "unavailable" | "expired" | "stale";
  latest: boolean; versionCount: number;
}>;
function liveOrigin(value: unknown): string {
  if (typeof value !== "string" || !/^http:\/\/(?:127\.0\.0\.1|\[::1\]):[1-9][0-9]{0,4}$/.test(value)) fail();
  const port = Number(new URL(value).port); if (port < 1024 || port > 65535) fail();
  return value;
}
function parseLive(item: Record<string, unknown>, scope: OutputScope, versions: OutputVersions): LiveOutput {
  keys(item, "kind output_id title task_id task_revision producer_agent_id producer_session_id producer_delegation_id delegation_revision url origin published_at expires_at reported_state state reachability latest version_count");
  if (typeof item.output_id !== "string" || !/^preview\.[a-f0-9]{32}$/.test(item.output_id)) fail();
  id(item.task_id, "task"); id(item.task_revision, "evt"); id(item.producer_agent_id, "agent");
  id(item.producer_delegation_id, "delegation"); id(item.delegation_revision, "evt");
  if (typeof item.producer_session_id !== "string" || !SESSION.test(item.producer_session_id)) fail();
  if ((scope.kind === "task" ? item.task_id : item.producer_agent_id) !== scope.id) fail();
  const origin = liveOrigin(item.origin);
  if (!["starting", "ready", "unavailable"].includes(item.reported_state as string) || !["starting", "reported_ready", "unavailable", "expired", "stale"].includes(item.state as string) || item.reachability !== "unknown") fail();
  if (typeof item.latest !== "boolean" || (versions === "latest" && !item.latest) || !count(item.version_count)) fail();
  if (item.state === "reported_ready" ? item.reported_state !== "ready" || item.url !== origin + "/" || !item.latest : item.url !== null) fail();
  if (typeof item.published_at !== "string" || typeof item.expires_at !== "string" || item.published_at.length > 40 || item.expires_at.length > 40) fail();
  const publishedAt = Date.parse(item.published_at), expiresAt = Date.parse(item.expires_at);
  if (!Number.isFinite(publishedAt) || !Number.isFinite(expiresAt) || expiresAt - publishedAt < 60_000 || expiresAt - publishedAt > 3_600_000) fail();
  return { kind: "live_preview", outputId: item.output_id, title: text(item.title, 160), url: item.url as string | null, origin, publishedAt, expiresAt,
    reportedState: item.reported_state as LiveOutput["reportedState"], state: item.state as LiveOutput["state"], latest: item.latest, versionCount: item.version_count as number };
}
/** New-tab navigation is rechecked at render and click, without a network probe. */
export function liveNavigation(item: LiveOutput, now: number, uiOrigin: string): string | null {
  if (item.state !== "reported_ready" || now >= item.expiresAt || !item.latest) return null;
  try {
    if (liveOrigin(item.origin) + "/" !== item.url || new URL(uiOrigin).port === new URL(item.origin).port) return null;
    return item.url;
  } catch { return null; }
}

/** Viewing exact verified pixels never promotes historical task output to current readiness. */
export function canViewImage(item: ImageOutput): boolean {
  return item.state === "ready" || (item.kind === "artifact_image" && item.state === "stale" && item.reason === "producer_task_revision_changed" && item.historicalPreviewVerified === true);
}
