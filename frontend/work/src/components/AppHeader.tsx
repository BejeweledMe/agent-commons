import type { ReactElement } from "react";

import type { Locale, MessageKey } from "../i18n";

type AppHeaderProps = {
  locale: Locale;
  text: (key: MessageKey) => string;
  onLocaleChange: (locale: Locale) => void;
};

export function AppHeader({ locale, text, onLocaleChange }: AppHeaderProps): ReactElement {
  return (
    <header className="app-header">
      <div>
        <p className="eyebrow">Agent Commons</p>
        <h1>{text("app_title")}</h1>
        <p className="app-intro">{text("app_intro")}</p>
      </div>
      <div className="locale-switcher" aria-label={text("language")}>
        <button
          aria-label={text("switch_to_english")}
          aria-pressed={locale === "en"}
          className={locale === "en" ? "locale-button locale-button-selected" : "locale-button"}
          onClick={() => onLocaleChange("en")}
          type="button"
        >
          EN
        </button>
        <button
          aria-label={text("switch_to_russian")}
          aria-pressed={locale === "ru"}
          className={locale === "ru" ? "locale-button locale-button-selected" : "locale-button"}
          onClick={() => onLocaleChange("ru")}
          type="button"
        >
          RU
        </button>
      </div>
    </header>
  );
}
