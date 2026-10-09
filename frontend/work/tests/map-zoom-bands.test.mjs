import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { getViewportForBounds } from "@xyflow/react";
import { fileURLToPath, pathToFileURL } from "node:url";

/**
 * UX-01, UX-04 and UX-10 all reduce to arithmetic on the map's `viewBox`:
 * which reading band a zoom level names, the smallest pan that reveals a
 * focused node, the explicit working-zoom action, and whether a resize is
 * worth offering a refit. These are behaviour tests on that arithmetic — not
 * a regex over the source — because the defects they guard are numeric: a
 * camera that moves when it should not, a focus that lands off-frame, a band
 * that strobes on a threshold, an unconditional auto-fit.
 */

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const compiled = mkdtempSync(resolve(tmpdir(), "commons-map-bands-"));
execFileSync(resolve(root, "node_modules/.bin/tsc"), [
  "--ignoreConfig", "--target", "ES2022", "--module", "ESNext", "--moduleResolution", "Bundler",
  "--lib", "ES2022,DOM", "--outDir", compiled,
  ...["mapViewport.ts", "mapZoomBands.ts"].map((file) => resolve(root, "src", file))
], { cwd: root });
symlinkSync(resolve(root, "node_modules"), resolve(compiled, "node_modules"), "dir");
const load = (file) => import(pathToFileURL(resolve(compiled, file + ".js")).href);
const { fitViewport, minimumPaddedMapZoom, reframeFlowViewport, reframeViewport, viewportZoom, zoomViewport } = await load("mapViewport");
const {
  BAND_HYSTERESIS, FAR_BAND_MAX, OVERVIEW_BAND_MAX, REVEAL_MARGIN_PIXELS, WORKING_ZOOM_MIN, WORKING_ZOOM_TARGET,
  bandHidesNodeActions, ensureVisible, frameChangedEnoughForFitHint, workingZoomLevel, workingZoomViewport, zoomBand
} = await load("mapZoomBands");

const FRAME = { width: 900, height: 600 };
test("agent camera retains world centre and scale across drawer and narrow-screen resizes", () => {
  for (const zoom of [.025, .2, 1, 1.6]) for (const width of [322, 818, 1200]) {
    const before = { width: 818, height: 461 }, after = { width, height: 580 };
    const camera = { x: -400, y: -100, zoom }, next = reframeFlowViewport(camera, before, after);
    assert.equal(next.zoom, zoom);
    assert.equal((before.width / 2 - camera.x) / zoom, (after.width / 2 - next.x) / zoom);
    assert.equal((before.height / 2 - camera.y) / zoom, (after.height / 2 - next.y) / zoom);
  }
  assert.deepEqual(reframeFlowViewport({ x: 10, y: 20, zoom: 1 }, { width: 0, height: 0 }, FRAME), { x: 10, y: 20, zoom: 1 });
});
test("agent Fit covers a wide or tall 50-agent tree and remote saved positions at both padding values", () => {
  const extents = [{ width: 49 * 264 + 48 * 48, height: 756 }, { width: 264, height: 50 * 320 }, { width: 2000264, height: 2000248 }];
  for (const content of extents) for (const frame of [{ width: 818, height: 461 }, { width: 322, height: 373 }]) {
    const bounds = { x: -500, y: -188, ...content };
    const minimum = minimumPaddedMapZoom(content, frame, 1);
    assert.ok(minimum > 0 && minimum < .2);
    for (const padding of [.1, .2]) {
      const viewport = getViewportForBounds(bounds, frame.width, frame.height, minimum, 1.6, padding);
      const left = bounds.x * viewport.zoom + viewport.x, top = bounds.y * viewport.zoom + viewport.y;
      assert.ok(left >= 0 && top >= 0);
      assert.ok(left + bounds.width * viewport.zoom <= frame.width);
      assert.ok(top + bounds.height * viewport.zoom <= frame.height);
      assert.equal(minimumPaddedMapZoom(content, { width: frame.width * 3, height: frame.height * 3 }, viewport.zoom), Math.min(.2, viewport.zoom, frame.width * 3 / (content.width * 2), frame.height * 3 / (content.height * 2)));
    }
  }
  assert.ok(Number.isFinite(minimumPaddedMapZoom({ width: 0, height: 0 }, { width: 0, height: 0 }, 1)));
});
/** A map a few screens wide, so panning has somewhere to go. */
const CONTENT = { width: 4000, height: 3000 };
const NODE = { width: 244, height: 168 };
const rect = (x, y) => ({ x, y, width: NODE.width, height: NODE.height });
/** A viewport at an exact zoom, placed at an exact map-space origin. */
const at = (zoom, x, y) => ({ x, y, width: FRAME.width / zoom, height: FRAME.height / zoom, frame: FRAME });

