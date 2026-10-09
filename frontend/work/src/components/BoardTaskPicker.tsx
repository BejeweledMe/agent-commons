import { boardPickerState } from "../boardState.js";
import { useMemo, useState, type ReactElement } from "react";
import type { MessageKey } from "../i18n.js";
import { filterTrackerTasks, stateLabel, taskFilterLabel, type TaskFilter } from "../taskPresentation.js";
import type { TrackerViewState } from "../trackerState.js";
import { Modal } from "./Modal.js";

/**
 * UX-03: "Give a task" always asks which task.
 *
 * The board's own selection lives in the Tasks tab and can be minutes old, so
 * it is offered as a *preselection* and never as the answer: this surface opens
 * with an explicit list, and nothing happens until a task is chosen here. The
 * component is read-only by construction — it issues no request of its own, and
 * its one outcome is handing a task id back to the shell, which opens Prepare
 * run. No assignment, no launch, no write of any kind.
 */
export type BoardTaskPickerProps = {
  agentName: string;
  /** The Tasks-tab selection, if any: a starting point for the list, not a choice. */
  preselectedTaskId: string | null;
  tracker: TrackerViewState;
  text: (key: MessageKey) => string;
  /** False means the panel cannot start a run; the reason stays visible and choosing still works. */
  writesEnabled: boolean;
  onCancel: () => void;
  onChoose: (taskId: string) => void;
};

/** The statuses worth offering here: the Work list's own filters, nothing invented. */
const PICKER_FILTERS: readonly TaskFilter[] = ["working", "attention", "ready", "active", "review", "all"];

export function BoardTaskPicker({
  agentName, preselectedTaskId, tracker, text, writesEnabled, onCancel, onChoose
}: BoardTaskPickerProps): ReactElement {
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<TaskFilter>("working");
  // The preselection starts the choice; it is not the choice until confirmed.
  const [chosen, setChosen] = useState<string | null>(preselectedTaskId);

  const state = boardPickerState(tracker);
  const tasks = useMemo(
    () => state === "ready" && tracker.kind === "ready" ? filterTrackerTasks(tracker.snapshot, filter, search) : [],
    [tracker, state, filter, search]
  );
  // A preselected task filtered out of the list cannot stay silently chosen.
  const chosenVisible = chosen !== null && tasks.some((task) => task.taskId === chosen);

  return <Modal size="medium" className="board-task-picker" title={text("board_role_give_task")} subtitle={agentName}
    closeLabel={text("shell_close")} onClose={onCancel}
    footer={<div className="board-picker-footer">
      <p className="small-copy" role="status">{chosenVisible ? "" : text("board_give_task_choose_first")}</p>
      <button type="button" className="button button-primary" disabled={!chosenVisible}
        onClick={() => { if (chosenVisible && chosen !== null) onChoose(chosen); }}>{text("board_give_task_confirm")}</button>
    </div>}>
    <p className="small-copy">{text("board_give_task_help")}</p>
    {writesEnabled ? null : <p className="notice" role="status">{text("board_give_task_read_only")}</p>}
    <div className="board-picker-controls">
      <label className="board-picker-search">{text("board_give_task_search")}
        <input type="search" maxLength={256} value={search} onChange={(event) => setSearch(event.target.value)} />
      </label>
      <label>{text("board_give_task_status")}
        <select value={filter} onChange={(event) => setFilter(event.target.value as TaskFilter)}>
          {PICKER_FILTERS.map((option) => <option key={option} value={option}>{text(taskFilterLabel[option])}</option>)}
        </select>
      </label>
    </div>
    {/* Loading and failure are the tracker's own states, reported as they are:
        an unread snapshot never renders as "this agent has no tasks". */}
    {state === "loading" ? <p role="status">{text("board_give_task_loading")}</p> : null}
    {state === "failure" ? <p className="notice" role="alert">{text("board_give_task_failed")}</p> : null}
    {state === "ready" ? tasks.length === 0
      ? <p className="empty-guidance">{text("board_give_task_empty")}</p>
      : <ul className="board-picker-list">{tasks.map((task) => <li key={task.taskId}>
        <button type="button" className="board-picker-option" aria-pressed={task.taskId === chosen}
          onClick={() => setChosen(task.taskId)}>
          <span className="board-picker-option-title">{task.title}</span>
          <span className="board-picker-option-state">{text(stateLabel("task", task.taskState))}</span>
          {task.taskId === preselectedTaskId ? <span className="board-picker-option-note">{text("board_give_task_preselected")}</span> : null}
          <code>{task.taskId}</code>
        </button>
      </li>)}</ul> : null}
  </Modal>;
}
