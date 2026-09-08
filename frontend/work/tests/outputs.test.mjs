import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const compiled = mkdtempSync(resolve(tmpdir(), "agent-commons-outputs-"));
execFileSync(resolve(root, "node_modules/.bin/tsc"), ["--ignoreConfig", "--target", "ES2022", "--module", "ESNext", "--moduleResolution", "Bundler", "--lib", "ES2022,DOM", "--jsx", "react-jsx", "--outDir", compiled, resolve(root, "src/components/OutputsPanel.tsx"), resolve(root, "src/api.ts")], { cwd: root });
symlinkSync(resolve(root, "node_modules"), resolve(compiled, "node_modules"), "dir");
const load = (path) => import(pathToFileURL(resolve(compiled, path)).href);
const { OutputsApi, presentCurrent } = await load("outputsApi.js");
const { parseOutputList, parseOutputSummary, OutputsError, canViewImage } = await load("outputsTypes.js");
const { WorkApi } = await load("api.js");
const { OutputsAction, ImagePreviewAction } = await load("components/OutputsPanel.js");
const id = (kind, n = "0") => `${kind}.${n.repeat(26)}`;
const scope = { kind: "task", id: id("task") };
const agentScope = { kind: "agent", id: id("agent") };
const summary = (overrides = {}) => ({ schema: "agent_commons.outputs-summary.v1", scope, versions: "latest", total: 1, counts: { unchecked: 1, stale: 0, unavailable: 0 }, previews_verified: false, ...overrides });
const row = (overrides = {}) => ({ kind: "design_image", output_id: `${id("design_package")}@${id("evt")}:${id("screen")}`, series_id: `${id("design_package")}:${id("screen")}`, title: "Home", package_id: id("design_package"), package_revision: id("evt"), screen_id: id("screen"), artifact_id: id("artifact"), artifact_revision: id("evt"), content_revision: `sha256:${"a".repeat(64)}`, task_id: scope.id, task_revision: id("evt"), producer_session_id: id("session"), producer_agent_id: agentScope.id, producer_delegation_id: id("delegation"), recorded_at: "2026-09-08T00:00:00Z", media_type: "image/png", classification: "internal", state: "ready", reason: null, latest: true, version_count: 1, width: 2, height: 3, ...overrides });
const list = (items = [row()], selection = scope, versions = "latest") => ({ schema: "agent_commons.outputs.v1", scope: selection, versions, items, truncated: false });
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const signal = () => new AbortController().signal;

test("summary counts stay metadata-only and reject mismatched scope or malformed totals", () => {
  assert.equal(parseOutputSummary(summary(), scope).total, 1);
  for (const value of [summary({ scope: agentScope }), summary({ total: 2 }), summary({ previews_verified: true }), summary({ private_path: "not accepted" })]) assert.throws(() => parseOutputSummary(value, scope), OutputsError);
});

test("output parser binds exact producer, series, version, and typed references", () => {
  assert.equal(parseOutputList(list(), scope, "latest").items[0].title, "Home");
  assert.equal(parseOutputList(list([row()], agentScope), agentScope, "latest").items.length, 1);
  for (const changed of [{ task_id: id("task", "1") }, { output_id: "wrong" }, { artifact_id: "artifact/path" }, { classification: "private" }, { latest: false }, { width: 20_000_000 }, { private_path: "not accepted" }, { reason: "raw error text" }]) assert.throws(() => parseOutputList(list([row(changed)]), scope, "latest"), OutputsError);
  assert.throws(() => parseOutputList(list([row({ producer_agent_id: id("agent", "1") })], agentScope), agentScope, "latest"), OutputsError);
  assert.throws(() => parseOutputList(list([row(), row()]), scope, "latest"), OutputsError);
});

test("latest scoped result remains explicitly stale; history never substitutes readiness", () => {
  const stale = row({ state: "stale", reason: "package_revision_superseded", width: null, height: null, version_count: 2 });
  const result = parseOutputList(list([stale]), scope, "latest").items[0];
  assert.equal(result.latest, true); assert.equal(result.state, "stale");
  const historical = parseOutputList(list([row({ ...stale, latest: false })], scope, "all"), scope, "all");
  assert.equal(historical.items[0].latest, false);
});

