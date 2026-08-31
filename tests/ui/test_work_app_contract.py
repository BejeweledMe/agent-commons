"""Contracts for the isolated React Work application.

These checks intentionally cover the boundary between the data-free public
shell and the existing, cookie-authenticated local UI APIs.  They do not
duplicate component behaviour that belongs in the Work application's own
TypeScript tests: their job is to keep a future workflow from quietly becoming
another unauthenticated panel or another inline-script surface.
"""

from __future__ import annotations

import json
import re
import subprocess
import textwrap
from pathlib import Path

from fastapi.testclient import TestClient

from agent_commons.ui import read_work_shell, work_static_directory
from agent_commons.ui.context import UIContext
from agent_commons.ui.security import is_public_path, work_content_security_policy
from agent_commons.ui.server import create_app
from agent_commons.ui.setup import SETUP_UNINITIALIZED
from tests.ui.conftest import authorized

_REPOSITORY_ROOT = Path(__file__).parents[2]
_WORK_SOURCE = _REPOSITORY_ROOT / "frontend" / "work"


def _source(relative_path: str) -> str:
    return (_WORK_SOURCE / relative_path).read_text("utf-8")


def _bundle_tree(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_work_bundle_is_a_packaged_same_origin_shell() -> None:
    shell = read_work_shell()
    assets = work_static_directory() / "assets"

    assert '<script type="module" crossorigin src="/work/assets/work-' in shell
    assert assets.is_dir()
    assert any(path.suffix == ".js" for path in assets.iterdir())
    assert any(path.suffix == ".css" for path in assets.iterdir())


def test_work_shell_and_assets_are_public_but_the_workspace_api_is_not(client) -> None:  # type: ignore[no-untyped-def]
    shell = client.get("/work")
    assert shell.status_code == 200
    assert "Content-Security-Policy" in shell.headers
    assert "script-src 'self'" in shell.headers["Content-Security-Policy"]

    source = re.search(r'src="(/work/assets/[^\"]+\.js)"', shell.text)
    assert source is not None
    assert client.get(source.group(1)).status_code == 200

    refused = client.get("/api/setup")
    assert refused.status_code == 401
    assert refused.json()["error"]["code"] == "unauthorized"
    assert client.get("/api/setup", headers=authorized()).status_code == 200


def test_work_public_path_rule_never_exposes_an_api() -> None:
    assert is_public_path("/work") is True
    assert is_public_path("/work/") is True
    assert is_public_path("/work/assets/work-anything.js") is True
    assert is_public_path("/api/work") is False
    assert is_public_path("/work/api/setup") is False


def test_work_csp_allows_only_packaged_same_origin_assets() -> None:
    policy = work_content_security_policy()
    assert "script-src 'self'" in policy
    assert "style-src 'self'" in policy
    assert "connect-src 'self'" in policy
    assert "unsafe-inline" not in policy
    assert "unsafe-eval" not in policy


def test_work_source_and_build_contract_stays_separate_from_gallery() -> None:
    package = json.loads(_source("package.json"))
    vite = _source("vite.config.ts")
    entry = _source("src/main.tsx")

    assert package["name"] == "agent-commons-work"
    assert package["scripts"]["build"] == "tsc -b && vite build"
    assert package["dependencies"]["react"]
    assert package["dependencies"]["react-dom"]
    assert 'base: "/work/"' in vite
    assert 'outDir: "../../src/agent_commons/ui/static/work"' in vite
    assert 'entryFileNames: "assets/work-[hash].js"' in vite
    assert "createRoot" in entry
    assert 'import "./styles.css"' in entry


def test_work_checked_in_bundle_is_fresh_from_its_pinned_frontend_source(tmp_path: Path) -> None:
    """Rebuilding elsewhere must reproduce the exact packaged Work tree.

    ``make check`` installs the locked frontend dependencies before pytest.  The
    temporary ``outDir`` is essential: this regression guard may never rewrite
    the checked-in package assets it is comparing.
    """

    rebuilt = tmp_path / "rebuilt-work"
    result = subprocess.run(
        ["npm", "run", "build", "--", "--outDir", str(rebuilt)],
        cwd=_WORK_SOURCE,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert _bundle_tree(rebuilt) == _bundle_tree(work_static_directory())


def test_work_locales_are_paired_and_include_actionable_failure_guidance() -> None:
    messages = json.loads(_source("src/i18n.json"))
    assert set(messages) == {"en", "ru"}
    assert set(messages["en"]) == set(messages["ru"])

    failure_keys = {
        "failure_access_title",
        "failure_access_next",
        "failure_setup_title",
        "failure_setup_next",
        "failure_launch_title",
        "failure_launch_next",
        "failure_validation_title",
        "failure_validation_next",
        "failure_unavailable_title",
        "failure_unavailable_next",
        "failure_unknown_title",
        "failure_unknown_next",
        "safe_next_actions",
    }
    assert failure_keys <= set(messages["en"])
    for locale in ("en", "ru"):
        for key in failure_keys:
            assert messages[locale][key].strip(), (
                f"{locale}.{key} must tell the operator what to do"
            )

    panel = _source("src/components/FailurePanel.tsx")
    assert "failure.nextStep" in panel
    assert "failure.safeNextActions" in panel
    assert 'role="alert"' in panel


def test_expired_work_session_guidance_requires_a_fresh_url(client) -> None:  # type: ignore[no-untyped-def]
    response = client.get("/api/graph")

    assert response.status_code == 401
    actions = response.json()["error"]["safe_next_actions"]
    assert actions
    assert "stop the local panel" in actions[0]
    assert "newly printed Work URL" in actions[0]
    assert "reopen the URL printed" not in actions[0]


def test_work_source_keeps_fragment_exchange_and_cookie_session_rules() -> None:
    source = _source("src/api.ts")

    assert "exchangeCodeFromFragment" in source
    assert "restoreStoredSession" in source
    assert 'await this.get("/setup", signal)' in source
    assert 'fetch("/api/auth/exchange"' in source
    assert 'credentials: "same-origin"' in source
    assert "window.history.replaceState" in source
    history_clear = source.index("window.history.replaceState")
    stored_session_restore = source.index("await this.restoreStoredSession")
    fresh_exchange = source.index('fetch("/api/auth/exchange"')
    assert history_clear < stored_session_restore < fresh_exchange
    assert "window.sessionStorage.getItem(API_BASE_STORAGE_KEY)" in source
    assert "window.sessionStorage.setItem(API_BASE_STORAGE_KEY, value)" in source
    assert "clearStoredApiBase();" in source
    assert "localStorage" not in source
    assert "Authorization" not in source
    assert "tokenFromFragment" not in source


def test_work_preserves_a_live_stored_session_when_a_stale_fragment_meets_a_5xx(
    tmp_path: Path,
) -> None:
    """A transient probe failure must not spend a stale fragment or erase a live base."""

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

    script = textwrap.dedent(
        """
        import assert from "node:assert/strict";
        import { pathToFileURL } from "node:url";

        const storedBase = "/api/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        const storage = new Map([["agent_commons.ui.api_base", storedBase]]);
        let removed = 0;
        let exchangeAttempts = 0;
        globalThis.window = {
          location: { hash: "#c=stale-code", pathname: "/work" },
          history: { replaceState: () => {} },
          sessionStorage: {
            getItem: (key) => storage.get(key) ?? null,
            setItem: (key, value) => storage.set(key, value),
            removeItem: (key) => { removed += 1; storage.delete(key); }
          }
        };
        globalThis.fetch = async (url) => {
          if (url === "/api/auth/exchange") {
            exchangeAttempts += 1;
            throw new Error("a transient stored-session failure must not exchange the fragment");
          }
          assert.equal(url, `${storedBase}/setup`);
          return {
            ok: false,
            status: 503,
            json: async () => ({ error: { code: "temporarily_unavailable" } })
          };
        };
        const { WorkApi } = await import(pathToFileURL(process.argv[2]).href);
        await assert.rejects(new WorkApi().connect(new AbortController().signal));
        assert.equal(exchangeAttempts, 0);
        assert.equal(removed, 0);
        assert.equal(storage.get("agent_commons.ui.api_base"), storedBase);
        """
    )
    node = subprocess.run(
        ["node", "--input-type=module", "-", str(compiled / "api.js")],
        check=False,
        input=script,
        capture_output=True,
        text=True,
    )
    assert node.returncode == 0, node.stdout + node.stderr


def test_work_reads_project_identity_only_through_the_authenticated_opaque_api_base() -> None:
    api = _source("src/api.ts")
    entry = _source("src/main.tsx")

    assert 'this.get("/meta", signal)' in api
    assert 'fetch("/api/meta"' not in api
    assert "repositoryBasename" in entry
    assert "showFullProjectPath" in entry
    assert 'href="/"' in entry
    assert "review_in_legacy" in entry


def test_work_setup_guidance_is_closed_typed_and_only_loaded_after_setup() -> None:
    api = _source("src/api.ts")
    contracts = _source("src/contracts.ts")

    assert "SetupGuidanceBlockerCode" in contracts
    assert "SetupGuidanceTool" in contracts
    assert "SetupGuidanceNextActionKey" in contracts
    assert "parseSetupGuidance" in api
    assert "SETUP_GUIDANCE_BLOCKER_CODES" in api
    assert "SETUP_GUIDANCE_TOOLS" in api
    assert "SETUP_GUIDANCE_ACTION_KEYS" in api
    assert 'this.get("/work/setup-guidance", signal)' in api
    assert 'fetch("/api/work/setup-guidance"' not in api
    assert 'setup.state !== "setup_uninitialized"' in api
    assert "error.status === 409" in api
    assert api.index('this.get("/setup", signal)') < api.index(
        'this.get("/work/setup-guidance", signal)'
    )


def test_work_guidance_is_user_actionable_without_automatic_setup_mutation() -> None:
    messages = json.loads(_source("src/i18n.json"))
    entry = _source("src/main.tsx")

    keys = {
        "check_again",
        "guidance_missing_tools",
        "guidance_install_provider",
        "guidance_install_support_tool",
        "configuration_confirmation_title",
        "configuration_confirmation_write",
        "configuration_confirmation_non_actions",
        "configuration_confirmation_confirm",
        "configuration_confirmation_cancel",
    }
    assert keys <= set(messages["en"])
    for locale in ("en", "ru"):
        for key in keys:
            assert messages[locale][key].strip()

    assert "guidance.tools.join" in entry
    assert "onClick={() => void refresh()}" in entry
    assert "onClick={() => setConfigurationConfirmationOpen(true)}" in entry
    assert 'role="dialog"' in entry
    assert 'aria-modal="true"' in entry
    assert entry.count('apiRef.current.setup("runtime", signal)') == 1
    assert "function confirmRuntimeConfiguration" in entry
    assert 'guidance?.nextActionKey === "configure_runtime"' in entry


def test_work_guidance_reconciles_uninitialized_and_409_without_rendering_raw_values(
    tmp_path: Path,
) -> None:
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

    script = textwrap.dedent(
        """
        import assert from "node:assert/strict";
        import { pathToFileURL } from "node:url";

        const apiBase = "/api/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
        const storage = new Map([["agent_commons.ui.api_base", apiBase]]);
        const calls = [];
        let setupState = "setup_unconfigured";
        let guidance = {
          blocker_code: "setup_support_binary_unresolved",
          tools: ["git"],
          next_action_key: "install_support_tool_and_check_again",
          location_label: null
        };
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
          calls.push(url);
          if (url === `${apiBase}/setup`) {
            return { ok: true, status: 200, json: async () => ({ state: setupState }) };
          }
          if (url === `${apiBase}/meta`) {
            return { ok: true, status: 200, json: async () => ({ repo: "/work/project" }) };
          }
          if (url === `${apiBase}/work/setup-guidance`) {
            if (guidance === "409") {
              return {
                ok: false,
                status: 409,
                json: async () => ({ error: { code: "setup_uninitialized" } })
              };
            }
            return { ok: true, status: 200, json: async () => guidance };
          }
          throw new Error(`unexpected ${url}`);
        };
        const { WorkApi } = await import(pathToFileURL(process.argv[2]).href);
        const api = new WorkApi();
        await api.connect(new AbortController().signal);

        calls.length = 0;
        const supported = await api.load(new AbortController().signal);
        assert.deepEqual(calls, [
          `${apiBase}/setup`, `${apiBase}/meta`, `${apiBase}/work/setup-guidance`
        ]);
        assert.deepEqual(supported.guidance.tools, ["git"]);

        calls.length = 0;
        guidance = "409";
        const unavailable = await api.load(new AbortController().signal);
        assert.equal(unavailable.guidance, null);

        calls.length = 0;
        setupState = "setup_uninitialized";
        const uninitialized = await api.load(new AbortController().signal);
        assert.equal(uninitialized.guidance, null);
        assert.equal(calls.includes(`${apiBase}/work/setup-guidance`), false);

        setupState = "setup_unconfigured";
        guidance = {
          blocker_code: "setup_support_binary_unresolved",
          tools: ["raw-detail-DO-NOT-RENDER"],
          next_action_key: "install_support_tool_and_check_again",
          location_label: null
        };
        await assert.rejects(api.load(new AbortController().signal), (error) => {
          assert.equal(error.message, "request_unavailable");
          assert.equal(error.message.includes("raw-detail-DO-NOT-RENDER"), false);
          return true;
        });
        """
    )
    node = subprocess.run(
        ["node", "--input-type=module", "-", str(compiled / "api.js")],
        check=False,
        input=script,
        capture_output=True,
        text=True,
    )
    assert node.returncode == 0, node.stdout + node.stderr


def test_work_run_handoff_waits_for_finished_work_before_prompting_for_review() -> None:
    messages = json.loads(_source("src/i18n.json"))

    assert (
        "When the run has finished and the work is ready, request Review in the legacy panel."
        in messages["en"]["run_started_help"]
    )
    assert "Next, request Review" not in messages["en"]["run_started_help"]
    assert "when work is ready for Review" in messages["en"]["review_in_legacy"]

    russian_handoff = messages["ru"]["run_started_help"]
    assert "Когда прогон завершится и работа будет готова, запросите Review" in russian_handoff
    assert "Далее запросите Review" not in russian_handoff
    assert "когда работа готова к Review" in messages["ru"]["review_in_legacy"]


def test_work_client_drives_failures_from_typed_refusal_codes() -> None:
    source = _source("src/api.ts")
    entry = _source("src/main.tsx")

    assert "parseApiError" in source
    assert "safeNextActions" in source
    assert "new ApiProblem" in source
    assert 'code.startsWith("setup_")' in entry
    assert 'code === "launch_not_configured"' in entry
    assert 'code === "invalid_request"' in entry
    assert "safeNextActions" in entry
    assert "problem?.apiError?.message" not in entry


def test_work_uses_existing_setup_refusal_shape_without_a_404_feature_probe(tmp_path: Path) -> None:
    repository = tmp_path / "bare-repository"
    repository.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=repository, check=True, capture_output=True)
    context = UIContext(
        repository,
        state_root=tmp_path / "state",
        writer_session_id="session.00000000000000000000000003",
    )
    app = create_app(context, token="test-token", port=51234)

    with TestClient(app, base_url="http://127.0.0.1:51234") as work_client:
        refusal = work_client.post("/api/tasks", headers=authorized(), json={})

    assert refusal.status_code == 409
    assert refusal.json()["error"]["code"] == SETUP_UNINITIALIZED
    assert refusal.json()["error"]["safe_next_actions"]
