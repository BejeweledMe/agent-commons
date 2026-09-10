/**
 * Keyboard focus around one write, as pure decisions over a minimal element
 * shape so the rules are testable without a DOM.
 *
 * The control that starts a write is disabled while the write runs, and a
 * disabled control loses focus to the document body. The feedback container
 * takes that dropped focus for the duration; when the write ends the initiating
 * control gets it back, but only if it is enabled again and focus has not been
 * moved somewhere else on purpose.
 */
export type FocusTarget = {
  /** Absent on a bare Element (document.activeElement); such a target is never focused. */
  focus?(): void;
  readonly isConnected?: boolean;
  readonly disabled?: boolean;
  closest?(selector: string): unknown;
};

function dropped(active: FocusTarget | null, body: FocusTarget | null, container: FocusTarget | null): boolean {
  return active === null || active === body || (container !== null && active === container);
}

/** What to focus when a write starts: the container, only if focus was dropped. */
export function focusOnWriteStart(active: FocusTarget | null, body: FocusTarget | null, container: FocusTarget | null): FocusTarget | null {
  return container !== null && (active === null || active === body) ? container : null;
}

/** What to focus when a write ends: the initiating control, only if it can take focus and nothing else has it. */
export function focusOnWriteEnd(initiator: FocusTarget | null, active: FocusTarget | null, body: FocusTarget | null, container: FocusTarget | null): FocusTarget | null {
  if (initiator === null || initiator === container || !dropped(active, body, container)) return null;
  if (initiator.isConnected === false || initiator.disabled === true) return null;
  if (typeof initiator.closest === "function" && initiator.closest("fieldset:disabled")) return null;
  return initiator;
}
