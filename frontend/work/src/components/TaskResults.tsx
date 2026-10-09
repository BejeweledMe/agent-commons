import { useEffect, useMemo, useSyncExternalStore, useRef, type ReactElement } from "react";
import type { WorkApi } from "../api.js";
import { translate, type Locale } from "../i18n.js";
import { outputText } from "../outputsStrings.js";
import { TaskBrief } from "./TaskBrief.js";
import { TaskResultsReader } from "../taskResults.js";
export function TaskResults({ api, taskId, revision, locale, onRefresh }: { api: WorkApi; taskId: string; revision: string; locale: Locale; onRefresh: () => void }): ReactElement {
  const brief = useRef<{ taskId: string; description: string | null }>({ taskId, description: null });
  const reader = useMemo(() => new TaskResultsReader(api, taskId, revision), [taskId, revision]);
  reader.setApi(api);
  const state = useSyncExternalStore(reader.subscribe, reader.snapshot, reader.snapshot);
  if (brief.current.taskId !== taskId) brief.current = { taskId, description: null };
  if (state.page) brief.current.description = state.page.description;
  useEffect(() => { void reader.loadMore(); return () => reader.dispose(); }, [reader]);
  const t = (key: Parameters<typeof outputText>[1]): string => outputText(locale, key);
  return <div className="task-results">
    {brief.current.description !== null ? <TaskBrief key={taskId} description={brief.current.description} text={(key) => translate(locale, key)} /> : null}
    {state.page ? <>
      {state.page.acceptanceCriteria !== null ? <section className="task-criteria"><h3>{translate(locale, "inspector_criteria")}</h3><ul className="inspector-criteria">{state.page.acceptanceCriteria?.map((item, index) => <li key={index}>{item}</li>)}</ul></section> : null}
      {state.page.summary !== null ? <details className="inspector-disclosure" open><summary><h3>{translate(locale, "inspector_summary")}</h3></summary><p className="inspector-prose">{state.page.summary}</p></details> : null}
      <h3>{translate(locale, "inspector_evidence")}</h3><p className="small-copy">{state.page.evidence.length} / {state.page.total}</p><ol>{state.page.evidence.map((item) => <li key={`${item.ref.id}@${item.revision}`}>
      <h4>{item.title}</h4>{item.summary !== null ? <p className="inspector-prose">{item.summary}</p> : null}
      {item.checks.length ? <ul>{item.checks.map((check, index) => <li key={index}>{check}</li>)}</ul> : null}
      {item.stale ? <p>{t("evidenceStale")}</p> : null}
      <details><summary>{t("technicalDetails")}</summary><code>{item.ref.id} @ {item.revision}</code>{item.contentRevision ? <p><code>{item.contentRevision}</code></p> : null}</details>
    </li>)}</ol>{state.page.total === 0 ? <p>{t("empty")}</p> : null}</> : null}
    {state.loading ? <p role="status">{t("loading")}</p> : state.error ? <div role="alert"><p>{t(state.error === "changed" ? "taskChanged" : "failed")}</p><button className="button button-secondary" type="button" onClick={state.error === "changed" ? onRefresh : () => void reader.loadMore()}>{t(state.error === "changed" ? "refresh" : "retry")}</button></div> : null}
    {state.page?.nextOffset !== null && state.page && !state.error ? <button className="button button-secondary" type="button" disabled={state.loading} onClick={() => void reader.loadMore()}>{t("loadMoreEvidence")}</button> : null}
  </div>;
}
