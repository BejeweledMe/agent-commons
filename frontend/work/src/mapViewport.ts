/**
 * The task map's viewport: a numeric SVG `viewBox` over the derived layout.
 *
 * Zoom and pan are plain arithmetic on that rectangle, so they work under the
 * strict CSP with no inline styles and no transform plumbing. The viewport is
 * the operator's reading position, not data: it is kept in memory per project,
 * per map kind and per focused branch, and an automatic refresh never refits
 * or resets it.
 */
export type Extent = Readonly<{ width: number; height: number }>;
/** The visible rectangle in layout units: exactly what the `viewBox` carries. */
export type Viewport = Readonly<{ x: number; y: number; width: number; height: number; frame?: Extent }>;

export const MIN_MAP_ZOOM = 0.2;
export const MAX_MAP_ZOOM = 4;
/** One keyboard/button step, as a zoom factor. */
export const ZOOM_STEP = 1.25;
/** How far past the content a person may pan, as a fraction of the frame. */
const OVERSCROLL = 0.5;

const positive = (value: number, fallback: number): number =>
  Number.isFinite(value) && value > 0 ? value : fallback;

export function boundedZoom(zoom: number, minimum = MIN_MAP_ZOOM): number {
  if (!Number.isFinite(zoom)) return 1;
  return Math.min(MAX_MAP_ZOOM, Math.max(positive(minimum, MIN_MAP_ZOOM), zoom));
}

/** The zoom a viewport represents inside a frame of this pixel size. */
export function viewportZoom(viewport: Viewport, frame: Extent): number {
  return positive(positive(frame.width, 1) / positive(viewport.width, 1), 1);
}

/** Fit must cover every node, even when a wide map needs less than 20%. */
function fitZoom(content: Extent, frame: Extent): number {
  const width = positive(content.width, 1), height = positive(content.height, 1);
  return Math.min(MAX_MAP_ZOOM, positive(frame.width, width) / width, positive(frame.height, height) / height);
}

/** Ordinary maps keep the familiar 20% floor; larger maps may zoom out to fit. */
export function minimumMapZoom(content: Extent, frame: Extent): number {
  return Math.min(MIN_MAP_ZOOM, fitZoom(content, frame));
}

/** React Flow's Fit includes padding; preserve an already smaller reading scale. */
export function minimumPaddedMapZoom(content: Extent, frame: Extent, current: number): number {
  return Math.min(positive(current, MIN_MAP_ZOOM), minimumMapZoom(
    { width: content.width * 2, height: content.height * 2 }, frame
  ));
}

/** Translate a pixel-transform camera to keep its world-space centre on resize. */
export function reframeFlowViewport(viewport: Readonly<{ x: number; y: number; zoom: number }>, before: Extent, after: Extent): { x: number; y: number; zoom: number } {
  if (before.width <= 0 || before.height <= 0 || after.width <= 0 || after.height <= 0) return { ...viewport };
  return { x: viewport.x + (after.width - before.width) / 2, y: viewport.y + (after.height - before.height) / 2, zoom: viewport.zoom };
}

/** The whole content, centred, with the frame's aspect ratio and layout margins. */
export function fitViewport(content: Extent, frame: Extent): Viewport {
  const contentWidth = positive(content.width, 1);
  const contentHeight = positive(content.height, 1);
  const frameWidth = positive(frame.width, contentWidth);
  const frameHeight = positive(frame.height, contentHeight);
  const zoom = fitZoom(content, { width: frameWidth, height: frameHeight });
  const width = frameWidth / zoom;
  const height = frameHeight / zoom;
  return { x: (contentWidth - width) / 2, y: (contentHeight - height) / 2, width, height, frame: { width: frameWidth, height: frameHeight } };
}

/**
 * Keep the viewport within reach of the content. Panning may overshoot by half
 * a frame so a node on the edge is never pinned under a pane, and content
 * smaller than the frame stays centred rather than drifting away.
 */
export function clampViewport(viewport: Viewport, content: Extent): Viewport {
  const contentWidth = positive(content.width, viewport.width);
  const contentHeight = positive(content.height, viewport.height);
  const marginX = viewport.width * OVERSCROLL;
  const marginY = viewport.height * OVERSCROLL;
  const minX = -marginX;
  const maxX = Math.max(minX, contentWidth + marginX - viewport.width);
  const minY = -marginY;
  const maxY = Math.max(minY, contentHeight + marginY - viewport.height);
  return {
    ...viewport,
    x: Math.min(maxX, Math.max(minX, viewport.x)),
    y: Math.min(maxY, Math.max(minY, viewport.y))
  };
}

/** Pan by a pixel delta measured in the frame, in the direction of the gesture. */
export function panViewport(viewport: Viewport, frame: Extent, dxPixels: number, dyPixels: number, content: Extent): Viewport {
  const scaleX = viewport.width / positive(frame.width, viewport.width);
  const scaleY = viewport.height / positive(frame.height, viewport.height);
  const dx = Number.isFinite(dxPixels) ? dxPixels : 0;
  const dy = Number.isFinite(dyPixels) ? dyPixels : 0;
  return clampViewport({ ...viewport, x: viewport.x + dx * scaleX, y: viewport.y + dy * scaleY }, content);
}

