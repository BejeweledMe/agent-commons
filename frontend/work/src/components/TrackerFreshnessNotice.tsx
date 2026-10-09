import type { ReactElement } from "react";
import type { Locale, MessageKey } from "../i18n.js";

/** Dates belong to the tracker's observed source, never an inferred task age. */
export function TrackerFreshnessNotice({ freshness, locale, text, onRefresh }: {
  freshness: import("../contracts.js").TrackerFreshness; locale: Locale; text: (key: MessageKey) => string; onRefresh: () => void;
}): ReactElement {
  const date = (value: string): string => new Intl.DateTimeFormat(locale === "ru" ? "ru-RU" : "en-US", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
  return <details className="tracker-state tracker-state-warning tracker-compact-notice tracker-freshness">
    <summary title={text("tracker_stale_title")}>{text("tracker_stale_short")}</summary>
    <p><strong>{text("tracker_stale_title")}</strong><br />
      <span>{text("tracker_freshness_source")}</span><br />
      <span>{text("tracker_observed_snapshot_time")}: <time dateTime={freshness.generatedAt}>{date(freshness.generatedAt)}</time></span><br />
      <span>{freshness.sourceUpdatedAt === null ? text("tracker_source_time_unknown") : <>{text("tracker_source_updated_time")}: <time dateTime={freshness.sourceUpdatedAt}>{date(freshness.sourceUpdatedAt)}</time></>}</span><br />
      <span>{text(freshness.resumeGap ? "tracker_resume_gap" : "tracker_stale_next")}</span><br />
      <button type="button" className="button button-secondary button-inline" onClick={onRefresh}>{text("task_view_refresh")}</button>
    </p>
  </details>;
}
