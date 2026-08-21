"""First run driven through the panel's own HTTP surface.

`ui.setup` answers the questions; this file is about the wiring around it, and
two properties are the whole reason the wiring exists:

- a panel configures itself **while it is serving**.  The operator writes the
  runtime config from the first-run screen and the very next Run works, with no
  restart -- which means `launch_enabled` has to flip, the permanently cached
  profile summary has to be dropped, and the launch route has to stop refusing;
- the paths that make the screen useful are visible exactly until they stop
  being needed.  While the runtime is unconfigured `GET /api/setup` names the
  binaries it found and the file it is about to write; once configured the same
  route answers with a state and nothing else.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import agent_commons.ui.setup as setup
from agent_commons.runtime.model import ExecutableResolutionError, ExecutableRole
from agent_commons.services import CommonsManager
from agent_commons.ui.context import (
    PANEL_ALREADY_OPEN_ACTIONS,
    SETUP_SUPPORT_BINARY_UNRESOLVED,
    UIContext,
)
from agent_commons.ui.server import CATALOG_ROUTES, SETUP_ROUTES, create_app
from tests.ui.conftest import (
    OPERATOR_SURFACE,
    PORT,
    authorized,
    expected_surface,
    mutating_surface,
)


@pytest.fixture(autouse=True)
def _isolated_operator_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The operator's real config and state overrides must not take part."""

    monkeypatch.delenv("AGENT_COMMONS_STATE_ROOT", raising=False)
    monkeypatch.delenv("AGENT_COMMONS_STATE_BASE", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))


def _client(context: UIContext):  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    app = create_app(context, token="test-token", port=PORT)
    return TestClient(app, base_url=f"http://127.0.0.1:{PORT}")


def _writing(workspace: dict[str, Any], *, window: str, **extra: Any) -> UIContext:
    """A panel with a real operator session and nothing else configured."""

    manager = CommonsManager(workspace["repo"], state_root=workspace["state_root"])
    session = manager.start_session(
        stable_instance_id=window,
        principal="operator",
        client="claude",
        software="claude-code",
        role="operator",
    )
    return UIContext(
        workspace["repo"],
        state_root=workspace["state_root"],
        writer_session_id=str(session["session_id"]),
        **extra,
    )