test("a zoom level names one reading band, and the band hides microactions away from work", () => {
  assert.equal(zoomBand(0.2), "far");
  assert.equal(zoomBand(0.45), "overview");
  assert.equal(zoomBand(1), "work");
  // The thresholds themselves belong to the band above them.
  assert.equal(zoomBand(FAR_BAND_MAX), "overview");
  assert.equal(zoomBand(OVERVIEW_BAND_MAX), "work");
  assert.equal(bandHidesNodeActions("far"), true);
  assert.equal(bandHidesNodeActions("overview"), true);
  assert.equal(bandHidesNodeActions("work"), false);
});

test("hysteresis keeps a drag that hovers on a threshold from strobing between bands", () => {
  // Coming from work, the threshold alone does not demote: the zoom has to
  // fall past it by the hysteresis.
  assert.equal(zoomBand(OVERVIEW_BAND_MAX - BAND_HYSTERESIS / 2, "work"), "work");
  assert.equal(zoomBand(OVERVIEW_BAND_MAX - BAND_HYSTERESIS * 1.5, "work"), "overview");
  // Coming from overview, it does not promote until past the threshold either.
  assert.equal(zoomBand(OVERVIEW_BAND_MAX + BAND_HYSTERESIS / 2, "overview"), "overview");
  assert.equal(zoomBand(OVERVIEW_BAND_MAX + BAND_HYSTERESIS * 1.5, "overview"), "work");
  assert.equal(zoomBand(FAR_BAND_MAX - BAND_HYSTERESIS / 2, "far"), "far");
  assert.equal(zoomBand(FAR_BAND_MAX + BAND_HYSTERESIS * 1.5, "far"), "overview");
  // A band never skips a step in either direction from one zoom reading.
  assert.equal(zoomBand(0.05, "work"), "far");
  assert.equal(zoomBand(2, "far"), "work");
});

test("an already visible node is revealed by doing nothing at all to the camera", () => {
  const viewport = at(1, 0, 0);
  const next = ensureVisible(viewport, FRAME, CONTENT, rect(300, 200));
  // The same object: the caller compares by identity and skips the write, so
  // following focus with Arrow/Home/End cannot nudge a stable map.
  assert.equal(next, viewport);
});

test("a node outside the frame is revealed by the smallest pan, with the zoom untouched", () => {
  const viewport = at(1, 0, 0);
  const node = rect(1200, 900);
  const next = ensureVisible(viewport, FRAME, CONTENT, node, REVEAL_MARGIN_PIXELS);
  assert.notEqual(next, viewport);
  assert.equal(viewportZoom(next, FRAME), viewportZoom(viewport, FRAME));
  // Minimal: the node's far edge lands on the frame's far edge plus the margin.
  assert.equal(Math.round(next.x), node.x + node.width + REVEAL_MARGIN_PIXELS - FRAME.width);
  assert.equal(Math.round(next.y), node.y + node.height + REVEAL_MARGIN_PIXELS - FRAME.height);
  // And it really is inside afterwards.
  assert.ok(node.x >= next.x && node.x + node.width <= next.x + next.width);
  assert.ok(node.y >= next.y && node.y + node.height <= next.y + next.height);
});