/**
 * Zoom to an exact level while holding one point still. `anchor` is where the
 * pointer is inside the frame, as a fraction from 0 to 1, so a pinch keeps the
 * node under the fingers in place; `{0.5, 0.5}` is the keyboard/button centre.
 */
export function zoomViewport(
  viewport: Viewport,
  frame: Extent,
  zoom: number,
  anchor: Readonly<{ x: number; y: number }>,
  content: Extent
): Viewport {
  const frameWidth = positive(frame.width, viewport.width);
  const frameHeight = positive(frame.height, viewport.height);
  // A resize can leave the preserved reading scale below the new fit floor.
  // Do not reverse a zoom-out gesture or jump to that floor on the next zoom-in.
  const minimum = Math.min(minimumMapZoom(content, frame), viewportZoom(viewport, frame));
  const next = boundedZoom(zoom, minimum);
  const width = frameWidth / next;
  const height = frameHeight / next;
  const fx = Math.min(1, Math.max(0, Number.isFinite(anchor.x) ? anchor.x : 0.5));
  const fy = Math.min(1, Math.max(0, Number.isFinite(anchor.y) ? anchor.y : 0.5));
  const userX = viewport.x + fx * viewport.width;
  const userY = viewport.y + fy * viewport.height;
  return clampViewport({ x: userX - fx * width, y: userY - fy * height, width, height, frame: { width: frameWidth, height: frameHeight } }, content);
}

export function zoomViewportBy(
  viewport: Viewport,
  frame: Extent,
  factor: number,
  anchor: Readonly<{ x: number; y: number }>,
  content: Extent
): Viewport {
  const current = viewportZoom(viewport, frame);
  return zoomViewport(viewport, frame, current * (Number.isFinite(factor) && factor > 0 ? factor : 1), anchor, content);
}

/** The pointer position inside a measured frame, as a 0..1 fraction. */
export function pointerAnchor(
  pointer: Readonly<{ clientX: number; clientY: number }>,
  rect: Readonly<{ left: number; top: number; width: number; height: number }>
): Readonly<{ x: number; y: number }> {
  const width = positive(rect.width, 1);
  const height = positive(rect.height, 1);
  return {
    x: Math.min(1, Math.max(0, (pointer.clientX - rect.left) / width)),
    y: Math.min(1, Math.max(0, (pointer.clientY - rect.top) / height))
  };
}

export function viewBoxAttribute(viewport: Viewport): string {
  const round = (value: number): number => Math.round(value * 100) / 100;
  return `${round(viewport.x)} ${round(viewport.y)} ${round(Math.max(1, viewport.width))} ${round(Math.max(1, viewport.height))}`;
}

/**
 * The identity of one reading position. The focused branch is part of it: the
 * dependency map of one component and the structure of another are different
 * places, and each keeps where the person was looking.
 */
export function viewportKey(parts: Readonly<{ projectId: string | null; kind: string; focus: string }>): string {
  return `${parts.projectId ?? "legacy"}/${parts.kind}/${parts.focus}`;
}

/**
 * What identifies one drawing. An automatic refresh that re-reads the same
 * tasks produces the same signature, so the reading position survives it; a
 * genuinely different drawing — another branch, another search — is a new
 * place and opens on its own fit.
 */
export function contentSignature(parts: Readonly<{ count: number; width: number; height: number; first: string }>): string {
  return `${parts.count}:${Math.round(parts.width)}x${Math.round(parts.height)}:${parts.first}`;
}

/**
 * In-memory store of viewports. Never browser storage: a reading position is
 * not worth a persisted record, and a reload legitimately starts from fit.
 */
export class MapViewportStore {
  private viewports = new Map<string, Viewport>();

  get(key: string): Viewport | null {
    return this.viewports.get(key) ?? null;
  }

  set(key: string, viewport: Viewport): void {
    this.viewports.set(key, viewport);
  }

  /** Forget one reading position, so the next render fits the content again. */
  clear(key: string): void {
    this.viewports.delete(key);
  }
}

/** Resize around the same map-space centre at the same pixels-per-unit scale.
 * Matching the SVG viewBox aspect to the frame avoids letterboxing pointer drift. */
export function reframeViewport(viewport: Viewport, frame: Extent): Viewport {
  const previous = viewport.frame;
  if (!previous || previous.width <= 0 || previous.height <= 0 || frame.width <= 0 || frame.height <= 0) return viewport;
  if (previous.width === frame.width && previous.height === frame.height) return viewport;
  const zoom = viewportZoom(viewport, previous);
  const width = frame.width / zoom, height = frame.height / zoom;
  return { x: viewport.x + (viewport.width - width) / 2, y: viewport.y + (viewport.height - height) / 2, width, height, frame };
}

export function wheelPixels(event: Readonly<{ deltaX: number; deltaY: number; deltaMode: number }>, frame: Extent): Readonly<{ x: number; y: number }> {
  const xScale = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? frame.width : 1;
  const yScale = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? frame.height : 1;
  return { x: event.deltaX * xScale, y: event.deltaY * yScale };
}