def _install(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.fixture
def provider_bin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A machine where every executable first run needs is resolvable."""

    bindir = tmp_path / "bin"
    for name in ("claude", "codex", "agent-commons-mcp", "git"):
        _install(bindir, name)
    monkeypatch.setenv("PATH", str(bindir))
    return bindir


def _strings(value: Any) -> list[str]:
    """Every string anywhere in a response body."""

    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [item for entry in value.values() for item in _strings(entry)]
    if isinstance(value, list):
        return [item for entry in value for item in _strings(entry)]
    return []


# -- the declared surface ------------------------------------------------------


def test_setup_writes_are_declared_apart_and_absent_from_a_read_only_panel(
    workspace: dict[str, Any],
) -> None:
    """First run is a privilege of its own, and a panel that records nothing
    does not get it: the read-only invariant is zero non-GET routes, setup
    included."""

    reading_only = UIContext(workspace["repo"], state_root=workspace["state_root"])
    with _client(reading_only) as client:
        assert mutating_surface(client.app) == expected_surface(reading_only) == set()

    writing = _writing(workspace, window="first-run-surface-window")
    with _client(writing) as client:
        found = mutating_surface(client.app)
    assert found == expected_surface(writing) == OPERATOR_SURFACE
    # Registered by a panel that is not configured yet -- being unconfigured is
    # the state these two routes exist to leave.
    assert writing.launch_enabled is False
    assert set(SETUP_ROUTES) <= found


def test_a_panel_on_a_repository_with_no_workspace_carries_the_surface_and_refuses_by_name(
    tmp_path: Path,
) -> None:
    """The whole surface is registered before the workspace exists, and every
    route that records refuses with the frozen code until it does.

    This is what makes first run reachable at all: the routes cannot wait for a
    workspace, because creating the workspace is one of them.  The refusal is
    raised from a dependency, so it lands before the request body is read --
    nothing in a body can make an absent workspace recordable.
    """

    import subprocess

    repo = tmp_path / "bare"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True, capture_output=True)
    context = UIContext(
        repo,
        state_root=tmp_path / "state",
        writer_session_id="session.00000000000000000000000003",
    )
    with _client(context) as client:
        assert mutating_surface(client.app) == expected_surface(context) == OPERATOR_SURFACE
        # Deliberately malformed: a body that could not be parsed proves the
        # refusal happened before anything looked at it.
        refused = client.post(
            "/api/tasks", headers=authorized(), content=b"{not json", timeout=None
        )
        catalogue = client.post("/api/catalog/entries", headers=authorized(), json={})
        launch = client.post("/api/delegations", headers=authorized(), json={})
        allowed = client.get("/api/setup", headers=authorized())

    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == setup.SETUP_UNINITIALIZED
    assert catalogue.json()["error"]["code"] == setup.SETUP_UNINITIALIZED
    assert launch.json()["error"]["code"] == setup.SETUP_UNINITIALIZED
    assert allowed.json()["state"] == setup.SETUP_UNINITIALIZED


def _reading_paths(app: Any) -> list[str]:
    """Every GET path the built application registers, read from the router.

    The coordinator's manual probe of four routes is exactly the kind of list
    kept in a head that misses the fifth; the router is the only complete
    account of the reading surface, so the walk reads it.
    """

    return sorted(
        route.path for route in app.routes if "GET" in (getattr(route, "methods", set()) or set())
    )


#: Fillers for parameterized paths.  The refusal under test is raised from a
#: dependency, before any handler looks at a parameter, so any syntactically
#: plausible value proves the point.
_PATH_FILLERS = {"{kind}": "task", "{entity_id}": "task.00000000000000000000000001"}

#: The reading routes that must answer before a workspace exists: the SPA and
#: its icon, `/api/meta` (the tab renders nothing until it returns), and
#: `GET /api/setup` -- the one entry point that names the state.
_ANSWERS_IN_EVERY_STATE = {"/", "/favicon.ico", "/api/meta", "/api/setup"}


def _walk_reading_surface(client: Any) -> dict[str, Any]:
    """GET every registered route; refuse-or-answer responses keyed by path."""

    responses = {}
    for path in _reading_paths(client.app):
        target = path
        for placeholder, value in _PATH_FILLERS.items():
            target = target.replace(placeholder, value)
        responses[path] = client.get(target, headers=authorized())
    return responses


def test_every_reading_route_on_a_bare_repository_refuses_by_name_or_answers(
    tmp_path: Path,
) -> None:
    """No reading route may 500 on the wave's headline directory.

    The writing half of the panel refused `setup_uninitialized` while the
    reading half let the manager's ConfigurationError escape -- as a 500 on
    the graph and runs, and as a 422 named after the exception class on the
    catalogue -- so the tab on a bare repository could not load against a
    fully working backend.  Every reading route now either answers (the four
    routes first run itself depends on) or refuses 409 with the frozen code in
    the exact shape the writing routes use, and `ConfigurationError` reaches
    no response in any form.
    """

    import subprocess

    repo = tmp_path / "bare"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True, capture_output=True)
    context = UIContext(
        repo,
        state_root=tmp_path / "state",
        writer_session_id="session.00000000000000000000000004",
    )
    with _client(context) as client:
        responses = _walk_reading_surface(client)

    # The walk must actually cover the surface: a shrunken route table would
    # pass every per-route assertion while checking nothing.
    assert {"/api/graph", "/api/runs", "/api/catalog", "/api/stream"} <= set(responses)
    for path, response in responses.items():
        assert "ConfigurationError" not in response.text, (path, response.text)
        if path in _ANSWERS_IN_EVERY_STATE:
            assert response.status_code in {200, 204}, (path, response.text)
            continue
        assert response.status_code == 409, (path, response.status_code, response.text)
        error = response.json()["error"]
        assert error["code"] == setup.SETUP_UNINITIALIZED, (path, error)
        assert error["message"], path
        assert error["safe_next_actions"], path
    assert responses["/api/setup"].json()["state"] == setup.SETUP_UNINITIALIZED


def test_every_reading_route_names_a_directory_that_is_not_a_repository(
    tmp_path: Path,
) -> None:
    """The same walk, one state earlier -- and on a read-only panel.

    The reading refusal is not a privilege of an operator panel: a read-only
    panel pointed at a directory that is not a repository has exactly as
    little to read, and its routes name that state rather than leaking the
    manager's failure.
    """

    loose = tmp_path / "loose"
    loose.mkdir()
    context = UIContext(loose, state_root=tmp_path / "state")
    with _client(context) as client:
        responses = _walk_reading_surface(client)

    assert {"/api/graph", "/api/runs", "/api/catalog", "/api/stream"} <= set(responses)
    for path, response in responses.items():
        assert "ConfigurationError" not in response.text, (path, response.text)
        if path in _ANSWERS_IN_EVERY_STATE:
            assert response.status_code in {200, 204}, (path, response.text)
            continue
        assert response.status_code == 409, (path, response.status_code, response.text)
        assert response.json()["error"]["code"] == setup.SETUP_NOT_A_REPOSITORY, path
    assert responses["/api/setup"].json()["state"] == setup.SETUP_NOT_A_REPOSITORY


def test_a_panel_that_lost_the_singleness_race_refuses_by_its_own_name(
    tmp_path: Path,
) -> None:
    """A deferred lock that loses the race must not masquerade as first run.

    Two panels opened on a directory with no workspace both defer their
    singleness lock; whichever resolves the workspace first takes it, and the
    other one's `writes_enabled` honestly answers False from then on.  But
    reporting that as `setup_uninitialized` told the operator the workspace
    was missing when it exists and is simply owned by another window.  The
    refusal keeps its frozen `panel_already_open` code, its text, and the
    first panel's address.
    """

    import subprocess

    from agent_commons.ui.session_owner import ProjectSessionOwner

    repo = tmp_path / "bare"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True, capture_output=True)
    state_root = tmp_path / "state"
    first = ProjectSessionOwner(repo, state_root=state_root)
    first.acquire_panel_lock(4321)
    second = ProjectSessionOwner(repo, state_root=state_root)
    second.acquire_panel_lock(9999)
    CommonsManager.initialize(repo, integrations=())
    first.ensure_active()  # resolving the workspace takes the deferred lock
    context = UIContext(repo, state_root=state_root, session_owner=second)
    try:
        with _client(context) as client:
            refused = client.post(
                "/api/tasks",
                json={"title": "Too many panels", "description": "the race is lost"},
                headers=authorized(),
            )
        assert refused.status_code == 409
        error = refused.json()["error"]
        assert error["code"] == "panel_already_open"
        assert "127.0.0.1:4321" in error["message"]
        assert "setup_uninitialized" not in str(refused.json())
    finally:
        second.shutdown()
        first.shutdown()


def test_the_losing_panel_still_boots_and_meta_says_who_owns_the_project(
    tmp_path: Path,
) -> None:
    """The refusal has to survive a GET, not only a write.

    `/api/meta` is the first request a tab makes and it renders nothing until
    that returns.  Reading the session directly meant the losing panel's very
    first request raised `PanelAlreadyOpenError` into a 500: the second window
    went blank, with no screen able to say that the first window is the live
    one or where it is -- the terminal was the only way to find out, which is
    the exact thing this wave removes.

    So it answers 200, reports itself as recording nothing, and names the
    refusal by its frozen code with the first panel's address beside it.
    """

    import subprocess

    from agent_commons.ui.session_owner import ProjectSessionOwner

    repo = tmp_path / "bare"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True, capture_output=True)
    state_root = tmp_path / "state"
    first = ProjectSessionOwner(repo, state_root=state_root)
    first.acquire_panel_lock(4321)
    second = ProjectSessionOwner(repo, state_root=state_root)
    second.acquire_panel_lock(9999)
    CommonsManager.initialize(repo, integrations=())
    first.ensure_active()  # resolving the workspace takes the deferred lock
    context = UIContext(repo, state_root=state_root, session_owner=second)
    try:
        with _client(context) as client:
            response = client.get("/api/meta", headers=authorized())
        assert response.status_code == 200, response.text
        meta = response.json()
        assert meta["writes_enabled"] is False
        assert meta["read_only"] is True
        assert meta["writer_session_id"] is None
        refusal = meta["session_refusal"]
        assert refusal["code"] == "panel_already_open"
        assert refusal["address"] == "127.0.0.1:4321"
        assert "127.0.0.1:4321" in refusal["message"]
        assert refusal["safe_next_actions"] == list(PANEL_ALREADY_OPEN_ACTIONS)
        # The workspace exists here, so the losing panel still reports it: this
        # is an occupied project, not an unconfigured one, and nothing in the
        # answer may point the operator at the first-run screen.
        assert meta["workspace_id"]
        assert "setup_uninitialized" not in str(meta)
    finally:
        second.shutdown()
        first.shutdown()


def test_meta_answers_on_a_repository_that_has_no_workspace_yet(tmp_path: Path) -> None:
    """The same defect, on the wave's headline scenario.

    `agent-commons ui` in a bare git repository is the whole promise, and the
    tab that opens on it fetched `/api/meta` first.  Building the manager to
    read a workspace id raised out of the handler, so the panel that was
    supposed to offer first run served a 500 to its own boot request instead.

    There is no id to report and no frozen code to carry -- which state this
    is remains `GET /api/setup`'s question, and inventing a verdict here would
    put a second first-run state machine beside it.  So: 200, a null id, and a
    null refusal, and the tab goes on to ask the surface that knows.
    """

    import subprocess

    from agent_commons.ui.session_owner import ProjectSessionOwner

    repo = tmp_path / "bare"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True, capture_output=True)
    owner = ProjectSessionOwner(repo, state_root=tmp_path / "state")
    context = UIContext(repo, state_root=tmp_path / "state", session_owner=owner)
    try:
        with _client(context) as client:
            response = client.get("/api/meta", headers=authorized())
            state = client.get("/api/setup", headers=authorized())
        assert response.status_code == 200, response.text
        meta = response.json()
        assert meta["workspace_id"] is None
        assert meta["session_refusal"] is None
        assert meta["writes_enabled"] is False
        assert meta["repo"] == str(repo)
        # And the surface that owns the question still answers it.
        assert state.json()["state"] == setup.SETUP_UNINITIALIZED
    finally:
        owner.shutdown()


def test_the_reading_half_of_setup_is_in_no_tuple_and_answers_read_only(
    workspace: dict[str, Any],
) -> None:
    reading_only = UIContext(workspace["repo"], state_root=workspace["state_root"])
    with _client(reading_only) as client:
        response = client.get("/api/setup", headers=authorized())
    assert response.status_code == 200
    assert response.json()["state"] == setup.SETUP_UNCONFIGURED
    # On `--read-only` no setup route is registered, and this is the only
    # answer the first-run screen gets before a workspace exists -- so it has
    # to say the panel cannot act, or the screen offers buttons that 404.
    assert response.json()["operator_panel"] is False


# -- the state the screen renders ---------------------------------------------


def test_an_unconfigured_panel_names_the_state_the_binaries_and_the_target(
    workspace: dict[str, Any], provider_bin: Path, tmp_path: Path
) -> None:
    context = _writing(workspace, window="first-run-status-window1")
    with _client(context) as client:
        body = client.get("/api/setup", headers=authorized()).json()

    assert body["state"] == setup.SETUP_UNCONFIGURED
    assert body["operator_panel"] is True
    assert body["launch_enabled"] is False
    assert body["blocking_refusal"] is None
    assert body["providers_found"] == ["claude", "codex"]
    assert body["providers_missing"] == []
    # The narrow exception: absolute paths, because choosing to trust them is
    # the decision the screen is asking the operator to make.
    assert body["providers"]["claude"]["path"] == str(provider_bin / "claude")
    assert body["config_path"] == str(tmp_path / "xdg-config" / "agent-commons" / "runtime.yaml")


def test_a_rejected_config_names_the_loaders_reason_and_leaves_the_file_alone(
    workspace: dict[str, Any], provider_bin: Path, tmp_path: Path
) -> None:
    """`setup_config_rejected_by_loader` must say why, and must not touch disk.

    The state branch used to eat the loader's CommonsError and answer with the
    bare code: the operator read "your file does not work" with no word about
    what is wrong, and the terminal was the only place to learn more -- the
    exact regress this wave closes.  The answer now carries the loader's own
    refusal text, in the same form `POST /api/setup/runtime-config` already
    reports it, and the path of the refused file.

    And because this file is the operator's own -- the product did not write
    it -- naming the state leaves it byte-for-byte where it stands.  Only a
    just-generated config the read-back refuses is renamed to `.rejected`,
    and that happens in the generator, never here.
    """

    from agent_commons.errors import CommonsError
    from agent_commons.services.delegation_runtime import load_runtime_configuration

    context = _writing(workspace, window="first-run-rejected-win1")
    with _client(context) as client:
        written = client.post("/api/setup/runtime-config", headers=authorized())
        assert written.status_code == 200, written.text
        target = setup.default_runtime_config_path()
        target.write_text(
            target.read_text(encoding="utf-8") + "bogus_field: true\n", encoding="utf-8"
        )
        before = target.read_bytes()
        body = client.get("/api/setup", headers=authorized()).json()

    assert body["state"] == setup.CONFIG_REJECTED_BY_LOADER
    with pytest.raises(CommonsError) as refusal:
        load_runtime_configuration(target, workspace_root=workspace["repo"])
    assert body["rejected_reason"] == str(refusal.value)
    assert body["rejected_path"] == str(target)
    assert target.read_bytes() == before
    assert not target.with_name(target.name + ".rejected").exists()


def test_a_directory_that_is_not_a_repository_is_named_not_refused(
    tmp_path: Path,
) -> None:
    bare = tmp_path / "not-a-repo"
    bare.mkdir()
    context = UIContext(bare, state_root=tmp_path / "state")
    with _client(context) as client:
        response = client.get("/api/setup", headers=authorized())
    assert response.status_code == 200
    assert response.json()["state"] == setup.SETUP_NOT_A_REPOSITORY


def test_total_absence_of_a_provider_is_named_before_the_operator_clicks(
    workspace: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    context = _writing(workspace, window="first-run-noprovider-win")
    with _client(context) as client:
        body = client.get("/api/setup", headers=authorized()).json()
        refused = client.post("/api/setup/runtime-config", headers=authorized())

    assert body["blocking_refusal"] == setup.SETUP_NO_PROVIDER_FOUND
    # The refusal is not a dead end: the same answer names demo as a way out,
    # or the person it stops would be left writing YAML by hand -- the exact
    # terminal step this panel exists to remove.
    assert body["demo_available"] is True
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == setup.SETUP_NO_PROVIDER_FOUND


def test_a_found_provider_without_its_support_binaries_carries_the_new_code(
    workspace: dict[str, Any], provider_bin: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The state `ui.setup` found and refused to invent a code for.

    The module raises an uncoded ConfigurationError there, keeping the
    resolver's reasons; binding the frozen `setup_support_binary_unresolved` to
    it is this wiring's job, and the reasons have to survive the binding.
    """

    real = setup.resolve_trusted_executable

    def resolver(value: str, *, workspace_root: Path, role: ExecutableRole) -> str:
        if role is ExecutableRole.MCP:
            raise ExecutableResolutionError(role, f"nothing usable at {value}")
        return real(value, workspace_root=workspace_root, role=role)

    monkeypatch.setattr(setup, "resolve_trusted_executable", resolver)
    context = _writing(workspace, window="first-run-nomcp-window1")
    with _client(context) as client:
        body = client.get("/api/setup", headers=authorized()).json()
        refused = client.post("/api/setup/runtime-config", headers=authorized())

    assert body["providers_found"] == ["claude", "codex"]
    assert body["support_missing"] == ["agent-commons-mcp"]
    assert body["blocking_refusal"] == SETUP_SUPPORT_BINARY_UNRESOLVED
    assert refused.status_code == 409
    error = refused.json()["error"]
    assert error["code"] == SETUP_SUPPORT_BINARY_UNRESOLVED
    # The resolver's own reason, per candidate, not a flattened "unavailable".
    assert "agent-commons-mcp" in error["message"]
    assert "nothing usable at" in error["message"]
    assert context.launch_enabled is False


# -- configuring a panel that is already serving -------------------------------


def test_the_panel_configures_itself_and_launches_without_a_restart(
    workspace: dict[str, Any], provider_bin: Path, tmp_path: Path
) -> None:
    """The load-bearing test of this wave.

    One TestClient, one app, one route table: the runtime config is written
    through the panel and the same panel launches afterwards.  Nothing here is
    rebuilt between the two halves, because in production nothing would be.
    """

    context = _writing(workspace, window="first-run-adopt-window01")
    with _client(context) as client:
        assert client.get("/api/launch", headers=authorized()).json()["launch_enabled"] is False
        refused = client.post(
            "/api/delegations",
            json={"agent_id": "agent.absent", "task_id": "task.absent"},
            headers=authorized(),
        )
        assert refused.json()["error"]["code"] == "launch_not_configured"

        written = client.post("/api/setup/runtime-config", headers=authorized())
        assert written.status_code == 200, written.text
        body = written.json()
        assert body["state"] == setup.SETUP_CONFIGURED
        assert body["launch_enabled"] is True
        assert body["providers_found"] == ["claude", "codex"]
        assert body["profiles"] == [
            "claude-builder",
            "claude-independent-reviewer",
            "codex-builder",
            "codex-independent-reviewer",
        ]

        # Same app, same route table, no restart.
        assert client.get("/api/launch", headers=authorized()).json()["launch_enabled"] is True
        after = client.post(
            "/api/delegations",
            json={"agent_id": "agent.absent", "task_id": "task.absent"},
            headers=authorized(),
        )

    assert context.launch_enabled is True
    # The launch gate is open: the refusal now comes from the domain being asked
    # about a role that does not exist, which is a different refusal entirely.
    assert after.json()["error"]["code"] != "launch_not_configured"
    assert (tmp_path / "xdg-config" / "agent-commons" / "runtime.yaml").is_file()


def test_the_demo_route_closes_the_whole_loop_with_no_resolvable_binary(
    workspace: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hire, task, Run, `succeeded` -- on a machine where nothing resolves.

    This is the machine `setup_no_provider_found` stops everywhere else, and
    the demo route is the frozen way out: a separate parameterless operation
    that writes `demo: true` and adopts it into the serving panel.  Everything
    below happens through one client against one route table -- the config is
    written by the route, the role is hired, the task is opened, and the run
    completes through the DemoRunner the adopted config selects, with PATH
    empty from the first request to the last.
    """

    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    context = _writing(workspace, window="first-run-demo-window01")
    with _client(context) as client:
        before = client.get("/api/setup", headers=authorized()).json()
        assert before["blocking_refusal"] == setup.SETUP_NO_PROVIDER_FOUND
        assert before["demo_available"] is True
        # The general write is refused on this machine; the demo one is not.
        refused = client.post("/api/setup/runtime-config", headers=authorized())
        assert refused.json()["error"]["code"] == setup.SETUP_NO_PROVIDER_FOUND

        written = client.post("/api/setup/demo-config", headers=authorized())
        assert written.status_code == 200, written.text
        body = written.json()
        assert body["state"] == setup.SETUP_CONFIGURED
        assert body["demo"] is True
        assert body["launch_enabled"] is True
        assert client.get("/api/setup", headers=authorized()).json()["state"] == (
            setup.SETUP_CONFIGURED
        )

        hired = client.post(
            "/api/agents",
            headers=authorized(),
            json={
                "name": "Demo builder",
                "profile_id": "claude-builder",
                "rationale": "sees the whole loop close without a provider",
                "idempotency_key": "demo-route-hire",
            },
        )
        assert hired.status_code == 200, hired.text
        role_id = hired.json()["entity_ref"]["id"]
        task = client.post(
            "/api/tasks",
            headers=authorized(),
            json={
                "title": "Close the demo loop",
                "description": "the DemoRunner completes this without a provider",
                "acceptance_criteria": ["the delegation reaches succeeded"],
                "idempotency_key": "demo-route-task",
            },
        )
        assert task.status_code == 200, task.text
        task_id = task.json()["entity_ref"]["id"]

        run = client.post(
            "/api/delegations",
            headers=authorized(),
            json={"agent_id": role_id, "task_id": task_id},
        )
        assert run.status_code == 200, run.text
        delegation_id = run.json()["delegation_id"]

    context.await_launches()
    manager = CommonsManager(workspace["repo"], state_root=workspace["state_root"], read_only=True)
    delegation = manager.get_delegation(delegation_id)
    assert delegation["state"] == "succeeded"
    # Honestly labelled: no provider ran, and the summary says so.
    assert "demo" in str(delegation.get("summary", "")).lower()
    assert "no provider" in str(delegation.get("summary", "")).lower()
    assert (tmp_path / "xdg-config" / "agent-commons" / "runtime.yaml").is_file()


def test_the_demo_write_refuses_to_replace_a_working_operator_config(
    workspace: dict[str, Any], provider_bin: Path, tmp_path: Path
) -> None:
    """The demo route is registered for the panel's whole life; the button is not.

    The first-run screen hides demo once the environment is configured, but a
    bare authorized POST reaches the route regardless -- and it used to answer
    200 and replace the operator's working `runtime.yaml` wholesale, marker
    gone, no backup.  The refusal has to come from the handler, typed with the
    state it refuses on, and the file has to still be byte-identical afterwards.
    """

    context = _writing(workspace, window="first-run-demoguard-w1")
    with _client(context) as client:
        written = client.post("/api/setup/runtime-config", headers=authorized())
        assert written.status_code == 200, written.text
        config = tmp_path / "xdg-config" / "agent-commons" / "runtime.yaml"
        # An operator's own touch: still valid YAML, so the loader keeps
        # accepting the file and the environment stays `setup_configured`.
        marked = config.read_bytes() + b"# the operator wrote this line by hand\n"
        config.write_bytes(marked)

        refused = client.post("/api/setup/demo-config", headers=authorized())

    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == setup.SETUP_CONFIGURED
    assert config.read_bytes() == marked


def test_adoption_drops_the_profile_summary_cached_before_the_config_existed(
    workspace: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_profile_info` is cached for the life of the process on purpose.

    A panel that adopted a config without dropping that cache would keep serving
    the summary it built while unconfigured -- the loader's deliberately
    unlaunchable default registry -- and the hire form would describe profiles
    that are not the ones a run would use.  Only claude is installed here, so
    the two answers cannot look alike.
    """

    bindir = tmp_path / "bin"
    for name in ("claude", "agent-commons-mcp", "git"):
        _install(bindir, name)
    monkeypatch.setenv("PATH", str(bindir))
    context = _writing(workspace, window="first-run-cache-window01")

    before = context.profile_info()
    assert sorted(before) == [
        "claude-builder",
        "claude-independent-reviewer",
        "codex-builder",
        "codex-independent-reviewer",
    ]

    context.configure_runtime()

    assert sorted(context.profile_info()) == ["claude-builder", "claude-independent-reviewer"]


def test_an_adopted_config_hands_the_panel_the_catalogue_path_beside_it(
    workspace: dict[str, Any], provider_bin: Path, tmp_path: Path
) -> None:
    """`RuntimeConfiguration.catalog_path` exists for exactly this hand-off.

    The generated config seeds a catalogue beside itself; the structure used to
    carry that file's *contents* and not its name, so the panel had no way to
    learn which file to read back.

    The second half is the rest of the promise, and it used to be a pinned
    caveat: the panel adopted the catalogue, said editing was enabled, and
    `POST /api/catalog/entries` answered 404 because the route table had been
    decided when the app was built.  The catalogue routes are now registered by
    every operator panel, so adopting a catalogue mid-flight really does turn
    editing on -- in this process, without a restart.
    """

    context = _writing(workspace, window="first-run-catalog-window")
    with _client(context) as client:
        registered = mutating_surface(client.app)
        client.post("/api/setup/runtime-config", headers=authorized())
        catalogue = client.get("/api/catalog", headers=authorized()).json()
        saved = client.post(
            "/api/catalog/entries",
            headers=authorized(),
            json={
                "section": "skills",
                "id": "house-style",
                "title": "House style",
                "instruction": "Follow the house style.",
            },
        )

    seeded = tmp_path / "xdg-config" / "agent-commons" / "catalog.yaml"
    assert seeded.is_file()
    assert catalogue["catalog_path"] == str(seeded)
    assert context.catalog_editing_enabled is True
    assert set(CATALOG_ROUTES) <= registered
    assert saved.status_code == 200, saved.text
    assert "house-style" in seeded.read_text(encoding="utf-8")


# -- paths stop at the moment they stop being needed ---------------------------


def test_a_configured_panel_hands_out_no_path_at_all(
    workspace: dict[str, Any], provider_bin: Path, tmp_path: Path
) -> None:
    """Owner's decision 3, closed by test: after setup, never again.

    The paths are not filtered out of a gathered answer -- discovery is not run
    at all -- so there is nothing to leak by forgetting a key.
    """

    context = _writing(workspace, window="first-run-noleak-window1")
    with _client(context) as client:
        written = client.post("/api/setup/runtime-config", headers=authorized())
        body = client.get("/api/setup", headers=authorized()).json()

    assert body["state"] == setup.SETUP_CONFIGURED
    assert body["launch_enabled"] is True
    assert set(body) == {"state", "operator_panel", "launch_enabled", "catalog_editing_enabled"}
    for payload in (body, written.json()):
        for value in _strings(payload):
            assert os.sep not in value, value
            assert str(tmp_path) not in value
            assert str(provider_bin) not in value


# -- initialization from the panel ---------------------------------------------


def test_the_panel_creates_the_workspace_through_the_initializer_init_uses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A repository with no workspace is a state, not an error, and the panel
    leaves it by calling the same `CommonsManager.initialize` the CLI does."""

    import subprocess

    repo = tmp_path / "fresh"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True, capture_output=True)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    # A workspace does not exist yet, so no session can be opened against it;
    # the id here only opens the writing surface, and initialization records no
    # canonical event of its own.
    context = UIContext(
        repo,
        state_root=tmp_path / "state",
        writer_session_id="session.00000000000000000000000001",
    )
    with _client(context) as client:
        assert client.get("/api/setup", headers=authorized()).json()["state"] == (
            setup.SETUP_UNINITIALIZED
        )
        created = client.post("/api/setup/initialize", headers=authorized())
        after = client.get("/api/setup", headers=authorized()).json()

    assert created.status_code == 200, created.text
    assert created.json()["state"] == setup.SETUP_UNCONFIGURED
    assert created.json()["integrations"] == ["codex", "claude"]
    assert (repo / ".agent-commons" / "workspace.yaml").is_file()
    assert after["state"] == setup.SETUP_UNCONFIGURED


def test_initialization_refuses_a_directory_that_is_not_a_repository(
    tmp_path: Path,
) -> None:
    bare = tmp_path / "loose"
    bare.mkdir()
    context = UIContext(
        bare,
        state_root=tmp_path / "state",
        writer_session_id="session.00000000000000000000000002",
    )
    with _client(context) as client:
        refused = client.post("/api/setup/initialize", headers=authorized())
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == setup.SETUP_NOT_A_REPOSITORY
    assert not (bare / ".agent-commons").exists()


# -- preflight, debuting in the panel ------------------------------------------


def test_preflight_refuses_before_there_is_anything_to_check(
    workspace: dict[str, Any],
) -> None:
    context = _writing(workspace, window="first-run-preflight-w01")
    with _client(context) as client:
        response = client.get("/api/setup/preflight", headers=authorized())
    assert response.status_code == 409
    assert response.json()["error"]["code"] == setup.SETUP_UNCONFIGURED


def test_preflight_runs_the_configured_profiles_and_says_what_it_cannot_know(
    workspace: dict[str, Any], provider_bin: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every answer carries the credential-free caveat.

    `preflight_profile` is substituted here so no provider process is started;
    what is under test is that the panel checks the profiles it actually
    configured and never lets a green result be read as "you are signed in"
    (decision.5QPR0HQYNAG3XKBMMKBJCAG1RB).
    """

    import agent_commons.runtime.preflight as preflight_module

    seen: list[str] = []

    def fake(profiles: Any, profile_id: Any, **_kwargs: Any) -> dict[str, Any]:
        seen.append(str(profile_id))
        return {"profile_id": str(profile_id), "ok": True, "checks": {}}

    context = _writing(workspace, window="first-run-preflight-w02")
    context.configure_runtime()
    monkeypatch.setattr(preflight_module, "preflight_profile", fake)

    with _client(context) as client:
        body = client.get("/api/setup/preflight", headers=authorized()).json()

    assert body["ok"] is True
    assert body["credential_free"] is True
    assert "signed-out" in body["note"]
    assert seen == [
        "claude-builder",
        "claude-independent-reviewer",
        "codex-builder",
        "codex-independent-reviewer",
    ]
