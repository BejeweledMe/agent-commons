import { useEffect, useRef, useState, type ReactElement } from "react";

import type { MessageKey } from "../i18n";
import { focusOnWriteEnd, focusOnWriteStart } from "../mutationFocus.js";
import {
  mutationElapsedSeconds,
  mutationHint,
  mutationState,
  type MutationHandle,
  type MutationSurface
} from "../mutationRegistry.js";

const systemClock = (): number => Date.now();

/**
 * One `setInterval` per in-flight write, cleared when that write ends, because
 * the component is unmounted with it. The timer is presentation only: it never
 * retries, never cancels and never turns an unfinished write into a failure.
 * The two thresholds are hints about a large project, not a latency promise.
 */
function MutationDuration({ handle, clock, text }: {
  handle: MutationHandle;
  clock: () => number;
  text: (key: MessageKey) => string;
}): ReactElement | null {
  const [elapsedMs, setElapsedMs] = useState(() => Math.max(0, clock() - handle.startedAt));
  const container = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const read = (): void => setElapsedMs(Math.max(0, clock() - handle.startedAt));
    read();
    const timer = window.setInterval(read, 1_000);
    return () => window.clearInterval(timer);
  }, [handle, clock]);
  // Focus management: see mutationFocus.ts. The initiating control is the one the
  // shell captured at begin(), before it was disabled; the container takes dropped
  // focus for the duration and hands it back when the write ends.
  useEffect(() => {
    const initiator = handle.initiator instanceof HTMLElement ? handle.initiator : null;
    const take = window.setTimeout(() => {
      focusOnWriteStart(document.activeElement, document.body, container.current)?.focus?.();
    }, 0);
    return () => {
      window.clearTimeout(take);
      const surface = container.current;
      window.setTimeout(() => { focusOnWriteEnd(initiator, document.activeElement, document.body, surface)?.focus?.(); }, 0);
    };
  }, [handle]);
  const hint = mutationHint(elapsedMs);
  // The counter ticks every second and is hidden from assistive technology; only
  // the hint copy, which changes at the 2 s and 8 s transitions, is announced.
  return (
    <div className="mutation-feedback" ref={container} tabIndex={-1}>
      {hint === "none" ? null : (
        <>
          <span className="mutation-feedback-elapsed" aria-hidden="true">{`${mutationElapsedSeconds(elapsedMs)} ${text("mutation_elapsed_unit")}`}</span>
          <span className="mutation-feedback-hint" role="status">
            {text(hint === "elapsed" ? "mutation_large_project" : "mutation_still_saving")}
          </span>
        </>
      )}
    </div>
  );
}

/**
 * The state of one write surface. Its own write shows elapsed time once the
 * wait is long enough to be worth naming; another surface's write shows why
 * this one is unavailable, since the server holds a single write lock.
 */
export function MutationFeedback({ entries, surface, clock = systemClock, text }: {
  entries: readonly MutationHandle[];
  surface: MutationSurface;
  clock?: () => number;
  text: (key: MessageKey) => string;
}): ReactElement | null {
  const state = mutationState(entries, surface);
  if (state.kind === "idle") return null;
  if (state.kind === "saving") return <MutationDuration handle={state.handle} clock={clock} text={text} />;
  return (
    <div className="mutation-feedback mutation-feedback-blocked">
      <span className="mutation-feedback-hint" role="status">{text("mutation_blocked_elsewhere")}</span>
    </div>
  );
}
