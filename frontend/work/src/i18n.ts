import messages from "./i18n.json" with { type: "json" };

export type Locale = keyof typeof messages;
export type MessageKey = keyof (typeof messages)["en"];

export function translate(locale: Locale, key: MessageKey): string {
  return messages[locale][key];
}

export function namespacedCopy<Keys extends readonly string[]>(
  locale: Locale,
  prefix: string,
  keys: Keys,
): { [K in Keys[number]]: string } {
  const table = messages[locale];
  const copy = {} as { [K in Keys[number]]: string };
  for (const key of keys) {
    copy[key as Keys[number]] = table[`${prefix}.${key}` as MessageKey];
  }
  return copy;
}
