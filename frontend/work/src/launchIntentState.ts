export type RunDraft = {
  agentId: string;
  taskId: string;
  contextPackKey: string;
  designPackageKey: string;
};

export type LaunchRequest = {
  agentId: string;
  taskId: string;
  contextPackId: string | null;
  contextPackRevision: string | null;
  designPackageId: string | null;
  designPackageRevision: string | null;
};

export type LaunchIntent = Readonly<{
  key: string;
  taskTitle: string;
  roleName: string;
  profileId: string | null;
  draft: Readonly<RunDraft>;
  input: Readonly<LaunchRequest>;
}>;

export function freezeLaunchIntent(intent: LaunchIntent): LaunchIntent {
  return Object.freeze({ ...intent, draft: Object.freeze({ ...intent.draft }), input: Object.freeze({ ...intent.input }) });
}

export function launchIntentIsVisible(intent: LaunchIntent, selectedTaskId: string | null, draft: RunDraft): boolean {
  return selectedTaskId === intent.input.taskId
    && draft.taskId === intent.draft.taskId && draft.agentId === intent.draft.agentId
    && draft.contextPackKey === intent.draft.contextPackKey && draft.designPackageKey === intent.draft.designPackageKey;
}

export function restoreLaunchDraft(intent: LaunchIntent): RunDraft {
  return { ...intent.draft };
}
