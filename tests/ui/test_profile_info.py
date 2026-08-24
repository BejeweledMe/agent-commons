"""What the catalogue may say about the profiles a role can be hired into.

A role names a profile and never a model, so the panel can only answer "what
will actually run?" from the operator's own config.  Two properties are the
whole point of the surface and neither follows from the code reading right:
exactly provider and model leave the process, and an operator config this
server cannot read costs the reader a detail rather than the whole catalogue.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent_commons.domain.agent_projection import AgentRecord
from agent_commons.domain.agents import PROFILE_NARROWING
from agent_commons.services import CommonsManager
from agent_commons.ui.context import UIContext
from tests.ui.conftest import PORT, authorized

#: A config the loader accepts, naming a model for one profile and leaving the
#: other silent -- both halves of the answer in one file.  The executables are
#: never invoked here; they exist so the leak test has real values to look for.
CONFIG = (
    "profiles:\n"
    "  claude-builder:\n"
    "    executable: /bin/echo\n"
    "    mcp_executable: /bin/echo\n"
    "    git_executable: /usr/bin/git\n"
    "    model: claude-sonnet-4-5\n"
    "    permission_mode: acceptEdits\n"
    "    trusted_workspace: true\n"
    "  codex-independent-reviewer:\n"
    "    executable: /bin/echo\n"
    "    mcp_executable: /bin/echo\n"
    "    git_executable: /usr/bin/git\n"
    "    sandbox: read-only\n"
)


def _client(context: UIContext):  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from agent_commons.ui.server import create_app

    return TestClient(
        create_app(context, token="test-token", port=PORT),
        base_url=f"http://127.0.0.1:{PORT}",
    )


def _configured(workspace: dict[str, Any], body: str) -> Path:
    """Write an operator config where the loader will accept it.

    Outside the delegated workspace and owner-writable only: the loader refuses
    anything else, and a fixture that trips its own guard proves nothing.
    """

    path = workspace["repo"].parent / "runtime.yaml"
    path.write_text(body, encoding="utf-8")
    path.chmod(0o600)
    return path


def _context(workspace: dict[str, Any], profile_config: Path | None) -> UIContext:
    return UIContext(
        workspace["repo"],
        state_root=workspace["state_root"],
        profile_config=profile_config,
    )


def test_the_catalogue_names_the_provider_and_the_model_and_nothing_else(
    workspace: dict[str, Any],
) -> None:
    """The profile detail is exactly two fields, by assertion and not by review.

    Everything else a profile body carries -- the executable, the argv it is
    built into, the sandbox or permission mode that narrows it -- describes the
    operator's machine and the guard around it, and a bearer token is not
    entitled to any of it.
    """

    context = _context(workspace, _configured(workspace, CONFIG))
    catalogue = context.catalog()
    info = catalogue["profile_info"]

    assert set(info) == {"claude-builder", "codex-independent-reviewer"}
    assert set(info) <= set(PROFILE_NARROWING)
    for detail in info.values():
        assert set(detail) == {"provider", "model"}
    assert info["claude-builder"] == {"provider": "claude", "model": "claude-sonnet-4-5"}
    assert info["codex-independent-reviewer"] == {"provider": "codex", "model": None}

    # The whole response, not just the new field: a leak anywhere else in the
    # catalogue is the same leak.
    body = json.dumps(catalogue)
    for absent in ('"executable"', '"argv"', '"sandbox"', '"permission_mode"', '"env"'):
        assert absent not in body
    for absent in ("/bin/echo", "acceptEdits", "read-only", "trusted_workspace"):
        assert absent not in body


def test_the_values_survive_the_trip_through_json(workspace: dict[str, Any]) -> None:
    """Provider is a string on the wire, not an enum that only prints like one."""

    context = _context(workspace, _configured(workspace, CONFIG))
    with _client(context) as client:
        response = client.get("/api/catalog", headers=authorized())
    assert response.status_code == 200
    info = response.json()["profile_info"]
    assert info["claude-builder"]["provider"] == "claude"
    assert isinstance(info["claude-builder"]["provider"], str)
    assert info["codex-independent-reviewer"]["model"] is None


def test_without_an_operator_config_every_profile_admits_it_names_no_model(
    workspace: dict[str, Any],
) -> None:
    """No config is not an error: the built-in profiles pin no model, and saying
    so is the honest answer the panel needs to show "fixed in the profile"."""

    context = _context(workspace, None)
    info = context.catalog()["profile_info"]

    assert sorted(info) == sorted(PROFILE_NARROWING)
    assert all(detail["model"] is None for detail in info.values())
    assert {name: detail["provider"] for name, detail in info.items()} == {
        "claude-builder": "claude",
        "claude-independent-reviewer": "claude",
        "codex-builder": "codex",
        "codex-independent-reviewer": "codex",
    }


@pytest.mark.parametrize("flavour", ["missing", "group-writable"])
def test_an_unreadable_operator_config_costs_a_detail_and_not_the_catalogue(
    workspace: dict[str, Any], flavour: str
) -> None:
    """A config the loader refuses must not take `/api/catalog` down.

    The catalogue is what the panel builds every form from; failing it because
    one optional detail is unavailable would take the workspace offline over a
    file permission.  Both refusals the operator can actually hit are covered:
    the path is gone, and the file is writable by more than its owner.
    """

    if flavour == "missing":
        config = workspace["repo"].parent / "no-such-runtime.yaml"
    else:
        config = _configured(workspace, CONFIG)
        config.chmod(0o664)

    with _client(_context(workspace, config)) as client:
        response = client.get("/api/catalog", headers=authorized())

    assert response.status_code == 200
    payload = response.json()
    assert payload["profile_info"] == {}
    # The rest of the catalogue is untouched -- the panel still has its forms.
    assert payload["profiles"] == sorted(PROFILE_NARROWING)


def test_the_operator_config_is_read_once_and_not_once_per_request(
    workspace: dict[str, Any],
) -> None:
    """The config is an operator file outside the workspace that cannot change
    under a running server; re-reading it per request buys nothing and puts a
    syscall on the path every form in the panel waits for.  Deleting the file
    after the first read proves the second answer came from memory."""

    config = _configured(workspace, CONFIG)
    context = _context(workspace, config)
    first = context.catalog()["profile_info"]
    config.unlink()
    assert context.catalog()["profile_info"] == first


# --- the models the hire form may offer -------------------------------------


def _hire(
    workspace: dict[str, Any], *, name: str, profile_id: str, model: str | None = None
) -> str:
    """Record one standing role the way the panel's hire route does."""

    manager = CommonsManager(workspace["repo"], state_root=workspace["state_root"])
    session = manager.start_session(
        stable_instance_id=f"model-options-{name}",
        principal="operator",
        client="claude",
        software="claude-code",
        role="operator",
    )
    bound = CommonsManager(
        workspace["repo"],
        state_root=workspace["state_root"],
        session_id=str(session["session_id"]),
    )
    created = bound.create_agent(
        name=name,
        profile_id=profile_id,
        rationale="staffed to populate the offer list",
        model=model,
    )
    return str(created["entity_ref"]["id"])


