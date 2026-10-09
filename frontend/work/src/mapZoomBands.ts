/**
 * Reading bands and the two deliberate camera moves of a task map.
 *
 * The council's UX-01 and UX-04 both come down to arithmetic on the `viewBox`
 * rectangle `mapViewport.ts` already owns, so everything here is a pure
 * function over a viewport, a frame in pixels and a node rectangle in map
 * units. Nothing measures the document, scrolls it, or writes a position.
 *
 * - `zoomBand` names the reading distance. Secondary fields and per-node
 *   microactions are hidden by CSS on that name, never shrunk; a hidden
 *   control is `display:none`, so it leaves the tab order with it.
 * - `ensureVisible` is the keyboard reveal: the smallest pan that brings a
 *   focused node inside the frame, and *the same viewport* when the node is
 *   already visible, so following focus never nudges the camera.
 * - `workingZoomViewport` is the explicit "working zoom" action: about 100%
 *   around a real node, so the full-size card is readable again.
 */
import { clampViewport, reframeViewport, viewportZoom, zoomViewport, type Extent, type Viewport } from "./mapViewport.js";

/** A node's rectangle in map units — exactly what the layout already carries. */
export type MapRect = Readonly<{ x: number; y: number; width: number; height: number }>;

export type ZoomBand = "far" | "overview" | "work";
export const ZOOM_BANDS: readonly ZoomBand[] = ["far", "overview", "work"];

/** Starting design parameters, to be re-calibrated only against a measurement. */
export const FAR_BAND_MAX = 0.3;
export const OVERVIEW_BAND_MAX = 0.6;
/** Keeps a border wobble from flipping the band on every pointer move. */
export const BAND_HYSTERESIS = 0.03;
/** The readable range the working-zoom action lands in. */
export const WORKING_ZOOM_MIN = 0.8;
export const WORKING_ZOOM_TARGET = 1;
/** Keyboard reveal margin, in screen pixels, converted by the live zoom. */
export const REVEAL_MARGIN_PIXELS = 14;

const finite = (value: number, fallback: number): number => (Number.isFinite(value) ? value : fallback);

/**
 * The band a zoom level reads as. `previous` applies hysteresis: a band only
 * changes once the zoom is past the threshold by `BAND_HYSTERESIS`, so a drag
 * that hovers on 0.60 does not strobe between overview and work.
 */
export function zoomBand(zoom: number, previous: ZoomBand | null = null): ZoomBand {
  const value = finite(zoom, 1);
  const lower = (threshold: number): number => threshold - BAND_HYSTERESIS;
  const upper = (threshold: number): number => threshold + BAND_HYSTERESIS;
  if (previous === "far") return value >= upper(FAR_BAND_MAX) ? (value >= upper(OVERVIEW_BAND_MAX) ? "work" : "overview") : "far";
  if (previous === "work") return value < lower(OVERVIEW_BAND_MAX) ? (value < lower(FAR_BAND_MAX) ? "far" : "overview") : "work";
  if (previous === "overview") {
    if (value < lower(FAR_BAND_MAX)) return "far";
    return value >= upper(OVERVIEW_BAND_MAX) ? "work" : "overview";
  }
  if (value < FAR_BAND_MAX) return "far";
  return value < OVERVIEW_BAND_MAX ? "overview" : "work";
}

/** True when the band hides per-node microactions and secondary fields. */
export function bandHidesNodeActions(band: ZoomBand): boolean {
  return band !== "work";
}

/**
 * The scale an explicit working-zoom action lands on.
 *
 * A zoom already inside 0.8–1.0 is a deliberate reading scale and is kept, so
 * pressing the action twice does not fight it; anything else lands on 100%.
 * Split out from `workingZoomViewport` because the agent board's camera is a
 * plain zoom number rather than a `viewBox`, and both maps must agree on the
 * readable range.
 */
export function workingZoomLevel(current: number): number {
  const value = finite(current, 1);
  return value >= WORKING_ZOOM_MIN && value <= WORKING_ZOOM_TARGET ? value : WORKING_ZOOM_TARGET;
}

/**
 * The smallest pan that brings `rect` inside the viewport, with a margin of
 * `marginPixels` screen pixels converted to map units at the live zoom.
 *
 * Reconciles a resized frame before calculating visibility. Returns the given
 * viewport unchanged — the same object — when its frame and visibility agree, so a caller can skip the write and the camera stays put.
 * A node larger than the frame cannot be shown whole: it is centred on the
 * axis that does not fit, and the zoom is never touched either way.
 */
export function ensureVisible(
  viewport: Viewport,
  frame: Extent,
  content: Extent,
  rect: MapRect,
  marginPixels: number = REVEAL_MARGIN_PIXELS
): Viewport {
  viewport = reframeViewport(viewport, frame);
  const zoom = viewportZoom(viewport, frame);
  const margin = Math.max(0, finite(marginPixels, 0)) / (zoom > 0 ? zoom : 1);
  const axis = (start: number, size: number, viewStart: number, viewSize: number): number => {
    const low = start - margin;
    const high = start + size + margin;
    if (high - low >= viewSize) return start + size / 2 - viewSize / 2;
    if (low < viewStart) return low;
    if (high > viewStart + viewSize) return high - viewSize;
    return viewStart;
  };
  const x = axis(rect.x, Math.max(0, rect.width), viewport.x, viewport.width);
  const y = axis(rect.y, Math.max(0, rect.height), viewport.y, viewport.height);
  if (x === viewport.x && y === viewport.y) return viewport;
  // Clamping stays inside the usual overscroll limit, so a reveal can never
  // leave the map in a place a gesture could not reach.
  return clampViewport({ ...viewport, x, y }, content);
}

/**
 * The explicit working-zoom action: a readable scale centred on one real node.
 *
 * A zoom already inside 0.8–1.0 is kept and only re-centred, so pressing the
 * action twice does not fight a deliberate reading scale. Anything outside
 * that range lands on 100%. `rect` is the selected node when there is one and
 * the caller's first real node otherwise; without any node the frame only
 * changes scale.
 */
export function workingZoomViewport(
  viewport: Viewport,
  frame: Extent,
  content: Extent,
  rect: MapRect | null
): Viewport {
  viewport = reframeViewport(viewport, frame);
  const scaled = zoomViewport(viewport, frame, workingZoomLevel(viewportZoom(viewport, frame)), { x: 0.5, y: 0.5 }, content);
  if (rect === null) return scaled;
  return clampViewport({
    ...scaled,
    x: rect.x + Math.max(0, rect.width) / 2 - scaled.width / 2,
    y: rect.y + Math.max(0, rect.height) / 2 - scaled.height / 2
  }, content);
}

/**
 * Whether the frame has changed enough since the reading position was last
 * written to be worth offering "fit everything" again.
 *
 * This only decides whether to *offer* it. A resize keeps the operator's
 * centre and scale (`reframeViewport`); nothing here refits, and nothing here
 * discards a manual position.
 */
export const FIT_HINT_FRAME_CHANGE = 0.25;

export function frameChangedEnoughForFitHint(previous: Extent | undefined, frame: Extent): boolean {
  if (!previous || previous.width <= 0 || previous.height <= 0 || frame.width <= 0 || frame.height <= 0) return false;
  const ratio = (before: number, after: number): number => Math.abs(after - before) / before;
  return Math.max(ratio(previous.width, frame.width), ratio(previous.height, frame.height)) >= FIT_HINT_FRAME_CHANGE;
}
