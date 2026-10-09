import { useEffect, useId, useRef, type ReactElement, type ReactNode } from "react";
import { isInnermostDialog, pushDialog } from "../dialogStack.js";
import { Icon } from "./Icon.js";

/**
 * The one modal primitive of the Work application.
 *
 * A detail, a form or a settings surface opens here instead of taking a column
 * away from the map. The element is a real `<dialog>`, so the browser gives us
 * the focus trap, the top layer and the backdrop; this component adds the three
 * things the platform does not:
 *
 * 1. **Return of focus that does not move the map.** The element that opened
 *    the dialog is remembered and refocused with `preventScroll`, so closing a
 *    task never scrolls the canvas under it.
 * 2. **Nested behaviour.** The dialog registers in the shared stack, so an
 *    Escape inside a conversation opened from a task detail closes only the
 *    conversation, and the window-level shortcuts stay quiet.
 * 3. **One close affordance.** A labelled × that is also the tooltip, in the
 *    same place on every surface.
 *
 * Nothing here is dismissed by clicking the backdrop: these surfaces hold typed
 * drafts, and losing one to a stray click is not an acceptable trade.
 */
export type ModalProps = {
  /** The heading, rendered as the dialog's accessible name. */
  title: string;
  /** A quiet line above the heading: what kind of surface this is. */
  eyebrow?: string;
  /** A quiet line under the heading, for the subject of the surface. */
  subtitle?: ReactNode;
  /** Accessible name and tooltip of the × control. */
  closeLabel: string;
  onClose: () => void;
  /** `large` nearly fills the viewport; `medium` suits a single question. */
  size?: "large" | "medium" | "small";
  /** Extra class on the dialog element, for surface-specific layout. */
  className?: string;
  /** Tabs or a small action group rendered on the header line. */
  toolbar?: ReactNode;
  /** A fixed footer, outside the scrolling body. */
  footer?: ReactNode;
  /** Where to put focus if the opener is gone when the dialog closes. */
  fallbackFocus?: () => HTMLElement | null;
  children: ReactNode;
};

export function Modal({
  title, eyebrow, subtitle, closeLabel, onClose, size = "large", className = "", toolbar, footer, fallbackFocus, children
}: ModalProps): ReactElement {
  const ref = useRef<HTMLDialogElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const registration = useRef<{ id: string; release: () => void } | null>(null);
  const headingId = useId();
  const closing = useRef(onClose);
  closing.current = onClose;
  const restore = useRef(fallbackFocus);
  restore.current = fallbackFocus;

  useEffect(() => {
    const dialog = ref.current;
    const active = document.activeElement;
    opener.current = active instanceof HTMLElement ? active : null;
    registration.current = pushDialog();
    // `showModal` throws if the element is already open, which StrictMode's
    // double effect can produce; an already-open dialog is the wanted state.
    if (dialog !== null && !dialog.open) dialog.showModal();
    return () => {
      registration.current?.release();
      registration.current = null;
      if (dialog?.open) dialog.close();
      const previous = opener.current;
      const target = previous !== null && previous.isConnected ? previous : restore.current?.() ?? null;
      // Never `scrollIntoView`: returning focus must not move the map, the
      // tracker list or the conversation the person was reading.
      target?.focus({ preventScroll: true });
    };
  }, []);

  return <dialog
    className={`commons-dialog commons-dialog-${size}${className ? ` ${className}` : ""}`}
    ref={ref}
    aria-labelledby={headingId}
    onCancel={(event) => {
      // Escape belongs to the innermost dialog and stops here: nothing under
      // this surface may read the same key as "close me too".
      event.preventDefault();
      event.stopPropagation();
      const id = registration.current?.id;
      if (id === undefined || isInnermostDialog(id)) closing.current();
    }}
    onKeyDown={(event) => { if (event.key === "Escape") event.stopPropagation(); }}
  >
    <div className="commons-dialog-frame">
      <header className="commons-dialog-head">
        <div className="commons-dialog-titles">
          {eyebrow === undefined ? null : <p className="commons-dialog-eyebrow">{eyebrow}</p>}
          <h2 id={headingId}>{title}</h2>
          {subtitle === undefined ? null : <p className="commons-dialog-subtitle">{subtitle}</p>}
        </div>
        {toolbar === undefined ? null : <div className="commons-dialog-toolbar">{toolbar}</div>}
        <button type="button" className="icon-button commons-dialog-close" aria-label={closeLabel} title={closeLabel}
          onClick={() => closing.current()}>
          <Icon name="close" size={18} />
        </button>
      </header>
      <div className="commons-dialog-body">{children}</div>
      {footer === undefined ? null : <div className="commons-dialog-footer">{footer}</div>}
    </div>
  </dialog>;
}

/**
 * The tab strip a large settings surface puts in its header. Tabs are real
 * buttons in a tablist, so arrow keys and the screen reader both work.
 */
export function ModalTabs<Tab extends string>({ tabs, value, onChange, label }: {
  tabs: readonly { id: Tab; label: string }[]; value: Tab; onChange: (tab: Tab) => void; label: string;
}): ReactElement {
  return <div className="modal-tabs" role="tablist" aria-label={label}>
    {tabs.map((tab) => <button key={tab.id} type="button" role="tab" id={`modal-tab-${tab.id}`}
      className="modal-tab" aria-selected={tab.id === value} aria-controls={`modal-panel-${tab.id}`}
      tabIndex={tab.id === value ? 0 : -1}
      onKeyDown={(event) => {
        if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
        event.preventDefault();
        const at = tabs.findIndex((item) => item.id === value);
        const next = tabs[(at + (event.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
        onChange(next.id);
        document.getElementById(`modal-tab-${next.id}`)?.focus({ preventScroll: true });
      }}
      onClick={() => onChange(tab.id)}>{tab.label}</button>)}
  </div>;
}
