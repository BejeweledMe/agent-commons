import { type ReactElement, useEffect, useState } from "react";

import { ApiProblem, type WorkApi } from "../api";
import type { StarterPack, StarterPackCatalog } from "../contracts";
import type { MessageKey } from "../i18n";

type RefusalKind = "catalog_unavailable" | "setup" | "unavailable";

type SectionState =
  | { kind: "loading" }
  | { kind: "ready"; catalog: StarterPackCatalog }
  | { kind: "refused"; refusal: RefusalKind; code: string };

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

function PackCard({
  pack,
  text
}: {
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
        </section>
      ))}
    </article>
  );
}

export function StarterPacksSection({ api, text }: StarterPacksSectionProps): ReactElement {
  const [state, setState] = useState<SectionState>({ kind: "loading" });
  const [reloadToken, setReloadToken] = useState(0);

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
          state.catalog.packs.map((pack) => <PackCard key={pack.id} pack={pack} text={text} />)
        )
      ) : null}
    </section>
  );
}