test("the reveal margin is screen pixels, so a zoomed-out map pans further in map units", () => {
  const close = ensureVisible(at(1, 0, 0), FRAME, CONTENT, rect(1200, 0), 20);
  const far = ensureVisible(at(0.5, 0, 0), FRAME, CONTENT, rect(2600, 0), 20);
  assert.equal(Math.round(close.x), 1200 + NODE.width + 20 - FRAME.width);
  // At 50% the same 20 screen pixels are 40 map units.
  assert.equal(Math.round(far.x), 2600 + NODE.width + 40 - FRAME.width / 0.5);
});

test("a node larger than the frame is centred rather than promised whole, and the zoom still does not change", () => {
  const viewport = at(2, 0, 0);
  const huge = { x: 1000, y: 1000, width: 900, height: 800 };
  const next = ensureVisible(viewport, FRAME, CONTENT, huge);
  assert.equal(viewportZoom(next, FRAME), 2);
  assert.equal(Math.round(next.x + next.width / 2), huge.x + huge.width / 2);
  assert.equal(Math.round(next.y + next.height / 2), huge.y + huge.height / 2);
});

test("working zoom lands on a readable scale centred on the given node", () => {
  const overview = at(0.35, 0, 0);
  const node = rect(2000, 1500);
  const next = workingZoomViewport(overview, FRAME, CONTENT, node);
  assert.equal(Math.round(viewportZoom(next, FRAME) * 100) / 100, WORKING_ZOOM_TARGET);
  assert.equal(Math.round(next.x + next.width / 2), node.x + node.width / 2);
  assert.equal(Math.round(next.y + next.height / 2), node.y + node.height / 2);
});

test("a zoom already inside the readable range is re-centred and not re-scaled", () => {
  const reading = at(0.9, 0, 0);
  const node = rect(1500, 1200);
  const next = workingZoomViewport(reading, FRAME, CONTENT, node);
  assert.ok(viewportZoom(next, FRAME) >= WORKING_ZOOM_MIN);
  assert.equal(Math.round(viewportZoom(next, FRAME) * 100) / 100, 0.9);
  assert.equal(Math.round(next.x + next.width / 2), node.x + node.width / 2);
});

test("working zoom without any node changes the scale only, and never resets the position to the origin", () => {
  const zoomedOut = at(0.25, 1800, 1400);
  const next = workingZoomViewport(zoomedOut, FRAME, CONTENT, null);
  assert.equal(Math.round(viewportZoom(next, FRAME) * 100) / 100, WORKING_ZOOM_TARGET);
  // The centre the operator was reading is the centre it zooms around.
  assert.equal(Math.round(next.x + next.width / 2), Math.round(zoomedOut.x + zoomedOut.width / 2));
  assert.notEqual(next.x, 0);
});

test("the fit hint is offered only after a real frame change, and offering it changes no camera", () => {
  assert.equal(frameChangedEnoughForFitHint(FRAME, FRAME), false);
  assert.equal(frameChangedEnoughForFitHint(FRAME, { width: 900 * 1.1, height: 600 }), false);
  assert.equal(frameChangedEnoughForFitHint(FRAME, { width: 900 * 1.3, height: 600 }), true);
  assert.equal(frameChangedEnoughForFitHint(FRAME, { width: 900, height: 600 * 0.7 }), true);
  // An unmeasured or degenerate frame never produces a hint, so a first paint
  // cannot be mistaken for a resize.
  assert.equal(frameChangedEnoughForFitHint(undefined, FRAME), false);
  assert.equal(frameChangedEnoughForFitHint({ width: 0, height: 0 }, FRAME), false);
});

