import type { Locale } from "./i18n.js";
const copy = {
  en: {
    live: "Live frontend preview", starting: "Publisher reports starting", reported_ready: "Publisher reports ready", reachability: "Reachability has not been checked. This preview opens in a separate tab.", openLive: "Open frontend preview", expires: "Expires", published: "Published",
    results: "Results", close: "Close results", latest: "Latest", history: "All versions", refresh: "Refresh",
    loading: "Loading results…", failed: "Results could not be loaded. Refresh to try again.", empty: "No accessible image results in this selection.",
    latestResult: "Latest result", earlierViewable: "Earlier version (viewable)", earlierVersion: "Earlier version",
    previewExpired: "Preview expired", addressUnavailable: "Address unavailable", previewNotVerified: "Preview not verified", previewUnavailable: "Preview unavailable",
    reviewAwaiting: "Awaiting check", reviewApproved: "Check approved", reviewReturned: "Returned with comments",
    earlierHelp: "This is an earlier recorded version. A verified image is not available for it.", unavailableHelp: "A verified image preview is not available for this result.",
    uncheckedHelp: "This result has not been checked for a viewable image yet.",
    historicalHelp: "The task has changed since this image was produced. Its original image bytes are verified; viewing does not mark the result current.", viewHistorical: "View recorded image",
    view: "View image", imageLoading: "Checking image…", imageFailed: "The image could not be verified. Refresh the results before trying again.",
    current: "Latest for this scope", previous: "Earlier version", versions: "versions", designs: "Image results", titlePrefix: "Results for",
  },
  ru: {
    live: "Живой просмотр интерфейса", starting: "Автор сообщает о запуске", reported_ready: "Автор сообщает о готовности", reachability: "Доступность не проверялась. Просмотр откроется в отдельной вкладке.", openLive: "Открыть интерфейс", expires: "Действует до", published: "Опубликовано",
    results: "Результаты", close: "Закрыть результаты", latest: "Последние", history: "Все версии", refresh: "Обновить",
    loading: "Загружаем результаты…", failed: "Не удалось загрузить результаты. Обновите, чтобы повторить.", empty: "В этой выборке пока нет доступных изображений.",
    latestResult: "Последний результат", earlierViewable: "Прежняя версия (можно посмотреть)", earlierVersion: "Прежняя версия",
    previewExpired: "Просмотр истёк", addressUnavailable: "Адрес недоступен", previewNotVerified: "Просмотр не проверен", previewUnavailable: "Просмотр недоступен",
    reviewAwaiting: "Ждёт проверки", reviewApproved: "Проверка одобрена", reviewReturned: "Возвращено с замечаниями",
    earlierHelp: "Это прежняя сохранённая версия. Проверенное изображение для неё недоступно.", unavailableHelp: "Проверенное изображение для этого результата недоступно.",
    uncheckedHelp: "Этот результат ещё не проверяли на доступное изображение.",
    historicalHelp: "После создания изображения задача изменилась. Исходное изображение проверено; просмотр не делает результат актуальным.", viewHistorical: "Посмотреть сохранённое изображение",
    view: "Посмотреть изображение", imageLoading: "Проверяем изображение…", imageFailed: "Не удалось проверить изображение. Обновите результаты перед повторной попыткой.",
    current: "Последняя версия в этой выборке", previous: "Предыдущая версия", versions: "версий", designs: "Изображения", titlePrefix: "Результаты:",
  },
} as const;
export type OutputMessage = keyof typeof copy.en;
export function outputText(locale: Locale, key: OutputMessage): string { return copy[locale][key]; }