test("Results entry exists only for actual positive counts and supports both languages", () => {
  for (const count of [null, 0]) assert.equal(renderToStaticMarkup(createElement(OutputsAction, { count, locale: "en", title: "Home", onOpen() {} })), "");
  assert.match(renderToStaticMarkup(createElement(OutputsAction, { count: 2, locale: "en", title: "Home", onOpen() {} })), /Results for Home/);
  assert.match(renderToStaticMarkup(createElement(OutputsAction, { count: 2, locale: "ru", title: "Экран", onOpen() {} })), /Результаты/);
});

test("abort prevents both late successful content and late errors crossing selection boundaries", async () => {
  for (const success of [true, false]) {
    const pending = deferred(), controller = new AbortController(), seen = [];
    const handled = presentCurrent(pending.promise, controller.signal, (value) => seen.push(value), () => seen.push("error"));
    controller.abort(); success ? pending.resolve("old project image") : pending.reject(new Error("old project failure"));
    await handled; assert.deepEqual(seen, []);
  }
});

test("summaries deduplicate and refresh on revision; failed reads can retry", async () => {
  const calls = [];
  const api = new OutputsApi({ readOutputs(...args) { const item = deferred(); calls.push({ args, ...item }); return item.promise; } });
  const first = api.summary(scope, "rev1"), duplicate = api.summary(scope, "rev1");
  assert.equal(first, duplicate); assert.equal(calls.length, 1);
  const second = api.summary(scope, "rev2"); assert.equal(calls[0].args[4].aborted, true);
  calls[0].reject(new Error("old revision")); await assert.rejects(first);
  assert.equal(api.summary(scope, "rev2"), second);
  calls[1].reject(new Error("temporary")); await assert.rejects(second);
  const third = api.summary(scope, "rev2"); calls[2].resolve(summary()); assert.equal((await third).total, 1);
  api.dispose(); assert.equal(calls.length, 3);
});

test("preview requires exact digest plus a still-current scoped record after byte download", async () => {
  const blob = new Blob([new Uint8Array([1, 2, 3])], { type: "image/png" });
  const digest = `sha256:${Buffer.from(await crypto.subtle.digest("SHA-256", await blob.arrayBuffer())).toString("hex")}`;
  const ready = row({ content_revision: digest });
  let response = list([ready]), calls = 0;
  const api = new OutputsApi({ readOutputImage: async () => { calls++; return blob; }, readOutputs: async () => response });
  const item = parseOutputList(response, scope, "latest").items[0];
  assert.equal(await api.preview(scope, item, "latest", signal()), blob);
  response = list([row({ ...ready, state: "stale", reason: "artifact_revision_changed", width: null, height: null })]);
  await assert.rejects(api.preview(scope, item, "latest", signal()), OutputsError);
  response = list([ready]); await assert.rejects(api.preview(scope, { ...item, contentRevision: `sha256:${"f".repeat(64)}` }, "latest", signal()), OutputsError);
  await assert.rejects(api.preview(scope, { ...item, state: "stale" }, "latest", signal()), OutputsError);
  assert.equal(calls, 3);
});

test("output requests and image bytes retain immutable project identity", async () => {
  const previous = globalThis.fetch, calls = [];
  const host = Object.assign(new WorkApi(), { apiBase: "/api/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" });
  const first = host.forProject("project.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"), second = host.forProject("project.bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb");
  globalThis.fetch = async (url, init) => { calls.push({ url, init }); return url.includes("/preview") ? new Response(new Uint8Array([1]), { headers: { "Content-Type": "image/png" } }) : new Response(JSON.stringify(summary()), { headers: { "Content-Type": "application/json" } }); };
  try {
    await first.readOutputs("task", scope.id, "latest", true, signal());
    await second.readOutputs("agent", agentScope.id, "all", false, signal());
    await first.readOutputImage(id("artifact"), signal());
    assert.match(calls[0].url, /\/projects\/project.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\/outputs\/summary\?scope_kind=task/);
    assert.match(calls[1].url, /\/projects\/project.bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\/outputs\?scope_kind=agent.*versions=all/);
    assert.match(calls[2].url, /\/projects\/project.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\/artifacts\/artifact\./);
    assert.equal(calls[2].init.redirect, "error");
    await assert.rejects(first.readOutputs("task", `${scope.id}?other=1`, "latest", true, signal()));
    await assert.rejects(first.readOutputImage("../config", signal()));
    assert.equal(calls.length, 3);
  } finally { globalThis.fetch = previous; }
});

