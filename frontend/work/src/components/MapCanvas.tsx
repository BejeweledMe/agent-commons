import { type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent, type ReactElement, type ReactNode, type Ref, useCallback, useEffect, useId, useImperativeHandle, useRef, useState } from "react";
import { ensureVisible, frameChangedEnoughForFitHint, workingZoomViewport, zoomBand, type MapRect, type ZoomBand } from "../mapZoomBands.js";
import {
  clampViewport,
  reframeViewport,
  wheelPixels,
  fitViewport,
  MAX_MAP_ZOOM,
  minimumMapZoom,
  panViewport,
  pointerAnchor,
  viewBoxAttribute,
  viewportZoom,
  ZOOM_STEP,
  zoomViewport,
  zoomViewportBy,
  type Extent,
  type Viewport
} from "../mapViewport.js";

export type MapCanvasLabels = Readonly<{
  canvas: string;
  options?: string;
  help?: string;
  gestures: string;
  zoom: string;
  zoomIn: string;
  zoomOut: string;
  fit: string;
  reset: string;
  /** Offered — never applied — after the frame changed a lot under a manual position. */
  fitHint?: string;
}>;

export type MapCanvasHandle = Readonly<{
  reveal: (rect: MapRect) => void;
  workingZoom: (rect: MapRect | null) => void;
}>;

type Props = {
  /** Camera actions use this canvas's current frame, including after resize. */
  ref?: Ref<MapCanvasHandle>;
  /** The accessible name of this map; Structure and Dependencies differ. */
  labels: MapCanvasLabels;
  /** The derived layout's size in map units. */
  content: Extent;
  /** The reading position, or null to open on the whole content. */
  viewport: Viewport | null;
  onViewportChange: (viewport: Viewport) => void;
  /** Search, focus and kind controls belong above the canvas, not inside it. */
  toolbar?: ReactNode;
  /**
   * The selected element's own readable strip, at document scale, in the map's
   * toolbar. This is what keeps the selection's actions reachable once the
   * reading band hides the per-node microactions; it is not a sidebar and not
   * a permanent inspector.
   */
  strip?: ReactNode;
  /** Counts and boundary notices, under the canvas. */
  footer?: ReactNode;
  /** Shared `defs` such as the edge marker. */
  defs?: ReactNode;
  children: ReactNode;
};

const ZERO: Extent = { width: 0, height: 0 };
const CENTRE = { x: 0.5, y: 0.5 } as const;
/** One arrow press moves a tenth of the visible width. */
const KEY_PAN = 0.1;

function measuredExtent(element: HTMLElement | null): Extent {
  if (element === null) return ZERO;
  const rect = element.getBoundingClientRect();
  return { width: rect.width || element.clientWidth, height: rect.height || element.clientHeight };
}

/**
 * The map's frame: a numeric `viewBox` the operator moves with the trackpad,
 * the mouse or the keyboard.
 *
 * - Two-finger scroll pans; `ctrl`/`⌘` with the wheel — which is what a Mac
 *   trackpad pinch sends — zooms around the pointer.
 * - Dragging empty canvas pans; dragging a node's own button does not, so
 *   selecting a task still works.
 * - The wheel listener is registered non-passively on this element only, and
 *   `preventDefault` is called nowhere else, so the project chat and the page
 *   keep their own scrolling.
 *
 * The viewport belongs to the caller, which keeps one per project, map kind and
 * focused branch. An automatic refresh re-renders the content and leaves the
 * reading position exactly where it was.
 */
