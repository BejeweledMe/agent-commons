import messages from "./i18n.json";

export type Locale = keyof typeof messages;
export type MessageKey = keyof (typeof messages)["en"];

export function translate(locale: Locale, key: MessageKey): string {
  return messages[locale][key];
}