test("preview transport rejects non-images, empty bytes, declared and streamed oversize", async () => {
  const previous = globalThis.fetch;
  const api = Object.assign(new WorkApi(), { apiBase: "/api/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" });
  try {
    for (const response of [new Response("<svg/>", { headers: { "Content-Type": "image/svg+xml" } }), new Response(new Uint8Array(), { headers: { "Content-Type": "image/png" } }), new Response(new Uint8Array([1]), { headers: { "Content-Type": "image/png", "Content-Length": "10485761" } }), new Response(new Uint8Array(10485761), { headers: { "Content-Type": "image/png" } })]) {
      globalThis.fetch = async () => response;
      await assert.rejects(api.readOutputImage(id("artifact"), signal()));
    }
  } finally { globalThis.fetch = previous; }
});

const liveRow = (overrides = {}) => ({ kind: "live_preview", output_id: `preview.${"a".repeat(32)}`, title: "Frontend", task_id: scope.id, task_revision: id("evt"), producer_agent_id: agentScope.id, producer_session_id: `session.${"b".repeat(32)}`, producer_delegation_id: id("delegation"), delegation_revision: id("evt"), url: "http://127.0.0.1:5173/", origin: "http://127.0.0.1:5173", published_at: "2026-09-08T00:00:00Z", expires_at: "2026-09-08T00:01:00Z", reported_state: "ready", state: "reported_ready", reachability: "unknown", latest: true, version_count: 1, ...overrides });

test("producer session parsing accepts actual UUID session identities without weakening other refs", () => {
  const actual = row({ producer_session_id: `session.${"b".repeat(32)}` });
  assert.equal(parseOutputList(list([actual]), scope, "latest").items[0].kind, "design_image");
  for (const session of ["session.raw-provider-text", `session.${"g".repeat(32)}`, `session.${"b".repeat(32)}?token=x`]) assert.throws(() => parseOutputList(list([row({ producer_session_id: session })]), scope, "latest"), OutputsError);
});

test("live previews are distinct metadata, require scoped producer, and reject URL tricks", () => {
  assert.equal(parseOutputList(list([liveRow()]), scope, "latest").items[0].kind, "live_preview");
  for (const updates of [{ task_id: id("task", "1") }, { producer_session_id: "fake" }, { origin: "http://127.1:5173", url: "http://127.1:5173/" }, { origin: "http://localhost:5173", url: "http://localhost:5173/" }, { url: "http://127.0.0.1:5173/?secret=token" }, { url: "http://127.0.0.1:5173/#secret" }, { reachability: "verified" }, { state: "expired" }, { state: "starting" }, { expires_at: "2026-09-09T00:00:00Z" }, { raw_source: "private" }]) assert.throws(() => parseOutputList(list([liveRow(updates)]), scope, "latest"), OutputsError);
});

test("live navigation expires locally and never links to the control server or an iframe", async () => {
  const { liveNavigation } = await load("outputsTypes.js");
  const { LivePreviewCard } = await load("components/OutputsPanel.js");
  const item = parseOutputList(list([liveRow()]), scope, "latest").items[0], before = item.publishedAt + 10_000;
  assert.equal(liveNavigation(item, before, "http://127.0.0.1:8080"), item.url);
  assert.equal(liveNavigation(item, item.expiresAt, "http://127.0.0.1:8080"), null);
  assert.equal(liveNavigation(item, before, "http://[::1]:5173"), null);
  const markup = renderToStaticMarkup(createElement(LivePreviewCard, { item, locale: "en", now: before, uiOrigin: "http://127.0.0.1:8080" }));
  assert.match(markup, /Publisher reports ready/); assert.match(markup, /Reachability has not been checked/);
  assert.match(markup, /target="_blank"/); assert.match(markup, /rel="noopener noreferrer"/); assert.doesNotMatch(markup, /iframe|<img/);
  const expired = renderToStaticMarkup(createElement(LivePreviewCard, { item, locale: "ru", now: item.expiresAt, uiOrigin: "http://127.0.0.1:8080" }));
  assert.match(expired, /Срок истёк/); assert.doesNotMatch(expired, /href=/);
});

test("summary cache expires without canonical changes so runtime advertisements become discoverable", async () => {
  const original = Date.now; let now = 1000, calls = 0;
  Date.now = () => now;
  try {
    const api = new OutputsApi({ readOutputs: async () => { calls++; return summary(); } });
    await api.summary(scope, "same-canonical-revision");
    await api.summary(scope, "same-canonical-revision"); assert.equal(calls, 1);
    now += 15_000; await api.summary(scope, "same-canonical-revision"); assert.equal(calls, 2);
    api.dispose();
  } finally { Date.now = original; }
});