export function MapCanvas({ ref, labels, content, viewport, onViewportChange, toolbar, strip, footer, defs, children }: Props): ReactElement {
  const frameRef = useRef<HTMLDivElement>(null);
  const [frame, setFrame] = useState<Extent>(ZERO);
  const gesturesId = useId();
  const drag = useRef<{ pointerId: number; x: number; y: number } | null>(null);
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    const element = frameRef.current;
    if (element === null) return;
    const measure = (): void => {
      const next = measuredExtent(element);
      setFrame((current) => current.width === next.width && current.height === next.height ? current : next);
    };
    measure();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(element);
    window.addEventListener("resize", measure);
    return () => { observer?.disconnect(); window.removeEventListener("resize", measure); };
  }, []);

  // A frame that has not been measured yet reads the content's own proportions,
  // so the first paint is a fit rather than a guess.
  const sizedFrame = frame.width > 0 && frame.height > 0 ? frame : content;
  const effective = viewport ? reframeViewport(viewport, sizedFrame) : fitViewport(content, sizedFrame);
  // The gesture handlers read the current values without being re-registered.
  const live = useRef({ viewport: effective, frame: sizedFrame, content });
  live.current = { viewport: effective, frame: sizedFrame, content };
  const zoom = viewportZoom(effective, sizedFrame);

  const change = useCallback((next: Viewport) => onViewportChange(next), [onViewportChange]);

  useImperativeHandle(ref, () => {
    // A keyboard action can arrive before the resize observer has rendered.
    // Measure here too; the pure camera helpers preserve centre and scale when
    // reconciling that frame with the last rendered viewport.
    const currentCamera = () => {
      const current = live.current;
      const measured = measuredExtent(frameRef.current);
      const frame = measured.width > 0 && measured.height > 0 ? measured : current.frame;
      return { ...current, frame };
    };
    return {
      reveal(rect) {
        const current = currentCamera();
        const next = ensureVisible(current.viewport, current.frame, current.content, rect);
        if (next !== current.viewport) change(next);
      },
      workingZoom(rect) {
        const current = currentCamera();
        change(workingZoomViewport(current.viewport, current.frame, current.content, rect));
      }
    };
  }, [change]);

  useEffect(() => {
    if (viewport === null && frame.width > 0 && frame.height > 0) change(fitViewport(content, frame));
  }, [viewport, frame, content.width, content.height, change]);

  useEffect(() => {
    const element = frameRef.current;
    if (element === null) return;
    const onWheel = (event: WheelEvent): void => {
      const rect = element.getBoundingClientRect();
      const measured: Extent = { width: rect.width, height: rect.height };
      // Scoped to the canvas: the page keeps its own scrolling everywhere else.
      event.preventDefault();
      const { viewport: previous, content: bounds } = live.current;
      const current = reframeViewport(previous, measured), delta = wheelPixels(event, measured);
      if (event.ctrlKey || event.metaKey) {
        // A trackpad pinch arrives as a ctrl-wheel; zoom around the pointer.
        change(zoomViewportBy(current, measured, Math.exp(-delta.y / 180), pointerAnchor(event, rect), bounds));
        return;
      }
      change(panViewport(current, measured, delta.x, delta.y, bounds));
    };
    element.addEventListener("wheel", onWheel, { passive: false });
    return () => element.removeEventListener("wheel", onWheel);
  }, [change]);

  function interactive(target: EventTarget | null): boolean {
    return target instanceof Element && target.closest("button, a, input, select, textarea, summary, [tabindex]:not(.map-canvas-frame)") !== null;
  }

  function startDrag(event: ReactPointerEvent<HTMLDivElement>): void {
    if (event.button !== 0 || interactive(event.target)) return;
    drag.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY };
    setDragging(true);
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function moveDrag(event: ReactPointerEvent<HTMLDivElement>): void {
    const current = drag.current;
    if (current === null || current.pointerId !== event.pointerId) return;
    const dx = event.clientX - current.x;
    const dy = event.clientY - current.y;
    drag.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY };
    const { viewport: position, frame: measured, content: bounds } = live.current;
    change(panViewport(position, measured, -dx, -dy, bounds));
  }

  function endDrag(event: ReactPointerEvent<HTMLDivElement>): void {
    if (drag.current?.pointerId !== event.pointerId) return;
    drag.current = null;
    setDragging(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  }

  function onKeyDown(event: ReactKeyboardEvent<HTMLDivElement>): void {
    // Only the canvas itself: a focused node keeps its own arrow navigation.
    if (event.target !== event.currentTarget || event.defaultPrevented) return;
    const { viewport: position, frame: measured, content: bounds } = live.current;
    const stepX = measured.width * KEY_PAN;
    const stepY = measured.height * KEY_PAN;
    const pan = (dx: number, dy: number): void => { event.preventDefault(); change(panViewport(position, measured, dx, dy, bounds)); };
    if (event.key === "ArrowLeft") return pan(-stepX, 0);
    if (event.key === "ArrowRight") return pan(stepX, 0);
    if (event.key === "ArrowUp") return pan(0, -stepY);
    if (event.key === "ArrowDown") return pan(0, stepY);
    if (event.key === "+" || event.key === "=") { event.preventDefault(); change(zoomViewportBy(position, measured, ZOOM_STEP, CENTRE, bounds)); return; }
    if (event.key === "-" || event.key === "_") { event.preventDefault(); change(zoomViewportBy(position, measured, 1 / ZOOM_STEP, CENTRE, bounds)); return; }
    if (event.key === "0" || event.key === "Home") { event.preventDefault(); change(fitViewport(bounds, measured)); return; }
  }

  const step = (factor: number): void => change(zoomViewportBy(live.current.viewport, live.current.frame, factor, CENTRE, live.current.content));
  const percent = Math.round(zoom * 100);
  // The reading band is derived from the live zoom, with hysteresis so a drag
  // across a threshold does not strobe. It is published as one attribute and
  // read by CSS: nodes keep their geometry, only what they show changes.
  const previousBand = useRef<ZoomBand | null>(null);
  const currentBand: ZoomBand = zoomBand(zoom, previousBand.current);
  previousBand.current = currentBand;
  // The frame changed a lot since the last written position. Offer a refit;
  // never perform one, and never discard the manual position to do it.
  const fitHint = labels.fitHint !== undefined && viewport !== null
    && frameChangedEnoughForFitHint(viewport.frame, sizedFrame);

  return <div className="map-canvas" data-zoom-band={currentBand}>
    <div className="map-canvas-toolbar">
      <details className="map-options"><summary>{labels.options ?? labels.canvas}</summary><div className="map-options-body">{toolbar}</div></details>
      <details className="map-options map-help"><summary>{labels.help ?? labels.gestures}</summary><div className="map-options-body"><p id={gesturesId}>{labels.gestures}</p>{footer}</div></details>
      <div className="map-canvas-zoom" role="group" aria-label={labels.zoom}>
        <button type="button" className="button button-secondary button-inline" aria-label={labels.zoomOut}
          disabled={zoom <= minimumMapZoom(content, sizedFrame) + Number.EPSILON} onClick={() => step(1 / ZOOM_STEP)}>−</button>
        <output className="map-canvas-zoom-level" aria-live="polite">{percent}%</output>
        <button type="button" className="button button-secondary button-inline" aria-label={labels.zoomIn}
          disabled={zoom >= MAX_MAP_ZOOM - 0.001} onClick={() => step(ZOOM_STEP)}>+</button>
        <button type="button" className="button button-secondary button-inline"
          onClick={() => change(fitViewport(live.current.content, live.current.frame))}>{labels.fit}</button>
        <button type="button" className="button button-secondary button-inline" aria-label={labels.reset} title={labels.reset}
          onClick={() => change(clampViewport(zoomViewport(live.current.viewport, live.current.frame, 1, CENTRE, live.current.content), live.current.content))}>100%</button>
      </div>
    </div>
    {strip === undefined ? null : <div className="map-selected-strip">{strip}</div>}
    {fitHint ? <p className="map-fit-hint" role="status">{labels.fitHint}</p> : null}
    <div className={`map-canvas-frame${dragging ? " map-canvas-frame-dragging" : ""}`} ref={frameRef}
      role="region" aria-label={labels.canvas} aria-describedby={gesturesId} tabIndex={0}
      data-map-zoom={percent} data-zoom-band={currentBand}
      onPointerDown={startDrag} onPointerMove={moveDrag} onPointerUp={endDrag} onPointerCancel={endDrag}
      onKeyDown={onKeyDown}>
      <svg className="map-canvas-svg" viewBox={viewBoxAttribute(effective)} preserveAspectRatio="xMidYMid meet" aria-label={labels.canvas}>
        {defs}
        {children}
      </svg>
    </div>
  </div>;
}
