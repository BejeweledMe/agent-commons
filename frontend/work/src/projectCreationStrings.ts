import { namespacedCopy, type Locale } from "./i18n.js";

const KEYS = [
  "title",
  "new",
  "existing",
  "newHelp",
  "existingHelp",
  "name",
  "parent",
  "folder",
  "chooseParent",
  "chooseFolder",
  "choosing",
  "manual",
  "pathLabel",
  "emptyLocation",
  "preview",
  "check",
  "checking",
  "create",
  "connect",
  "retry",
  "creating",
  "back",
  "close",
  "ready",
  "initialize",
  "initializeHelp",
  "readyHelp",
  "newReady",
  "path",
  "nameError",
  "folderName",
  "picker",
  "inspect",
  "uncertain",
  "ambiguous",
  "expired",
] as const;

export function projectCreationText(locale: Locale): { [K in (typeof KEYS)[number]]: string } {
  return namespacedCopy(locale, "project_creation", KEYS);
}
