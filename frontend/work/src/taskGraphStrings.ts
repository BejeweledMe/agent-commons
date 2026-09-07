import type { Locale } from "./i18n";

const en = {
  suggested: "Suggested role",
  graph: "Dependency graph", list: "Task list", graphHelp: "Prerequisites appear above the tasks that depend on them. Select a task to inspect its work and current run.",
  legend: "Task states", accepted: "Accepted", completed: "Work completed", review: "In review", active: "Active", ready: "Ready", queued: "Queued", blocked: "Blocked", cancelled: "Cancelled", unknown: "Unconfirmed",
  unassigned: "No role assigned", noRun: "No current run", focus: "Selected task and connected work", all: "All matching tasks", graphLimit: "This graph is too large for one readable view. Select a task, narrow the search, or use the task list.",
  graphInvalid: "Some dependencies are missing or cyclic. The task list remains available; repair the graph before starting affected work.",
  filtered: "Only matching tasks are shown. Dependencies outside this view remain in the task details.",
  edit: "Edit task", addDependent: "Add dependent task", cancel: "Cancel task", cancelHelp: "Cancellation keeps the task and its history. Requested or running work must be resolved first.",
  title: "Title", description: "Description", criteria: "Acceptance criteria — one per line", dependencies: "Prerequisites", noDependencies: "No prerequisites", reason: "Cancellation reason",
  save: "Save changes", add: "Create dependent task", close: "Close editor", working: "Saving…", loading: "Loading the complete task…", loadFailed: "The complete task could not be loaded. Your draft remains here.",
  refresh: "Reload task", stale: "The task changed after this draft was opened. Your draft is preserved. Reload the task before preparing a new edit.",
  live: "Resolve requested or running work before editing or cancelling this task.", unavailable: "This action is unavailable in the task's current state.", readonly: "Editing requires a current observation and an operator session.",
  failed: "The action was not completed.", uncertain: "The outcome is unconfirmed. Retry the same action before preparing another change.", retry: "Retry the same action", newAttempt: "Prepare a new attempt", success: "Saved to the task history.",
  required: "Enter a title, description, and at least one acceptance criterion.", cycle: "These prerequisites would create a cycle.", missing: "A selected prerequisite is no longer available.", tooLarge: "The task exceeds the editor's size limit.",
  cancelRequired: "Enter a cancellation reason.", selectedCount: "tasks shown", removeDependency: "Remove unavailable prerequisite", keyboard: "Use arrow keys to move between tasks, or Tab to use the task list and inspector.",
  cancelledDependency: "Cancelled prerequisite", sourceChanged: "Task history refreshed", cancelConfirm: "Confirm cancellation", draftKept: "Edits remain local until you save them."
};
const ru: typeof en = {
  suggested: "Предлагаемая роль",
  graph: "Граф зависимостей", list: "Список задач", graphHelp: "Предварительные задачи расположены выше зависимых. Выберите задачу, чтобы увидеть её результат и текущий запуск.",
  legend: "Состояния задач", accepted: "Принято", completed: "Работа завершена", review: "На ревью", active: "В работе", ready: "Готово к запуску", queued: "В очереди", blocked: "Заблокировано", cancelled: "Отменено", unknown: "Не подтверждено",
  unassigned: "Роль не назначена", noRun: "Текущего запуска нет", focus: "Выбранная задача и связанная работа", all: "Все подходящие задачи", graphLimit: "Граф слишком велик для одного читаемого вида. Выберите задачу, уточните поиск или откройте список.",
  graphInvalid: "Часть зависимостей отсутствует или образует цикл. Список задач доступен; исправьте граф до запуска затронутой работы.",
  filtered: "Показаны только подходящие задачи. Зависимости вне этого вида остаются в подробностях задачи.",
  edit: "Изменить задачу", addDependent: "Добавить зависимую задачу", cancel: "Отменить задачу", cancelHelp: "Отмена сохраняет задачу и её историю. Сначала завершите запрошенную или выполняющуюся работу.",
  title: "Название", description: "Описание", criteria: "Критерии приёмки — по одному на строку", dependencies: "Предварительные задачи", noDependencies: "Зависимостей нет", reason: "Причина отмены",
  save: "Сохранить изменения", add: "Создать зависимую задачу", close: "Закрыть редактор", working: "Сохраняется…", loading: "Загружается полная задача…", loadFailed: "Не удалось загрузить полную задачу. Ваш черновик сохранён здесь.",
  refresh: "Загрузить задачу заново", stale: "После открытия черновика задача изменилась. Черновик сохранён. Загрузите задачу заново перед подготовкой нового изменения.",
  live: "Сначала завершите запрошенную или выполняющуюся работу, затем изменяйте или отменяйте задачу.", unavailable: "Действие недоступно в текущем состоянии задачи.", readonly: "Для изменения нужны актуальные данные и сессия оператора.",
  failed: "Действие не завершено.", uncertain: "Результат не подтверждён. Повторите то же действие перед подготовкой другого изменения.", retry: "Повторить то же действие", newAttempt: "Подготовить новую попытку", success: "Сохранено в истории задачи.",
  required: "Введите название, описание и хотя бы один критерий приёмки.", cycle: "Эти зависимости образуют цикл.", missing: "Выбранная предварительная задача больше недоступна.", tooLarge: "Задача превышает допустимый размер редактора.",
  cancelRequired: "Введите причину отмены.", selectedCount: "задач показано", removeDependency: "Убрать недоступную зависимость", keyboard: "Стрелки перемещают между задачами. Tab открывает список задач и инспектор.",
  cancelledDependency: "Отменённая предварительная задача", sourceChanged: "История задачи обновлена", cancelConfirm: "Подтвердить отмену", draftKept: "Изменения остаются в черновике до сохранения."
};
export type TaskGraphMessage = keyof typeof en;
export function taskGraphText(locale: Locale, key: TaskGraphMessage): string { return (locale === "ru" ? ru : en)[key]; }
