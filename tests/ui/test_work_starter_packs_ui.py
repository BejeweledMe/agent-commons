"""Contracts for the read-only Starter Pack examples section of Work.

These checks pin the frontend slice of the bundled Starter Pack catalogue:
the section reads only through the opaque authenticated api base, renders
CSP-safe DOM, keeps both locales honest about the packs being uninstalled
examples, and drives every refusal off typed codes instead of raw payload
values.  The backend route contract lives in
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


def test_starter_pack_locales_are_paired_and_say_examples_are_not_installed() -> None:
    messages = json.loads(_source("src/i18n.json"))
    english_keys = {key for key in messages["en"] if key.startswith("starter_packs_")}
    russian_keys = {key for key in messages["ru"] if key.startswith("starter_packs_")}

    assert english_keys == russian_keys
    assert "starter_packs_intro" in english_keys
    assert "starter_packs_check_again" in english_keys
    for locale in ("en", "ru"):
        for key in sorted(english_keys):
            assert messages[locale][key].strip(), f"{locale}.{key} must not be blank"

    english_intro = messages["en"]["starter_packs_intro"]
    assert "example" in english_intro.lower()
    assert "not installed" in english_intro
    assert "creates no roles" in english_intro
    assert "changes nothing" in english_intro

    russian_intro = messages["ru"]["starter_packs_intro"]
    assert "ример" in russian_intro.lower()
    assert "не установлены" in russian_intro
    assert "не создаёт роли" in russian_intro
    assert "ничего не меняет" in russian_intro

    for key in sorted(russian_keys):
        value = messages["ru"][key].lower()
        for fragment in _FORBIDDEN_RUSSIAN_FRAGMENTS:
            assert fragment not in value, f"ru.{key} uses forbidden transliteration {fragment!r}"


def test_starter_packs_read_only_through_the_opaque_api_base_with_safe_dom() -> None:
    api = _source("src/api.ts")
    contracts = _source("src/contracts.ts")
    component = _source("src/components/StarterPacksSection.tsx")
    entry = _source("src/main.tsx")

    assert 'this.get("/work/starter-packs", signal)' in api
    assert "parseStarterPackCatalog" in api
    assert "loadStarterPacks" in api
    assert 'source_kind !== "bundled"' in api
    assert 'context_mode !== "fresh"' in api
    assert "value.example !== true" in api
    for source in (api, component, entry):
        assert "/api/work/starter-packs" not in source

    for name in (
        "StarterPack",
        "StarterPackBlueprint",
        "StarterPackRole",
        "StarterPackCatalog",
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
    assert '{text("starter_packs_check_again")}' in component
    assert "starter_packs_empty" in component
    assert "error.message" not in component
    assert "WorkflowCard" not in component
    for forbidden_action in ("apply", "install", "use as basis"):
        assert forbidden_action not in component.lower()


def test_starter_pack_parser_round_trips_and_rejects_unknown_shapes(tmp_path: Path) -> None:
    script = textwrap.dedent(
        """
        import assert from "node:assert/strict";
        import { pathToFileURL } from "node:url";

        const apiBase = "/api/cccccccccccccccccccccccccccccccc";
        const storage = new Map([["agent_commons.ui.api_base", apiBase]]);
        const role = (id, name, purpose, skills) => (
          { id, name, purpose, context_mode: "fresh", skills }
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
                    ["software-engineering", "qa-testing"]
                  ),
                  role(
                    "independent-reviewer",
                    "Independent reviewer",
                    "Assess the submitted work without inheriting the implementer's context.",
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
                    ["business-product-consulting"]
                  ),
                  role(
                    "product-reviewer",
                    "Product reviewer",
                    "Check the recommendation against evidence without deciding for the owner.",
                    ["business-product-consulting"]
                  )
                ]
              }
            ]
          }
        ];
        let payload = { packs };
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
          return { ok: true, status: 200, json: async () => payload };
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
                contextMode: "fresh",
                skills: entry.skills
              }))
            }))
          }))
        });

        payload = { packs: [] };
        const empty = await api.loadStarterPacks(new AbortController().signal);
        assert.deepEqual(empty, { packs: [] });

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
