import type { ReactElement, ReactNode } from "react";

import type { Locale, MessageKey } from "../i18n";

type AppHeaderProps = {
  title: string;
  locale: Locale;
  text: (key: MessageKey) => string;
  onLocaleChange: (locale: Locale) => void;
  /** Collapse, swap and restore for the two side panes. */
  layoutControls?: ReactNode;
  hideTitle?: boolean;
};

export function AppHeader({ locale, text, title, onLocaleChange, layoutControls, hideTitle }: AppHeaderProps): ReactElement {
  return (
    <header className="app-header">
      <div>
        {layoutControls}
        <h1 className={hideTitle ? "sr-only" : undefined}>{title}</h1>
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
