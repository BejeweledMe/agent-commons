import { useEffect, useRef, useState, type MouseEvent } from "react";
export const COMPACT_WORKSPACE_QUERY = "(max-width: 1100px)";
export function useCompactPane(projectId: string | null) {
  const [compact, setCompact] = useState(() => window.matchMedia(COMPACT_WORKSPACE_QUERY).matches);
  const [pane, setPane] = useState<"sidebar" | "chat" | null>(null);
  const element = useRef<HTMLElement | null>(null), opener = useRef<HTMLElement | null>(null);
  const close = () => { setPane(null); window.requestAnimationFrame(() => opener.current?.focus()); };
  useEffect(() => { setPane(null); }, [projectId]);
  useEffect(() => {
    const media = window.matchMedia(COMPACT_WORKSPACE_QUERY);
    const changed = () => { setCompact(media.matches); setPane(null); };
    media.addEventListener("change", changed); return () => media.removeEventListener("change", changed);
  }, []);
  useEffect(() => {
    if (!compact || !pane || !element.current) return;
    const dialog = element.current;
    const controls = () => Array.from(dialog.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary, [tabindex="0"]')).filter((item) => item.getClientRects().length > 0);
    (controls()[0] ?? dialog).focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); close(); }
      if (event.key !== "Tab") return;
      const nodes = controls(), first = nodes[0], last = nodes.at(-1);
      if (!first) { event.preventDefault(); dialog.focus(); }
      else if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog)) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    dialog.addEventListener("keydown", key); return () => dialog.removeEventListener("keydown", key);
  }, [compact, pane]);
  return { compact, pane, element, close, show: (next: "sidebar" | "chat") => { opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null; setPane(next); }, reset: () => setPane(null), open: (next: "sidebar" | "chat", event: MouseEvent<HTMLButtonElement>) => { opener.current = event.currentTarget; setPane((current) => current === next ? null : next); } };
}
