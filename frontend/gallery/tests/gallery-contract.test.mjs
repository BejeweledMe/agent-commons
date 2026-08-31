import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  AbortSlot,
  MAX_GALLERY_PACKAGES,
  MAX_GALLERY_SCREENS,
  authoringSavedCopyKeys,
  authoringTitleIsValid,
  authoringSavedState,
  applyDocumentLocale,
  feedbackMessageIsValid,
  parseGalleryResponse,
  parseAuthoringResponse,
  parseAuthoringResult,
  parseSuccessfulGalleryRefresh,
  readBoundedJson,
  retainAuthoringIntent,
  settleAuthoringRefresh,
} from "../src/contracts.ts";

const source = await readFile(new URL("../src/main.tsx", import.meta.url), "utf8");
const styles = await readFile(new URL("../src/styles.css", import.meta.url), "utf8");
const authoringSource = await readFile(new URL("../src/AuthoringPanel.tsx", import.meta.url), "utf8");
const packageJson = JSON.parse(await readFile(new URL("../package.json", import.meta.url), "utf8"));
const messages = JSON.parse(await readFile(new URL("../src/i18n.json", import.meta.url), "utf8"));
const ulid = "01ARZ3NDEKTSV4RRFFQ69G5FAV";
const nextUlid = "01ARZ3NDEKTSV4RRFFQ69G5FAW";

function screen(overrides = {}) {
  return {
    screen_id: `screen.${ulid}`,
    ordinal: 1,
    title: "Home",
    artifact_id: `artifact.${ulid}`,
    artifact_revision: `evt.${ulid}`,
    artifact_content_revision: `sha256:${"a".repeat(64)}`,
    producer_task_id: `task.${ulid}`,
    producer_task_revision: `evt.${nextUlid}`,
    producer_session_id: `session.${ulid}`,
    classification: "internal",
    media_type: "image/png",
    preview_state: "ready",
    preview_reason: null,
    preview_eligible: true,
    width: 100,
    height: 80,
    ...overrides,
  };
}

function packageValue(screens = [screen()], overrides = {}) {
  return {
    design_package_id: `design_package.${ulid}`,
    revision: `evt.${ulid}`,
    title: "Release",
    producer_session_id: `session.${ulid}`,
    recorded_at: "2026-08-31T00:00:00Z",
    freshness: screens.every((item) => item.preview_state === "ready") ? "fresh" : "stale",
    screen_count: screens.length,
    screens,
    ...overrides,
  };
}

function response(packages = [packageValue()], overrides = {}) {
  return {
    schema: "agent_commons.gallery.v1",
    state: "ready",
    freshness: "fresh",
    read_at: "2026-08-31T00:00:00Z",
    packages,
    error: null,
    ...overrides,
  };
}

test("locale keys stay paired and carry no sample-screen copy", () => {
  assert.deepEqual(Object.keys(messages.en).sort(), Object.keys(messages.ru).sort());
  assert.doesNotMatch(Object.values(messages.en).join(" "), /\bdemo\b|mock|sample screen/i);
  assert.doesNotMatch(Object.values(messages.ru).join(" "), /демо|макетные данные/i);
});