test("a resize keeps the reading scale, so nothing in this wave auto-fits or resets a position", () => {
  // The hint exists exactly because the camera is preserved: fitting on resize
  // would be the defect UX-10 names.
  const manual = zoomViewport(at(1, 500, 400), FRAME, 1.4, { x: 0.5, y: 0.5 }, CONTENT);
  const fitted = fitViewport(CONTENT, FRAME);
  assert.notEqual(Math.round(viewportZoom(manual, FRAME) * 100), Math.round(viewportZoom(fitted, FRAME) * 100));
  assert.equal(frameChangedEnoughForFitHint(manual.frame, { width: 500, height: 600 }), true);
  // The hint is a statement, not an action: the viewport it was computed from
  // is returned untouched by asking the question.
  assert.equal(manual.x, zoomViewport(at(1, 500, 400), FRAME, 1.4, { x: 0.5, y: 0.5 }, CONTENT).x);
});


test("keyboard reveal uses a smaller live frame even when the saved frame still contains the node", () => {
  const saved = at(1, 0, 0);
  const frame = { width: 450, height: 300 };
  const node = rect(600, 400);
  // This used to do nothing: the node fits the saved 900x600 view, but not
  // the centre-preserving 450x300 view the resized canvas actually displays.
  assert.equal(ensureVisible(saved, FRAME, CONTENT, node), saved);
  const displayed = reframeViewport(saved, frame);
  assert.ok(node.x + node.width > displayed.x + displayed.width);
  assert.ok(node.y + node.height > displayed.y + displayed.height);
  const next = ensureVisible(saved, frame, CONTENT, node);
  assert.deepEqual(next.frame, frame);
  assert.equal(viewportZoom(next, frame), 1);
  assert.equal(next.x, node.x + node.width + REVEAL_MARGIN_PIXELS - frame.width);
  assert.equal(next.y, node.y + node.height + REVEAL_MARGIN_PIXELS - frame.height);
  assert.ok(node.x >= next.x && node.x + node.width <= next.x + next.width);
  assert.ok(node.y >= next.y && node.y + node.height <= next.y + next.height);
  assert.equal(ensureVisible(next, frame, CONTENT, node), next, "repeat focus does not move the camera");
  assert.deepEqual(saved, at(1, 0, 0), "the saved reading position is not mutated");
});

test("a wider live frame reveals an already visible node without panning to the old frame", () => {
  const saved = at(1, 800, 500);
  const frame = { width: 1350, height: 900 };
  const node = rect(600, 380);
  const displayed = reframeViewport(saved, frame);
  // The node is outside the saved rectangle but within the enlarged display.
  assert.ok(node.x < saved.x && node.y < saved.y);
  const next = ensureVisible(saved, frame, CONTENT, node);
  assert.deepEqual(next, displayed);
  assert.equal(viewportZoom(next, frame), 1);
  assert.equal(next.x + next.width / 2, saved.x + saved.width / 2);
  assert.equal(next.y + next.height / 2, saved.y + saved.height / 2);
});

test("working zoom preserves a readable scale and centres the target after either resize direction", () => {
  const saved = at(0.9, 800, 500);
  const node = rect(1800, 1400);
  for (const frame of [{ width: 450, height: 300 }, { width: 1350, height: 900 }]) {
    const next = workingZoomViewport(saved, frame, CONTENT, node);
    assert.equal(Math.round(viewportZoom(next, frame) * 100), 90);
    assert.deepEqual(next.frame, frame);
    assert.equal(next.x + next.width / 2, node.x + node.width / 2);
    assert.equal(next.y + next.height / 2, node.y + node.height / 2);
    assert.deepEqual(workingZoomViewport(next, frame, CONTENT, node), next);
  }
});


test("agent and task maps share the same deliberate working scale", () => {
  for (const value of [0.2, 0.5, 1.2, NaN]) assert.equal(workingZoomLevel(value), 1);
  for (const value of [0.8, 0.9, 1]) assert.equal(workingZoomLevel(value), value);
});