def test_the_offer_list_comes_from_the_two_honest_sources_and_no_others(
    workspace: dict[str, Any],
) -> None:
    """The panel may name no model of its own -- the frontend asset is pinned
    against carrying one -- so the hire form's suggestions can only come from
    the server, and only from somewhere this project can point at: the models
    the operator's own profiles name, and the models roles here already run on.

    A key per provider, always present, so the form looks one up by the
    selected profile's provider without knowing which providers exist.
    """

    _hire(
        workspace,
        name="Reviewer",
        profile_id="codex-independent-reviewer",
        model="gpt-5.2-codex",
    )
    _hire(workspace, name="Plain", profile_id="claude-builder")

    options = _context(workspace, _configured(workspace, CONFIG)).catalog()["model_options"]

    assert sorted(options) == ["claude", "codex"]
    # claude-sonnet-4-5 is the configured profile's; gpt-5.2-codex is a role's.
    assert options["claude"] == ["claude-sonnet-4-5"]
    assert options["codex"] == ["gpt-5.2-codex"]


def test_a_provider_with_nothing_to_offer_still_gets_its_key(
    workspace: dict[str, Any],
) -> None:
    """Empty is a normal answer, not a missing one: on a fresh project the
    field is simply typed into, and the form must not have to tell an absent
    key apart from an empty list."""

    options = _context(workspace, None).catalog()["model_options"]

    assert options == {"claude": [], "codex": []}


