import type { TaskEditDetail, TaskEditInput } from "./taskGraphApi.js";

export type TaskEditorDraft = {
  taskId: string; revision: string; title: string; description: string; criteria: string;
  dependencyIds: readonly string[]; stale: boolean;
  originalCriteria?: readonly string[];
};
export function taskEditorDraft(detail: TaskEditDetail): TaskEditorDraft {
  return { taskId: detail.taskId, revision: detail.revision, title: detail.title,
    description: detail.description, criteria: detail.criteria.join("\n"), originalCriteria: [...detail.criteria], dependencyIds: [...detail.dependencyIds], stale: false };
}
export function observeTaskEditorDraft(current: TaskEditorDraft | null, detail: TaskEditDetail): TaskEditorDraft {
  if (current === null || current.taskId !== detail.taskId) return taskEditorDraft(detail);
  return current.revision === detail.revision || current.stale ? current : { ...current, stale: true };
}
export function editorInput(draft: Pick<TaskEditorDraft, "title" | "description" | "criteria" | "dependencyIds" | "originalCriteria">): TaskEditInput {
  return { title: draft.title, description: draft.description,
    criteria: draft.originalCriteria?.join("\n") === draft.criteria ? [...draft.originalCriteria]
      : draft.criteria.split("\n").map((line) => line.trim()).filter(Boolean), dependencyIds: [...draft.dependencyIds] };
}
export function taskEditorInputError(input: TaskEditInput): "required" | "tooLarge" | null {
  if (!input.title.trim() || !input.description.trim() || input.criteria.length === 0) return "required";
  if (Array.from(input.title).length > 512 || Array.from(input.description).length > 16000 || input.criteria.length > 128
    || input.criteria.some((line) => Array.from(line).length > 2048) || input.dependencyIds.length > 128
    || new TextEncoder().encode(JSON.stringify(input)).byteLength > 65000) return "tooLarge";
  return null;
}
