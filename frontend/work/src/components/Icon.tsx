import type { ReactElement } from "react";

/**
 * The one icon set of the Work application.
 *
 * Every glyph is a 24×24 line drawing on the same grid, drawn with the same
 * stroke weight, and it takes its colour from the surrounding text through the
 * `.icon` class — never through a `fill`, `stroke` or `style` attribute, which
 * the CSP forbids and which would also break the two colour schemes. There are
 * no emoji anywhere in this application; a picture of a thing is this file or
 * it does not exist.
 */
export type IconName =
  | "map" | "agents" | "library" | "settings" | "provider" | "project" | "folder"
  | "attachment" | "close" | "plus" | "search" | "refresh" | "run" | "chat" | "results" | "tasks"
  | "skills" | "permissions" | "add-child" | "link" | "supervisor" | "info"
  | "sidebar" | "chat-pane" | "swap" | "check" | "alert" | "chevron" | "dots" | "zoom-in" | "zoom-out" | "fit";

/** The path geometry of each glyph. Numeric SVG attributes only. */
const GLYPHS: Readonly<Record<IconName, readonly string[]>> = {
  map: ["M3 6.5 9 4l6 2.5L21 4v13.5L15 20l-6-2.5L3 20z", "M9 4v13.5", "M15 6.5V20"],
  agents: ["M12 3.5a3 3 0 1 1 0 6 3 3 0 0 1 0-6z", "M5.5 14.5a3 3 0 1 1 0 6 3 3 0 0 1 0-6z", "M18.5 14.5a3 3 0 1 1 0 6 3 3 0 0 1 0-6z", "M12 9.5v2.5", "M12 12H5.5v2.5", "M12 12h6.5v2.5"],
  library: ["M4 5.5A1.5 1.5 0 0 1 5.5 4H19v16H5.5A1.5 1.5 0 0 1 4 18.5z", "M8 4v16", "M11.5 8.5h4", "M11.5 12h4"],
  settings: ["M12 9a3 3 0 1 1 0 6 3 3 0 0 1 0-6z", "M19.4 14.4a1.5 1.5 0 0 0 .3 1.7l.1.1a1.8 1.8 0 1 1-2.6 2.6l-.1-.1a1.5 1.5 0 0 0-2.5 1v.3a1.8 1.8 0 0 1-3.6 0v-.2a1.5 1.5 0 0 0-2.6-1l-.1.1A1.8 1.8 0 1 1 5.7 16l.1-.1a1.5 1.5 0 0 0-1-2.5h-.3a1.8 1.8 0 0 1 0-3.6h.2a1.5 1.5 0 0 0 1-2.6l-.1-.1A1.8 1.8 0 1 1 8.2 4.5l.1.1a1.5 1.5 0 0 0 1.7.3h.1a1.5 1.5 0 0 0 .9-1.4v-.3a1.8 1.8 0 0 1 3.6 0v.2a1.5 1.5 0 0 0 2.5 1l.1-.1a1.8 1.8 0 1 1 2.6 2.6l-.1.1a1.5 1.5 0 0 0 1 2.5h.3a1.8 1.8 0 0 1 0 3.6h-.2a1.5 1.5 0 0 0-1.4.9z"],
  provider: ["M4 7.5A1.5 1.5 0 0 1 5.5 6h13A1.5 1.5 0 0 1 20 7.5v3A1.5 1.5 0 0 1 18.5 12h-13A1.5 1.5 0 0 1 4 10.5z", "M4 15.5A1.5 1.5 0 0 1 5.5 14h13a1.5 1.5 0 0 1 1.5 1.5v1A1.5 1.5 0 0 1 18.5 18h-13A1.5 1.5 0 0 1 4 16.5z", "M7.5 9h.01", "M7.5 16h.01"],
  project: ["M3.5 6.5A1.5 1.5 0 0 1 5 5h4l2 2.5h6.5A1.5 1.5 0 0 1 19 9v8a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 17z"],
  folder: ["M3.5 6.5A1.5 1.5 0 0 1 5 5h4l2 2.5h6.5A1.5 1.5 0 0 1 19 9v8a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 17z", "M3.5 10.5h15.5"],
  attachment: ["M8 12.5 14.5 6a3 3 0 0 1 4.2 4.2l-8.5 8.5a4.5 4.5 0 0 1-6.4-6.4l8.5-8.5", "M7.1 14.8a1.5 1.5 0 0 0 2.1 2.1l7.5-7.5"],
  close: ["M6 6l12 12", "M18 6 6 18"],
  plus: ["M12 5v14", "M5 12h14"],
  search: ["M11 4.5a6.5 6.5 0 1 1 0 13 6.5 6.5 0 0 1 0-13z", "M15.8 15.8 20 20"],
  refresh: ["M19.5 12a7.5 7.5 0 1 1-2.6-5.7", "M19.5 4.5V9h-4.5"],
  run: ["M8 5.5 18.5 12 8 18.5z"],
  chat: ["M4.5 6A1.5 1.5 0 0 1 6 4.5h12A1.5 1.5 0 0 1 19.5 6v8a1.5 1.5 0 0 1-1.5 1.5H9l-4.5 4z"],
  results: ["M4.5 6A1.5 1.5 0 0 1 6 4.5h12A1.5 1.5 0 0 1 19.5 6v12a1.5 1.5 0 0 1-1.5 1.5H6A1.5 1.5 0 0 1 4.5 18z", "m4.5 15 4-4 3.5 3.5 3-3 4.5 4.5", "M14.5 8.5h.01"],
  tasks: ["M4.5 6.5h3v3h-3z", "M4.5 14.5h3v3h-3z", "M10.5 8h9", "M10.5 16h9"],
  skills: ["M12 3.5 14.6 9l6 .9-4.3 4.2 1 6-5.3-2.8L6.7 20l1-6L3.4 9.9 9.4 9z"],
  permissions: ["M12 3.5 19 6v5.5c0 4-2.9 7.3-7 9-4.1-1.7-7-5-7-9V6z", "m9 12 2 2 4-4"],
  "add-child": ["M12 3.5a2.8 2.8 0 1 1 0 5.6 2.8 2.8 0 0 1 0-5.6z", "M12 9.1v4.4", "M12 13.5H6.5v3", "M12 13.5h5.5v3", "M4.5 19.5h4", "M15.5 19.5h4", "M17.5 17.5v4"],
  link: ["M10 14a4 4 0 0 0 5.7 0l2.8-2.8A4 4 0 0 0 12.8 5.5L11.4 7", "M14 10a4 4 0 0 0-5.7 0L5.5 12.8A4 4 0 0 0 11.2 18.5l1.4-1.5"],
  supervisor: ["M12 4.5a2.8 2.8 0 1 1 0 5.6 2.8 2.8 0 0 1 0-5.6z", "M12 10.1v3.4", "M6 19.5v-2a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v2"],
  info: ["M12 3.8a8.2 8.2 0 1 1 0 16.4 8.2 8.2 0 0 1 0-16.4z", "M12 11v5", "M12 8h.01"],
  sidebar: ["M4 5.5A1.5 1.5 0 0 1 5.5 4h13A1.5 1.5 0 0 1 20 5.5v13a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 18.5z", "M9.5 4v16"],
  "chat-pane": ["M4 5.5A1.5 1.5 0 0 1 5.5 4h13A1.5 1.5 0 0 1 20 5.5v13a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 18.5z", "M14.5 4v16"],
  swap: ["M7 8h12", "m16 5 3 3-3 3", "M17 16H5", "m8 13-3 3 3 3"],
  check: ["m5 12.5 4.5 4.5L19 7.5"],
  alert: ["M12 4.5 20.5 19h-17z", "M12 10v4", "M12 16.5h.01"],
  chevron: ["m9 6 6 6-6 6"],
  dots: ["M6 12h.01", "M12 12h.01", "M18 12h.01"],
  "zoom-in": ["M11 4.5a6.5 6.5 0 1 1 0 13 6.5 6.5 0 0 1 0-13z", "M15.8 15.8 20 20", "M11 8.5v5", "M8.5 11h5"],
  "zoom-out": ["M11 4.5a6.5 6.5 0 1 1 0 13 6.5 6.5 0 0 1 0-13z", "M15.8 15.8 20 20", "M8.5 11h5"],
  fit: ["M4.5 9V5.5a1 1 0 0 1 1-1H9", "M15 4.5h3.5a1 1 0 0 1 1 1V9", "M19.5 15v3.5a1 1 0 0 1-1 1H15", "M9 19.5H5.5a1 1 0 0 1-1-1V15"]
};