def test_a_retired_role_stops_offering_its_model(workspace: dict[str, Any]) -> None:
    """The list says what this project runs *now*.  A model that only a retired
    role ever used is not something the project is running, and offering it
    would make the list grow forever with every experiment."""

    manager = CommonsManager(workspace["repo"], state_root=workspace["state_root"])
    session = manager.start_session(
        stable_instance_id="model-options-retire",
        principal="operator",
        client="claude",
        software="claude-code",
        role="operator",
    )
    bound = CommonsManager(
        workspace["repo"],
        state_root=workspace["state_root"],
        session_id=str(session["session_id"]),
    )
    created = bound.create_agent(
        name="Experiment",
        profile_id="claude-builder",
        rationale="tried once",
        model="claude-opus-4-6",
    )
    context = _context(workspace, None)
    assert context.catalog()["model_options"]["claude"] == ["claude-opus-4-6"]

    bound.retire_agent(
        created["entity_ref"]["id"],
        created["revision"],
        reason="the experiment is over",
    )
    assert context.catalog()["model_options"]["claude"] == []


def test_a_name_no_launch_would_accept_is_never_offered(
    workspace: dict[str, Any],
) -> None:
    """The list is re-validated on the way out.

    Both sources are files this process may not have written -- an operator
    config and replayed canonical events -- so a name that could not survive a
    launch has no business being offered for one.  A leading dash is the case
    that matters: offered and picked, it would arrive at a provider as a flag.
    """

    manager = CommonsManager(workspace["repo"], state_root=workspace["state_root"])
    session = manager.start_session(
        stable_instance_id="model-options-hostile",
        principal="operator",
        client="claude",
        software="claude-code",
        role="operator",
    )
    bound = CommonsManager(
        workspace["repo"],
        state_root=workspace["state_root"],
        session_id=str(session["session_id"]),
    )
    good = bound.create_agent(
        name="Good",
        profile_id="claude-builder",
        rationale="a name a launch accepts",
        model="claude-opus-4-6",
    )
    # Reach past the write guard the way a foreign writer or an older build
    # could have: the record exists, and this surface still refuses to offer it.
    snapshot = bound.snapshot()
    agent_id = str(good["entity_ref"]["id"])
    malformed = snapshot.agents[agent_id].to_dict()
    malformed["extensions"] = {"model": "--dangerously-skip-permissions"}
    snapshot.agents[agent_id] = AgentRecord.from_projected_data(malformed)
    options = _context(workspace, None).model_options(snapshot)

    assert options == {"claude": [], "codex": []}


def test_the_offer_list_survives_an_unreadable_operator_config(
    workspace: dict[str, Any],
) -> None:
    """The config half is optional exactly like `profile_info` is: an operator
    file the loader refuses costs the offer list its first source and nothing
    else -- the roles this project already runs are still offered."""

    _hire(workspace, name="Builder", profile_id="claude-builder", model="claude-opus-4-6")
    missing = workspace["repo"].parent / "no-such-runtime.yaml"

    with _client(_context(workspace, missing)) as client:
        response = client.get("/api/catalog", headers=authorized())

    assert response.status_code == 200
    assert response.json()["profile_info"] == {}
    assert response.json()["model_options"] == {"claude": ["claude-opus-4-6"], "codex": []}
