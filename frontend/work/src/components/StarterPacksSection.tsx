import { type ReactElement, useEffect, useRef, useState } from "react";

import { ApiProblem, type WorkApi } from "../api";
import type { AppliedStarterPackRole, StarterPack, StarterPackBlueprint, StarterPackCatalog } from "../contracts";
import { ContextPackRetryIdentity } from "../contextPackEditorState";
import type { MessageKey } from "../i18n";

type RefusalKind = "catalog_unavailable" | "setup" | "unavailable";

type SectionState =
  | { kind: "loading" }
  | { kind: "ready"; catalog: StarterPackCatalog }
  | { kind: "refused"; refusal: RefusalKind; code: string };

type ApplyState =
  | { kind: "idle" }
  | { kind: "submitting"; key: string }
  | { kind: "success"; key: string; roles: readonly AppliedStarterPackRole[] }
  | { kind: "error"; key: string; code: string; safeNextActions: readonly string[] };

type StarterPacksSectionProps = {
  api: WorkApi;
  text: (key: MessageKey) => string;
};

const refusalMessages: Readonly<Record<RefusalKind, { title: MessageKey; next: MessageKey }>> = {
  catalog_unavailable: {
    title: "starter_packs_refusal_unavailable_title",
    next: "starter_packs_refusal_unavailable_next"
  },
  setup: {
    title: "starter_packs_refusal_setup_title",
    next: "starter_packs_refusal_setup_next"
  },
  unavailable: {
    title: "starter_packs_error_title",
    next: "starter_packs_error_next"
  }
};

function refusalFrom(error: unknown): { refusal: RefusalKind; code: string } {
  const problem = error instanceof ApiProblem ? error : null;
  const code = problem?.apiError?.code ?? "request_unavailable";
  if (problem?.status === 409 && code === "starter_pack_catalog_unavailable") {
    return { refusal: "catalog_unavailable", code };
  }
  if (problem?.status === 409 && code.startsWith("setup_")) {
    return { refusal: "setup", code };
  }
  return { refusal: "unavailable", code };
}

function actionFailure(error: unknown): { code: string; safeNextActions: readonly string[] } {
  if (error instanceof ApiProblem) {
    return {
      code: error.apiError?.code ?? "request_unavailable",
      safeNextActions: error.apiError?.safeNextActions ?? []
    };
  }
  return { code: "request_unavailable", safeNextActions: [] };
}

function blueprintKey(packId: string, blueprintId: string): string {
  return `${packId}:${blueprintId}`;
}

function AppliedRoles({
  roles,
  text
}: {
  roles: readonly AppliedStarterPackRole[];
  text: (key: MessageKey) => string;
}): ReactElement {
  return (
    <ul className="starter-pack-applied-list">
      {roles.map((role) => (
        <li key={role.sourceRoleId}>
          <p className="starter-pack-role-name">{role.name}</p>
          <p className="starter-pack-role-meta">
            {text("starter_packs_template_label")}: <code>{role.agentId}</code>
            {" · "}
            {text("starter_packs_profile_label")}: <code>{role.profileId}</code>
            {" · "}
            {text("starter_packs_revision_label")}: <code>{role.revision}</code>
          </p>
        </li>
      ))}
    </ul>
  );
}

function PackCard({
  applyState,
  confirmed,
  onApply,
  onConfirm,
  pack,
  text
}: {
  applyState: ApplyState;
  confirmed: Readonly<Record<string, boolean>>;
  onApply: (pack: StarterPack, blueprint: StarterPackBlueprint) => void;
  onConfirm: (key: string, confirmed: boolean) => void;
  pack: StarterPack;
  text: (key: MessageKey) => string;
}): ReactElement {
  return (
    <article className="starter-pack-card">
      <header className="starter-pack-header">
        <h3>{pack.title}</h3>
        <span className="example-badge">{text("starter_packs_example_badge")}</span>
      </header>
      <p className="starter-pack-meta">
        <code>{pack.id}</code>
        {" · "}
        {text("starter_packs_version_label")} <code>{pack.version}</code>
        {" · "}
        {text("starter_packs_source_label")}: <code>{pack.sourceKind}</code>
        {" — "}
        {text("starter_packs_source_bundled_gloss")}
      </p>
      <p className="small-copy">{pack.summary}</p>
      {pack.blueprints.map((blueprint) => (
        <section aria-label={blueprint.title} className="starter-pack-blueprint" key={blueprint.id}>
          <h4>{blueprint.title}</h4>
          <p className="small-copy">{blueprint.summary}</p>
          <p className="starter-pack-roles-label">{text("starter_packs_roles_label")}</p>
          <ul className="starter-pack-role-list">
            {blueprint.roles.map((role) => (
              <li className="starter-pack-role" key={role.id}>
                <p className="starter-pack-role-name">{role.name}</p>
                <p className="small-copy">{role.purpose}</p>
                <p className="starter-pack-role-meta">
                  {text("starter_packs_profile_label")}: <code>{role.profileId}</code>
                </p>
                <p className="starter-pack-role-meta">
                  {text("starter_packs_context_label")}: <code>{role.contextMode}</code>
                  {" — "}
                  {text("starter_packs_context_fresh_gloss")}
                </p>
                <p className="starter-pack-role-meta">
                  {text("starter_packs_skills_label")}:{" "}
                  <span className="starter-pack-skills">
                    {role.skills.map((skill) => <code className="skill-chip" key={skill}>{skill}</code>)}
                  </span>
                </p>
              </li>
            ))}
          </ul>
          <div className="starter-pack-apply">
            <label className="starter-pack-confirm">
              <input
                checked={confirmed[blueprintKey(pack.id, blueprint.id)] === true}
                disabled={
                  applyState.kind === "submitting"
                  && applyState.key === blueprintKey(pack.id, blueprint.id)
                }
                onChange={(event) => onConfirm(
                  blueprintKey(pack.id, blueprint.id),
                  event.currentTarget.checked
                )}
                type="checkbox"
              />
              <span>{text("starter_packs_apply_confirm")}</span>
            </label>
            <button
              className="button button-primary"
              disabled={
                confirmed[blueprintKey(pack.id, blueprint.id)] !== true
                || (
                  applyState.kind === "submitting"
                  && applyState.key === blueprintKey(pack.id, blueprint.id)
                )
              }
              onClick={() => onApply(pack, blueprint)}
              type="button"
            >
              {applyState.kind === "submitting" && applyState.key === blueprintKey(pack.id, blueprint.id)
                ? text("starter_packs_apply_submitting")
                : text("starter_packs_apply")}
            </button>
            {applyState.kind === "success" && applyState.key === blueprintKey(pack.id, blueprint.id) ? (
              <div aria-live="polite" className="notice starter-pack-result" role="status">
                <p className="starter-pack-role-name">{text("starter_packs_apply_success")}</p>
                <p className="small-copy">{text("starter_packs_apply_success_next")}</p>
                <AppliedRoles roles={applyState.roles} text={text} />
              </div>
            ) : null}
            {applyState.kind === "error" && applyState.key === blueprintKey(pack.id, blueprint.id) ? (
              <div aria-live="assertive" className="notice starter-pack-result" role="alert">
                <p className="starter-pack-role-name">{text("starter_packs_apply_error")}</p>
                <p className="small-copy">{text("status_code")}: <code>{applyState.code}</code></p>
                {applyState.safeNextActions.length > 0 ? (
                  <ul>
                    {applyState.safeNextActions.map((action) => <li key={action}>{action}</li>)}
                  </ul>
                ) : (
                  <p className="small-copy">{text("starter_packs_apply_error_next")}</p>
                )}
              </div>
            ) : null}
          </div>
        </section>
      ))}
    </article>
  );
}

