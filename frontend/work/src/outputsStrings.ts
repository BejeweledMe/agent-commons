import { translate, type Locale, type MessageKey } from "./i18n.js";

const KEYS = [
  "live",
  "starting",
  "reported_ready",
  "reachability",
  "openLive",
  "expires",
  "published",
  "results",
  "close",
  "latest",
  "history",
  "refresh",
  "loading",
  "failed",
  "empty",
  "latestResult",
  "earlierViewable",
  "earlierVersion",
  "previewExpired",
  "addressUnavailable",
  "previewNotVerified",
  "previewUnavailable",
  "reviewAwaiting",
  "reviewApproved",
  "reviewReturned",
  "earlierHelp",
  "unavailableHelp",
  "uncheckedHelp",
  "historicalHelp",
  "viewHistorical",
  "view",
  "imageLoading",
  "imageFailed",
  "current",
  "previous",
  "versions",
  "designs",
  "titlePrefix",
] as const;

export type OutputMessage = (typeof KEYS)[number];

export function outputText(locale: Locale, key: OutputMessage): string {
  return translate(locale, `outputs.${key}` as MessageKey);
}