test("Gallery uses a CSP-safe semantic board without ReactFlow", () => {
  assert.equal(packageJson.dependencies["@xyflow/react"], undefined);
  assert.doesNotMatch(source, /ReactFlow|@xyflow|style=\{/);
  assert.match(source, /<section aria-labelledby=.*className="package-column"/s);
  assert.match(source, /<ol className="screen-list">/);
  assert.match(source, /<li key=/);
  assert.match(styles, /\.gallery-board[\s\S]*grid-auto-flow: column/);
  assert.doesNotMatch(styles, /\.react-flow__/);
});

test("Gallery consumes only typed same-origin routes", () => {
  assert.match(source, /fetch\(`\$\{currentApiBase\}\/gallery`/);
  assert.match(source, /\/artifacts\/\$\{encodeURIComponent\(screen\.artifact_id\)\}\/preview/);
  assert.match(source, /\/gallery\/\$\{encodeURIComponent\(packageValue\.design_package_id\)\}/);
  assert.match(source, /credentials: "same-origin"/);
  assert.doesNotMatch(source, /Authorization|localStorage|filesystem|file:\/\//);
  assert.doesNotMatch(source, /stdout|stderr|transcript|reasoning/);
});

test("preview bytes are revision-checked and object URLs are revoked", () => {
  const preview = source.indexOf("const preview = await fetch");
  const detail = source.indexOf("const detailResponse = await fetch");
  const display = source.indexOf("URL.createObjectURL(blob)");
  assert.ok(preview >= 0 && detail > preview && display > detail);
  assert.match(source, /currentPackage\?\.revision !== packageValue\.revision/);
  assert.match(source, /currentScreen\?\.artifact_revision !== screen\.artifact_revision/);
  assert.match(source, /currentScreen\?\.artifact_content_revision !== screen\.artifact_content_revision/);
  assert.match(source, /currentScreen\?\.producer_task_revision !== screen\.producer_task_revision/);
  assert.match(source, /URL\.revokeObjectURL/);
});

test("preview request survives UI loading-to-ready transitions and only explicit lifecycle events abort it", () => {
  const requests = new AbortSlot();
  const first = requests.begin();
  assert.equal(first.signal.aborted, false);
  assert.equal(requests.isCurrent(first), true);
  // A React render/state transition has no hook into the slot and cannot abort it.
  assert.equal(first.signal.aborted, false);
  const second = requests.begin();
  assert.equal(first.signal.aborted, true);
  assert.equal(requests.isCurrent(second), true);
  requests.finish(first);
  assert.equal(requests.isCurrent(second), true);
  requests.abort();
  assert.equal(second.signal.aborted, true);
  assert.match(source, /useEffect\(\(\) => \(\) => \{[\s\S]*previewRequests\.current\.abort\(\)[\s\S]*\}, \[\]\)/);
});

test("exact Gallery parser accepts one bounded backend-shaped response", () => {
  const parsed = parseGalleryResponse(response());
  assert.equal(parsed.packages[0].screens[0].artifact_id, `artifact.${ulid}`);
});

test("exact Gallery parser rejects unknown fields, open enums, and inconsistent state", () => {
  assert.throws(() => parseGalleryResponse({ ...response(), surprise: true }), /gallery_contract_invalid/);
  assert.throws(() => parseGalleryResponse(response([packageValue([screen({ classification: "secret" })])])), /gallery_contract_invalid/);
  assert.throws(() => parseGalleryResponse(response([], { state: "ready" })), /gallery_contract_invalid/);
  assert.throws(() => parseGalleryResponse(response([], {
    state: "error", freshness: null, error: { code: "new_server_code", message: "No", safe_next_actions: [] },
  })), /gallery_contract_invalid/);
});

test("exact Gallery parser enforces backend package and total-screen caps", () => {
  const tooManyPackages = Array.from({ length: MAX_GALLERY_PACKAGES + 1 }, () => packageValue());
  assert.throws(() => parseGalleryResponse(response(tooManyPackages)), /gallery_contract_invalid/);
  const tooManyScreens = Array.from({ length: MAX_GALLERY_SCREENS + 1 }, (_, index) => screen({ ordinal: index + 1 }));
  assert.throws(() => parseGalleryResponse(response([packageValue(tooManyScreens)])), /gallery_contract_invalid/);
});

test("exact Gallery parser bounds strings, dimensions, and response bytes", async () => {
  assert.throws(() => parseGalleryResponse(response([packageValue([screen({ title: "x".repeat(257) })])])), /gallery_contract_invalid/);
  assert.throws(() => parseGalleryResponse(response([packageValue([screen({ width: 16_000_001 })])])), /gallery_contract_invalid/);
  const oversized = new Response("x".repeat(100), { headers: { "Content-Length": "100" } });
  await assert.rejects(readBoundedJson(oversized, 99), /gallery_contract_invalid/);
});

test("exact Gallery parser counts Unicode code points like the canonical Python contract", () => {
  const canonicalTitle = "😀".repeat(200);
  assert.equal(parseGalleryResponse(response([packageValue([screen({ title: canonicalTitle })])])).packages[0].screens[0].title, canonicalTitle);
  assert.throws(
    () => parseGalleryResponse(response([packageValue([screen({ title: "😀".repeat(257) })])])),
    /gallery_contract_invalid/,
  );
});

test("feedback posts every exact revision and exposes typed recovery states", () => {
  assert.match(source, /screens\/\$\{encodeURIComponent\(screen\.screen_id\)\}\/feedback/);
  assert.match(source, /design_package_revision: packageValue\.revision/);
  assert.match(source, /artifact_revision: screen\.artifact_revision/);
  assert.match(source, /producer_task_revision: screen\.producer_task_revision/);
  assert.match(source, /feedbackIntentRef\.current\?\.fingerprint !== fingerprint/);
  assert.match(source, /idempotency_key: idempotencyKey/);
  assert.match(source, /kind: "success"/);
  assert.match(source, /kind: "stale"/);
  assert.match(source, /kind: "authentication_required"/);
  assert.match(source, /kind: "error"/);
  assert.equal(feedbackMessageIsValid("ready"), true);
  assert.equal(feedbackMessageIsValid("😀".repeat(2_049)), false);
});

function authoringCandidate(overrides = {}) {
  return {
    candidate_id: `candidate.${ulid}`,
    artifact_id: `artifact.${ulid}`,
    artifact_revision: `evt.${ulid}`,
    artifact_content_revision: `sha256:${"a".repeat(64)}`,
    producer_task_id: `task.${ulid}`,
    producer_task_revision: `evt.${nextUlid}`,
    producer_task_title: "Checkout design",
    classification: "internal",
    media_type: "image/png",
    width: 100,
    height: 80,
    ...overrides,
  };
}

test("authoring parser is closed, bounded, and provenance exact", () => {
  const ready = parseAuthoringResponse({
    schema: "agent_commons.gallery-authoring.v1",
    state: "ready",
    writes_enabled: true,
    candidates: [authoringCandidate()],
    error: null,
  });
  assert.equal(ready.candidates[0].artifact_revision, `evt.${ulid}`);
  assert.equal(ready.candidates[0].producer_task_revision, `evt.${nextUlid}`);
  assert.throws(() => parseAuthoringResponse({ ...ready, path: "/tmp/screen.png" }), /gallery_contract_invalid/);
  assert.throws(() => parseAuthoringResponse({ ...ready, candidates: [authoringCandidate({ media_type: "image/svg+xml" })] }), /gallery_contract_invalid/);
  assert.throws(() => parseAuthoringResponse({ ...ready, state: "unavailable" }), /gallery_contract_invalid/);
});

test("authoring uses opaque selections, exact CAS, stable retry intent, and typed success", () => {
  assert.match(authoringSource, /fetch\(`\$\{apiBase\}\/gallery\/authoring`/);
  assert.match(authoringSource, /expected_revision: target\.revision/);
  assert.match(authoringSource, /intent\.current = retainAuthoringIntent/);
  assert.match(authoringSource, /idempotency_key: intent\.current\.key/);
  assert.doesNotMatch(authoringSource, /intent\.current = null/);
  assert.match(authoringSource, /submit\.kind === "saved_refresh_failed"/);
  assert.match(authoringSource, /settleAuthoringRefresh/);
  assert.match(authoringSource, /previous: AuthoringResponse \| null/);
  assert.match(authoringSource, /state\.kind === "ready" \? state\.response : state\.previous/);
  assert.ok(authoringSource.indexOf("savedStatus !== null") < authoringSource.indexOf('currentResponse?.state === "ready"'));
  assert.doesNotMatch(authoringSource, /state\.kind === "error"[^\n]+authoring_failed/);
  assert.match(authoringSource, /candidate_id: candidateId/);
  assert.doesNotMatch(authoringSource, /artifact_content_revision:/);
  assert.doesNotMatch(authoringSource, /filesystem|file:\/\/|localStorage|stdout|stderr|transcript|reasoning/);
  const result = parseAuthoringResult({
    schema: "agent_commons.gallery-authoring-result.v1",
    state: "published",
    design_package_id: `design_package.${ulid}`,
    revision: `evt.${nextUlid}`,
  });
  assert.equal(result.state, "published");
  assert.equal(authoringTitleIsValid("😀".repeat(256)), true);
  assert.equal(authoringTitleIsValid("😀".repeat(257)), false);
  const firstIntent = retainAuthoringIntent(null, "same-form", "key-one");
  assert.equal(retainAuthoringIntent(firstIntent, "same-form", "key-two"), firstIntent);
  assert.equal(retainAuthoringIntent(firstIntent, "changed-form", "key-two").key, "key-two");
  assert.deepEqual(authoringSavedState(result, false), {
    kind: "saved_refresh_failed",
    revision: `evt.${nextUlid}`,
    code: "gallery_refresh_failed",
  });
});

test("canonical save survives a failed candidate refresh without a second POST or a new intent", async () => {
  let postCalls = 0;
  let galleryRefreshes = 0;
  let candidateRefreshes = 0;
  const intent = retainAuthoringIntent(null, "same-form", "key-one");
  postCalls += 1;
  const result = parseAuthoringResult({
    schema: "agent_commons.gallery-authoring-result.v1",
    state: "published",
    design_package_id: `design_package.${ulid}`,
    revision: `evt.${nextUlid}`,
  });
  const settled = await settleAuthoringRefresh(
    result,
    async () => {
      galleryRefreshes += 1;
      await parseSuccessfulGalleryRefresh(new Response(JSON.stringify(response())));
    },
    async () => {
      candidateRefreshes += 1;
      throw new Error("candidate_refresh_failed");
    },
  );
  const retriedIntent = retainAuthoringIntent(intent, "same-form", "key-two");

  assert.deepEqual(settled, {
    kind: "saved_refresh_failed",
    revision: `evt.${nextUlid}`,
    code: "candidate_refresh_failed",
  });
  assert.equal(retriedIntent.key, "key-one");
  assert.equal(postCalls, 1);
  assert.equal(galleryRefreshes, 1);
  assert.equal(candidateRefreshes, 1);
  const copy = authoringSavedCopyKeys(settled);
  assert.deepEqual(copy, {
    summary: "authoring_saved_candidates_refresh_failed",
    help: "authoring_saved_candidates_refresh_failed_help",
  });
  for (const locale of ["en", "ru"]) {
    const visible = `${messages[locale][copy.summary]} ${messages[locale][copy.help]}`;
    assert.doesNotMatch(visible, /could not be published|no canonical success|не удалось опубликовать|успех не записан/i);
  }
});

test("typed non-2xx Gallery refresh remains a saved refresh failure and skips candidate refresh", async () => {
  let postCalls = 0;
  let galleryRefreshes = 0;
  let candidateRefreshes = 0;
  const intent = retainAuthoringIntent(null, "same-revision", "key-one");
  postCalls += 1;
  const result = parseAuthoringResult({
    schema: "agent_commons.gallery-authoring-result.v1",
    state: "revised",
    design_package_id: `design_package.${ulid}`,
    revision: `evt.${nextUlid}`,
  });
  const galleryError = () => new Response(JSON.stringify(response([], {
    state: "error",
    freshness: null,
    error: {
      code: "gallery_projection_unavailable",
      message: "Projection is unavailable",
      safe_next_actions: [],
    },
  })), { status: 503, headers: { "Content-Type": "application/json" } });

  await assert.rejects(parseSuccessfulGalleryRefresh(galleryError()), /gallery_projection_unavailable/);
  const settled = await settleAuthoringRefresh(
    result,
    async () => {
      galleryRefreshes += 1;
      await parseSuccessfulGalleryRefresh(galleryError());
    },
    async () => {
      candidateRefreshes += 1;
    },
  );
  const retriedIntent = retainAuthoringIntent(intent, "same-revision", "key-two");

  assert.deepEqual(settled, {
    kind: "saved_refresh_failed",
    revision: `evt.${nextUlid}`,
    code: "gallery_refresh_failed",
  });
  assert.equal(retriedIntent.key, "key-one");
  assert.equal(postCalls, 1);
  assert.equal(galleryRefreshes, 1);
  assert.equal(candidateRefreshes, 0);
});

test("empty and unavailable candidate refreshes cannot hide the saved exact revision", async () => {
  for (const candidateResponse of [
    {
      schema: "agent_commons.gallery-authoring.v1",
      state: "empty",
      writes_enabled: true,
      candidates: [],
      error: null,
    },
    {
      schema: "agent_commons.gallery-authoring.v1",
      state: "unavailable",
      writes_enabled: false,
      candidates: [],
      error: {
        code: "design_package_unavailable",
        message: "Writes are disabled",
        safe_next_actions: [],
      },
    },
  ]) {
    let postCalls = 0;
    const intent = retainAuthoringIntent(null, "unchanged-form", "key-one");
    postCalls += 1;
    const result = parseAuthoringResult({
      schema: "agent_commons.gallery-authoring-result.v1",
      state: "published",
      design_package_id: `design_package.${ulid}`,
      revision: `evt.${nextUlid}`,
    });
    let refreshedState = null;
    const settled = await settleAuthoringRefresh(
      result,
      async () => {
        await parseSuccessfulGalleryRefresh(new Response(JSON.stringify(response())));
      },
      async () => {
        refreshedState = parseAuthoringResponse(candidateResponse).state;
      },
    );
    const copy = authoringSavedCopyKeys(settled);

    assert.equal(refreshedState, candidateResponse.state);
    assert.deepEqual(settled, { kind: "success", revision: `evt.${nextUlid}` });
    assert.equal(copy.summary, "authoring_saved");
    assert.equal(messages.en[copy.summary], "Canonical revision saved");
    assert.equal(messages.ru[copy.summary], "Каноническая ревизия записана");
    assert.equal(retainAuthoringIntent(intent, "unchanged-form", "key-two").key, "key-one");
    assert.equal(postCalls, 1);
  }
});

test("a superseded saved-refresh continuation cannot clobber the newer submit", async () => {
  const requests = new AbortSlot();
  const first = requests.begin();
  let finishFirstRefresh;
  const firstRefresh = new Promise((resolve) => {
    finishFirstRefresh = resolve;
  });
  const staleMutations = [];
  const firstContinuation = firstRefresh.then((settled) => {
    if (!requests.isCurrent(first)) return;
    staleMutations.push(settled);
  });

  const second = requests.begin();
  assert.equal(first.signal.aborted, true);
  assert.equal(requests.isCurrent(second), true);
  finishFirstRefresh({ kind: "success", revision: `evt.${nextUlid}` });
  await firstContinuation;

  assert.deepEqual(staleMutations, []);
  assert.equal(requests.isCurrent(second), true);
  assert.match(
    authoringSource,
    /await settleAuthoringRefresh\([\s\S]*?if \(!submitRequests\.current\.isCurrent\(controller\)\) return;[\s\S]*?setSubmit\(settled\)/,
  );
});
