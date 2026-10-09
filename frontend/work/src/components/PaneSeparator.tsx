import { type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent, type ReactElement, useRef } from "react";
import { WIDTH_STEP } from "../workspaceLayout.js";

type Props = {
  /** Which boundary this is; it carries the pane's grid position. */
  pane: "sidebar" | "chat";
  label: string;
  /** The current pane width, already snapped to the 8px step. */
  value: number;
  min: number;
  max: number;
  /** `1` when dragging right widens the pane, `-1` when it narrows it. */
  direction: 1 | -1;
  /** Live, local feedback while the gesture runs. */
  onResize: (width: number) => void;
  /** The gesture ended: this is the one moment the arrangement is saved. */
  onCommit: (width: number) => void;
  onBeginDrag?: () => void;
  /** Double-click or Enter restores this pane's default width. */
  onReset: () => void;
  valueText: string;
};

/**
 * The draggable boundary between two panes.
 *
 * It is a real ARIA separator: focusable, with `aria-valuenow` in pixels, so
 * the arrangement is reachable with arrow keys and announced. The pointer path
 * reports every step for live feedback and saves only when the drag ends.
 */
export function PaneSeparator({ pane, label, value, min, max, direction, onResize, onCommit, onBeginDrag, onReset, valueText }: Props): ReactElement {
  const drag = useRef<{ pointerId: number; startX: number; startWidth: number } | null>(null);

  function down(event: ReactPointerEvent<HTMLDivElement>): void {
    if (event.button !== 0) return;
    drag.current = { pointerId: event.pointerId, startX: event.clientX, startWidth: value };
    onBeginDrag?.();
    event.currentTarget.setPointerCapture(event.pointerId);
    event.preventDefault();
  }

  function move(event: ReactPointerEvent<HTMLDivElement>): void {
    const current = drag.current;
    if (current === null || current.pointerId !== event.pointerId) return;
    onResize(current.startWidth + direction * (event.clientX - current.startX));
  }

  function up(event: ReactPointerEvent<HTMLDivElement>): void {
    const current = drag.current;
    if (current === null || current.pointerId !== event.pointerId) return;
    drag.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    onCommit(current.startWidth + direction * (event.clientX - current.startX));
  }

  function key(event: ReactKeyboardEvent<HTMLDivElement>): void {
    const nudge = (delta: number): void => { event.preventDefault(); onCommit(value + delta); };
    if (event.key === "ArrowLeft") return nudge(-direction * WIDTH_STEP);
    if (event.key === "ArrowRight") return nudge(direction * WIDTH_STEP);
    if (event.key === "Home") return nudge(min - value);
    if (event.key === "End") return nudge(max - value);
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onReset(); }
  }

  return <div className={`pane-separator pane-separator-${pane}`} role="separator" aria-orientation="vertical" tabIndex={0}
    aria-label={label} aria-valuenow={value} aria-valuemin={min} aria-valuemax={max} aria-valuetext={valueText}
    onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up}
    onDoubleClick={onReset} onKeyDown={key}>
    <span aria-hidden="true" className="pane-separator-grip" />
  </div>;
}