/**
 * One glyph. Decorative by default: an icon-only control carries its name on
 * the button, so the picture itself stays out of the accessibility tree.
 */
export function Icon({ name, size = 16 }: { name: IconName; size?: 14 | 16 | 18 | 20 }): ReactElement {
  return <svg className={`icon icon-${size}`} viewBox="0 0 24 24" width={size} height={size} aria-hidden="true" focusable="false">
    {GLYPHS[name].map((path) => <path key={path} d={path} />)}
  </svg>;
}

/**
 * A compact action with no visible label. The name is never optional: it is the
 * button's accessible name and its tooltip, so a wall of icons is still
 * readable by anyone who stops on one.
 */
export function IconButton({ icon, label, onClick, disabled = false, pressed, tone = "quiet", busy = false, className = "" }: {
  icon: IconName; label: string; onClick: () => void; disabled?: boolean; pressed?: boolean;
  tone?: "quiet" | "accent"; busy?: boolean; className?: string;
}): ReactElement {
  return <button type="button" className={`icon-button icon-button-${tone}${className ? ` ${className}` : ""}`}
    aria-label={label} title={label} aria-pressed={pressed} aria-busy={busy || undefined}
    disabled={disabled} onClick={onClick}>
    <Icon name={icon} />
  </button>;
}
