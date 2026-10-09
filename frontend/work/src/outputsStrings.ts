import { translate, type Locale, type MessageKey } from "./i18n.js";

const KEYS = ["reports", "textReport", "readReport", "checks", "evidenceStale", "taskChanged", "loadMoreEvidence", "reviewCommandContext","staticBuild", "builds", "downloadBuild", "openLocally", "retainedBytes", "sourceBytes", "resultAwaiting", "resultApproved", "resultReturned", "resultUnreviewed",
  "galleryEmpty", "gallery", "openGallery", "versionFilter", "kindFilter", "allKinds", "images", "liveKinds", "retry", "filterEmpty", "designImage", "generatedImage", "noThumbnail", "task", "agent", "producerUnknown", "technicalDetails", "address",
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
