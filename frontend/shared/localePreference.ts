/** The only cross-application persistent preference; never accepts content or credentials. */
export type PreferredLocale = "en" | "ru";
export const LOCALE_PREFERENCE_KEY = "agent-commons.locale";
type PreferenceStorage = Pick<Storage, "getItem" | "setItem">;
type StorageSource = () => PreferenceStorage;
const browserStorage: StorageSource = () => window.localStorage;

export function readLocalePreference(source: StorageSource = browserStorage): PreferredLocale {
  try {
    const value = source().getItem(LOCALE_PREFERENCE_KEY);
    return value === "ru" ? "ru" : "en";
  } catch {
    return "en";
  }
}

export function writeLocalePreference(locale: PreferredLocale, source: StorageSource = browserStorage): void {
  if (locale !== "en" && locale !== "ru") return;
  try {
    source().setItem(LOCALE_PREFERENCE_KEY, locale);
  } catch {
    // A blocked or full store never prevents changing language in this window.
  }
}