export function StarterPacksSection({ api, text }: StarterPacksSectionProps): ReactElement {
  const [state, setState] = useState<SectionState>({ kind: "loading" });
  const [reloadToken, setReloadToken] = useState(0);
  const [confirmed, setConfirmed] = useState<Readonly<Record<string, boolean>>>({});
  const [applyState, setApplyState] = useState<ApplyState>({ kind: "idle" });
  const retryIdentity = useRef(new ContextPackRetryIdentity());

  useEffect(() => {
    const controller = new AbortController();
    setState({ kind: "loading" });
    api.loadStarterPacks(controller.signal).then(
      (catalog) => setState({ kind: "ready", catalog }),
      (error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        setState({ kind: "refused", ...refusalFrom(error) });
      }
    );
    return () => controller.abort();
  }, [api, reloadToken]);

  const confirmBlueprint = (key: string, value: boolean): void => {
    setConfirmed((current) => ({ ...current, [key]: value }));
    if (
      !value
      && (applyState.kind === "success" || applyState.kind === "error")
      && applyState.key === key
    ) {
      setApplyState({ kind: "idle" });
    }
  };

  const applyBlueprint = (pack: StarterPack, blueprint: StarterPackBlueprint): void => {
    const key = blueprintKey(pack.id, blueprint.id);
    const idempotencyKey = retryIdentity.current.forOperation(
      JSON.stringify({ operation: "starter-pack-apply", packId: pack.id, blueprintId: blueprint.id })
    );
    const controller = new AbortController();
    setApplyState({ kind: "submitting", key });
    api.applyStarterPackBlueprint(pack.id, blueprint.id, idempotencyKey, controller.signal).then(
      (result) => {
        retryIdentity.current.reset();
        setApplyState({ kind: "success", key, roles: result.roles });
        setConfirmed((current) => ({ ...current, [key]: false }));
        setReloadToken((token) => token + 1);
      },
      (error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        setApplyState({ kind: "error", key, ...actionFailure(error) });
      }
    );
  };

  return (
    <section aria-label={text("starter_packs_title")} className="starter-packs-section">
      <header className="starter-packs-header">
        <h2>{text("starter_packs_title")}</h2>
        <span className="example-badge">{text("starter_packs_example_badge")}</span>
      </header>
      <p className="small-copy">{text("starter_packs_intro")}</p>
      {state.kind === "loading" ? (
        <p aria-live="polite" className="small-copy" role="status">{text("starter_packs_loading")}</p>
      ) : null}
      {state.kind === "refused" ? (
        <div aria-live="polite" className="notice" role="status">
          <p className="starter-pack-role-name">{text(refusalMessages[state.refusal].title)}</p>
          <p>{text(refusalMessages[state.refusal].next)}</p>
          {state.refusal !== "setup" ? (
            <p className="small-copy">{text("status_code")}: <code>{state.code}</code></p>
          ) : null}
          <button
            className="button button-secondary"
            onClick={() => setReloadToken((token) => token + 1)}
            type="button"
          >
            {text("starter_packs_check_again")}
          </button>
        </div>
      ) : null}
      {state.kind === "ready" ? (
        state.catalog.packs.length === 0 ? (
          <p className="small-copy">{text("starter_packs_empty")}</p>
        ) : (
          state.catalog.packs.map((pack) => (
            <PackCard
              applyState={applyState}
              confirmed={confirmed}
              key={pack.id}
              onApply={applyBlueprint}
              onConfirm={confirmBlueprint}
              pack={pack}
              text={text}
            />
          ))
        )
      ) : null}
    </section>
  );
}