const generatedRow = (overrides = {}) => {
  const { package_id, package_revision, screen_id, ...image } = row();
  return { ...image, kind: "artifact_image", output_id: `${image.artifact_id}@${image.artifact_revision}`, series_id: `generated.${"a".repeat(64)}`, delegation_revision: id("evt"), historical_preview_verified: false, ...overrides };
};
test("generated images retain exact producer boundaries without claiming a Design Package", async () => {
  const blob = new Blob([new Uint8Array([1,2,3])], {type:"image/png"});
  const digest = `sha256:${Buffer.from(await crypto.subtle.digest("SHA-256", await blob.arrayBuffer())).toString("hex")}`;
  const wire = generatedRow({content_revision:digest});
  let response = list([wire]);
  const item = parseOutputList(response, scope, "latest").items[0];
  assert.equal(item.kind, "artifact_image");
  assert.equal(parseOutputList(list([wire],agentScope),agentScope,"latest").items.length,1);
  for (const changed of [{package_id:id("design_package")},{producer_session_id:null},{producer_agent_id:null},{delegation_revision:"wrong"},{series_id:"path/to/file.png"},{task_id:id("task","1")},{output_id:"wrong"}]) {
    assert.throws(()=>parseOutputList(list([generatedRow(changed)]),scope,"latest"),OutputsError);
  }
  const api = new OutputsApi({readOutputImage:async()=>blob,readOutputs:async()=>response});
  assert.equal(await api.preview(scope,item,"latest",signal()),blob);
  response=list([generatedRow({...wire,state:"stale",reason:"artifact_revision_changed",width:null,height:null})]);
  await assert.rejects(api.preview(scope,item,"latest",signal()),OutputsError);
});


test("completed-task recorded images remain stale but exact verified bytes can be viewed", async () => {
  const blob = new Blob([new Uint8Array([1,2,3])], {type:"image/png"});
  const digest = `sha256:${Buffer.from(await crypto.subtle.digest("SHA-256", await blob.arrayBuffer())).toString("hex")}`;
  const wire = generatedRow({state:"stale", reason:"producer_task_revision_changed", width:null, height:null, historical_preview_verified:true, content_revision:digest});
  let response = list([wire]);
  const item = parseOutputList(response, scope, "latest").items[0];
  assert.equal(item.state,"stale"); assert.equal(canViewImage(item),true);
  const api = new OutputsApi({readOutputImage:async()=>blob,readOutputs:async()=>response});
  assert.equal(await api.preview(scope,item,"latest",signal()),blob);
  response = list([generatedRow({...wire,historical_preview_verified:false})]);
  await assert.rejects(api.preview(scope,item,"latest",signal()),OutputsError);
  response = list([wire]);
  await assert.rejects(api.preview(scope,{...item,contentRevision:`sha256:${"f".repeat(64)}`},"latest",signal()),OutputsError);
  for (const changed of [{state:"ready",reason:null,width:2,height:3},{reason:"artifact_preview_stale_source"},{classification:"restricted"},{producer_agent_id:id("agent","1")}]) {
    const selection = changed.producer_agent_id ? agentScope : scope;
    assert.throws(()=>parseOutputList(list([generatedRow({...wire,...changed})],selection),selection,"latest"),OutputsError);
  }
  assert.equal(canViewImage({...item,kind:"design_image"}),false);
  assert.equal(canViewImage({...item,historicalPreviewVerified:false}),false);
});


test("verified task history renders a recorded-image action without implying current readiness", () => {
  const item = parseOutputList(list([generatedRow({state:"stale",reason:"producer_task_revision_changed",historical_preview_verified:true,width:null,height:null})]),scope,"latest").items[0];
  const markup = renderToStaticMarkup(createElement(ImagePreviewAction,{item,locale:"en",onOpen(){}}));
  assert.match(markup,/View recorded image/); assert.match(markup,/does not mark the result current/);
  assert.doesNotMatch(markup,/cannot be previewed|Ready to view/);
  const refused = renderToStaticMarkup(createElement(ImagePreviewAction,{item:{...item,historicalPreviewVerified:false},locale:"en",onOpen(){}}));
  assert.doesNotMatch(refused,/<button/);
});
