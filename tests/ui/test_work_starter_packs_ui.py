"""Contracts for the Starter Pack examples section of Work.

These checks pin the frontend slice of the bundled Starter Pack catalogue:
the section reads and explicitly applies only through the opaque authenticated
api base, renders CSP-safe DOM, keeps both locales honest that viewing is
non-mutating while applying creates ordinary role templates, and drives every
refusal off typed codes instead of raw payload values.  The backend route
contract lives in
``tests/ui/test_starter_pack_routes.py`` and is not duplicated here.
"""

from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).parents[2]
_WORK_SOURCE = _REPOSITORY_ROOT / "frontend" / "work"
_FORBIDDEN_RUSSIAN_FRAGMENTS = ("скилл", "тулл", "борд", "агент", "делегац")


def _source(relative_path: str) -> str:
    return (_WORK_SOURCE / relative_path).read_text("utf-8")


def _compiled_api(tmp_path: Path) -> Path:
    compiled = tmp_path / "compiled"
    compiler = _WORK_SOURCE / "node_modules" / ".bin" / "tsc"
    result = subprocess.run(
        [
            str(compiler),
            "--ignoreConfig",
            "--target",
            "ES2022",
            "--module",
            "ESNext",
            "--moduleResolution",
            "Bundler",
            "--lib",
            "ES2022,DOM",
            "--outDir",
            str(compiled),
            str(_WORK_SOURCE / "src" / "api.ts"),
        ],
        cwd=_WORK_SOURCE,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return compiled / "api.js"


def _run_node(script: str, compiled_api: Path) -> None:
    node = subprocess.run(
        ["node", "--input-type=module", "-", str(compiled_api)],
        check=False,
        input=script,
        capture_output=True,
        text=True,
    )
    assert node.returncode == 0, node.stdout + node.stderr


def test_starter_pack_locales_are_paired_and_separate_viewing_from_applying() -> None:
    messages = json.loads(_source("src/i18n.json"))
    english_keys = {key for key in messages["en"] if key.startswith("starter_packs_")}
    russian_keys = {key for key in messages["ru"] if key.startswith("starter_packs_")}

    assert english_keys == russian_keys
    assert "starter_packs_intro" in english_keys
    assert "starter_packs_apply_confirm" in english_keys
    assert "starter_packs_apply_success" in english_keys
    assert "starter_packs_check_again" in english_keys
    for locale in ("en", "ru"):
        for key in sorted(english_keys):
            assert messages[locale][key].strip(), f"{locale}.{key} must not be blank"

    english_intro = messages["en"]["starter_packs_intro"]
    assert "explicit confirmation" in english_intro
    assert "Viewing them still changes nothing" in english_intro
    assert "template roles" in english_intro
    assert "deny-by-default" in english_intro

    russian_intro = messages["ru"]["starter_packs_intro"]
    assert "явного подтверждения" in russian_intro
    assert "Просмотр по-прежнему ничего не меняет" in russian_intro
    assert "шаблоны ролей" in russian_intro
    assert "deny — запрет" in russian_intro

    for key in sorted(russian_keys):
        value = messages["ru"][key].lower()
        for fragment in _FORBIDDEN_RUSSIAN_FRAGMENTS:
            assert fragment not in value, f"ru.{key} uses forbidden transliteration {fragment!r}"


def test_starter_packs_use_opaque_api_base_with_safe_dom_and_explicit_apply() -> None:
    api = _source("src/api.ts")
    contracts = _source("src/contracts.ts")
    component = _source("src/components/StarterPacksSection.tsx")
    entry = _source("src/main.tsx")

    assert 'this.get("/work/starter-packs", signal)' in api
    assert "parseStarterPackCatalog" in api
    assert "parseStarterPackApplyResult" in api
    assert "loadStarterPacks" in api
    assert "applyStarterPackBlueprint" in api
    assert 'source_kind !== "bundled"' in api
    assert 'context_mode !== "fresh"' in api
    assert "value.example !== true" in api
    assert "value.template !== true" in api
    assert 'grant !== "deny"' in api
    for source in (api, component, entry):
        assert "/api/work/starter-packs" not in source

    for name in (
        "StarterPack",
        "StarterPackBlueprint",
        "StarterPackRole",
        "StarterPackCatalog",
        "StarterPackApplyResult",
        "AppliedStarterPackRole",
        "StarterPackSourceKind",
        "StarterPackContextMode",
    ):
        assert name in contracts

    assert "StarterPacksSection" in entry
    for banned in ("innerHTML", "dangerouslySetInnerHTML", "style={{", "<style", "fetch("):
        assert banned not in component

    assert "starter_pack_catalog_unavailable" in component
    assert 'code.startsWith("setup_")' in component
    assert "AbortController" in component
    assert "applyStarterPackBlueprint" in component
    assert "ContextPackRetryIdentity" in component
    assert "confirmed: true" not in component
    assert '{text("starter_packs_check_again")}' in component
    assert "starter_packs_empty" in component
    assert "starter_packs_apply_confirm" in component
    assert "starter_packs_apply_success" in component
    assert "starter_packs_apply_error" in component
    assert "error.message" not in component
    assert "WorkflowCard" not in component
    for forbidden_action in ("install", "use as basis"):
        assert forbidden_action not in component.lower()


def test_starter_pack_parser_round_trips_and_rejects_unknown_shapes(tmp_path: Path) -> None:
    script = textwrap.dedent(
        """
        import assert from "node:assert/strict";
        import { pathToFileURL } from "node:url";

        const apiBase = "/api/cccccccccccccccccccccccccccccccc";
        const storage = new Map([["agent_commons.ui.api_base", apiBase]]);
        const role = (id, name, purpose, profileId, skills) => (
          { id, name, purpose, profile_id: profileId, context_mode: "fresh", skills }
        );
        const packs = [
          {
            id: "starter.feature-delivery.mock",
            version: "0.1.0",
            title: "Feature delivery (example)",
            summary: "An implementer and an independent reviewer for a small technical change.",
            source_kind: "bundled",
            example: true,
            blueprints: [
              {
                id: "feature-delivery",
                title: "Feature delivery",
                summary: "Plan, implement, then independently review a bounded change.",
                roles: [
                  role(
                    "implementer",
                    "Implementer",
                    "Build the scoped change and report verifiable evidence.",
                    "claude-builder",
                    ["software-engineering", "qa-testing"]
                  ),
                  role(
                    "independent-reviewer",
                    "Independent reviewer",
                    "Assess the submitted work without inheriting the implementer's context.",
                    "claude-independent-reviewer",
                    ["qa-testing"]
                  )
                ]
              }
            ]
          },
          {
            id: "starter.product-discovery.mock",
            version: "0.1.0",
            title: "Product discovery (example)",
            summary: "A researcher and product reviewer for an evidence-backed product question.",
            source_kind: "bundled",
            example: true,
            blueprints: [
              {
                id: "product-discovery",
                title: "Product discovery",
                summary: "Gather evidence, then review the recommendation before an owner decides.",
                roles: [
                  role(
                    "researcher",
                    "Researcher",
                    "Collect bounded evidence and state assumptions and open questions.",
                    "codex-builder",
                    ["business-product-consulting"]
                  ),
                  role(
                    "product-reviewer",
                    "Product reviewer",
                    "Check the recommendation against evidence without deciding for the owner.",
                    "codex-independent-reviewer",
                    ["business-product-consulting"]
                  )
                ]
              }
            ]
          }
        ];
        let payload = { packs };
        const applyPayload = {
          pack_id: "starter.feature-delivery.mock",
          blueprint_id: "feature-delivery",
          applied: true,
          roles: [
            {
              source_role_id: "implementer",
              agent_id: "agent.01M1FPPEZHYF9C9QTR6PFKM2Q9",
              revision: "evt.01M1FPPEZHYF9C9QTR6PFKM2Q8",
              name: "Implementer",
              profile_id: "claude-builder",
              context_mode: "fresh",
              template: true,
              grants: { create_roles: "deny", retire_roles: "deny", open_links: "deny" },
              skills: ["commons-start", "software-engineering"]
            }
          ]
        };
        const posts = [];
        globalThis.window = {
          location: { hash: "", pathname: "/work" },
          history: { replaceState: () => {} },
          sessionStorage: {
            getItem: (key) => storage.get(key) ?? null,
            setItem: (key, value) => storage.set(key, value),
            removeItem: (key) => storage.delete(key)
          }
        };
        globalThis.fetch = async (url, init = {}) => {
          if (url === `${apiBase}/setup`) {
            return { ok: true, status: 200, json: async () => ({ state: "setup_configured" }) };
          }
          if (url === `${apiBase}/work/starter-packs`) {
            assert.equal(init.credentials, "same-origin");
            return { ok: true, status: 200, json: async () => payload };
          }
          assert.equal(
            url,
            `${apiBase}/work/starter-packs/starter.feature-delivery.mock/blueprints/feature-delivery/apply`
          );
          assert.equal(init.method, "POST");
          assert.equal(init.credentials, "same-origin");
          posts.push(JSON.parse(init.body));
          return { ok: true, status: 200, json: async () => applyPayload };
        };

        const { WorkApi } = await import(pathToFileURL(process.argv[2]).href);
        const api = new WorkApi();
        await api.connect(new AbortController().signal);

        const catalog = await api.loadStarterPacks(new AbortController().signal);
        assert.deepEqual(catalog, {
          packs: packs.map((pack) => ({
            id: pack.id,
            version: pack.version,
            title: pack.title,
            summary: pack.summary,
            sourceKind: "bundled",
            example: true,
            blueprints: pack.blueprints.map((blueprint) => ({
              id: blueprint.id,
              title: blueprint.title,
              summary: blueprint.summary,
              roles: blueprint.roles.map((entry) => ({
                id: entry.id,
                name: entry.name,
                purpose: entry.purpose,
                profileId: entry.profile_id,
                contextMode: "fresh",
                skills: entry.skills
              }))
            }))
          }))
        });

        payload = { packs: [] };
        const empty = await api.loadStarterPacks(new AbortController().signal);
        assert.deepEqual(empty, { packs: [] });

        const applied = await api.applyStarterPackBlueprint(
          "starter.feature-delivery.mock",
          "feature-delivery",
          "apply-key",
          new AbortController().signal
        );
        assert.deepEqual(posts, [{ confirmed: true, idempotency_key: "apply-key" }]);
        assert.deepEqual(applied, {
          packId: "starter.feature-delivery.mock",
          blueprintId: "feature-delivery",
          applied: true,
          roles: [
            {
              sourceRoleId: "implementer",
              agentId: "agent.01M1FPPEZHYF9C9QTR6PFKM2Q9",
              revision: "evt.01M1FPPEZHYF9C9QTR6PFKM2Q8",
              name: "Implementer",
              profileId: "claude-builder",
              contextMode: "fresh",
              template: true,
              grants: { create_roles: "deny", retire_roles: "deny", open_links: "deny" },
              skills: ["commons-start", "software-engineering"]
            }
          ]
        });

        const clone = () => JSON.parse(JSON.stringify({ packs }));
        const rejected = [];
        rejected.push(clone().packs);
        rejected.push({ packs: "raw-detail-DO-NOT-RENDER" });
        const notExample = clone();
        notExample.packs[0].example = false;
        rejected.push(notExample);
        const notBundled = clone();
        notBundled.packs[1].source_kind = "raw-detail-DO-NOT-RENDER";
        rejected.push(notBundled);
        const wrongContext = clone();
        wrongContext.packs[0].blueprints[0].roles[0].context_mode = "accumulated";
        rejected.push(wrongContext);
        const wrongSkill = clone();
        wrongSkill.packs[0].blueprints[0].roles[1].skills = ["qa-testing", 42];
        rejected.push(wrongSkill);
        const wrongProfile = clone();
        wrongProfile.packs[0].blueprints[0].roles[0].profile_id = "raw-detail-DO-NOT-RENDER";
        rejected.push(wrongProfile);
        const blankName = clone();
        blankName.packs[1].blueprints[0].roles[0].name = "";
        rejected.push(blankName);

        for (const broken of rejected) {
          payload = broken;
          await assert.rejects(api.loadStarterPacks(new AbortController().signal), (error) => {
            assert.equal(error.name, "ApiProblem");
            assert.equal(error.status, 502);
            assert.equal(error.message, "request_unavailable");
            assert.equal(error.message.includes("raw-detail-DO-NOT-RENDER"), false);
            return true;
          });
        }
        """
    )
    _run_node(script, _compiled_api(tmp_path))


def test_starter_pack_catalogue_409_surfaces_as_a_typed_refusal(tmp_path: Path) -> None:
    script = textwrap.dedent(
        """
        import assert from "node:assert/strict";
        import { pathToFileURL } from "node:url";

        const apiBase = "/api/dddddddddddddddddddddddddddddddd";
        const storage = new Map([["agent_commons.ui.api_base", apiBase]]);
        globalThis.window = {
          location: { hash: "", pathname: "/work" },
          history: { replaceState: () => {} },
          sessionStorage: {
            getItem: (key) => storage.get(key) ?? null,
            setItem: (key, value) => storage.set(key, value),
            removeItem: (key) => storage.delete(key)
          }
        };
        globalThis.fetch = async (url) => {
          if (url === `${apiBase}/setup`) {
            return { ok: true, status: 200, json: async () => ({ state: "setup_configured" }) };
          }
          assert.equal(url, `${apiBase}/work/starter-packs`);
          return {
            ok: false,
            status: 409,
            json: async () => ({
              error: {
                code: "starter_pack_catalog_unavailable",
                message: "bundled Starter Pack examples could not be verified"
              }
            })
          };
        };

        const { WorkApi } = await import(pathToFileURL(process.argv[2]).href);
        const api = new WorkApi();
        await api.connect(new AbortController().signal);
        await assert.rejects(api.loadStarterPacks(new AbortController().signal), (error) => {
          assert.equal(error.name, "ApiProblem");
          assert.equal(error.status, 409);
          assert.equal(error.apiError.code, "starter_pack_catalog_unavailable");
          assert.deepEqual(error.apiError.safeNextActions, []);
          return true;
        });
        """
    )
    _run_node(script, _compiled_api(tmp_path))
