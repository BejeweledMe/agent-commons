"""Which model a role runs on, chosen once, at the one moment it can be.

Three properties, and none of them follows from the code reading right:

- the choice is recorded where the event schema already has room for it, so no
  schema moves and no recorded event changes shape;
- an unsafe name is refused on the form, not from inside a launch an hour
  later -- and specifically a name that would arrive at a provider as a flag;
- a role that named no model keeps running whatever its profile names, which
  is what an untouched field has to mean.

Delivery -- that the chosen name actually reaches the provider's argv rather
than only the ledger -- is pinned in `tests/runtime/test_orchestration.py`,
beside the launch path it travels through, and asserted from the argv a real
broker run hands the runner.
"""

from __future__ import annotations

from typing import Any

import pytest

from agent_commons.errors import ValidationError
from agent_commons.services import CommonsManager
from agent_commons.services.roles import role_model
from agent_commons.ui.context import MODEL_NAME_REFUSED, UIContext
from tests.ui.conftest import authorized


def _body(**overrides: Any) -> dict[str, Any]:
    return {
        "name": "Backend",
        "profile_id": "claude-builder",
        "rationale": "the backend surface needs a standing owner",
        **overrides,
    }


def _record(writable: UIContext, agent_id: str) -> dict[str, Any]:
    return writable.manager().snapshot().agents[agent_id]


def test_a_chosen_model_is_recorded_under_the_payload_extensions(
    writable_client,  # type: ignore[no-untyped-def]
    writable: UIContext,
) -> None:
    """`payload.extensions` is a free-form object the agent payload schema
    already carries, so a model lands in a canonical event without the schema
    moving and without any already-written event changing shape.  A model is
    operator preference layered on a profile, not a fifth thing the domain
    reasons about: nothing branches on it and no state depends on it."""

    response = writable_client.post(
        "/api/agents", json=_body(model="claude-opus-4-6"), headers=authorized()
    )
    assert response.status_code == 200, response.text
    agent_id = response.json()["entity_ref"]["id"]

    record = _record(writable, agent_id)
    assert record["extensions"] == {"model": "claude-opus-4-6"}
    assert role_model(record) == "claude-opus-4-6"
    # And it is in the event itself, not only in the projection: a role hired
    # today has to still name its model after a replay tomorrow.
    hired = [
        item.event
        for item in writable.manager().events.iter_events()
        if item.event.get("event_type") == "agent.created"
    ]
    assert [event["payload"]["extensions"]["model"] for event in hired] == ["claude-opus-4-6"]


@pytest.mark.parametrize("absent", [None, "", "   "])
def test_an_untouched_field_leaves_the_profile_s_model_standing(
    writable_client,  # type: ignore[no-untyped-def]
    writable: UIContext,
    absent: str | None,
) -> None:
    """The field is optional and its placeholder says so.  Blank must therefore
    mean "you choose" and never "run a role named ''" -- so it normalizes to
    absent rather than being refused, and the record carries no model at all
    rather than an empty one for a later reader to interpret."""

    response = writable_client.post(
        "/api/agents", json=_body(name=f"Blank {absent!r}", model=absent), headers=authorized()
    )
    assert response.status_code == 200, response.text
    record = _record(writable, response.json()["entity_ref"]["id"])
    assert "extensions" not in record
    assert role_model(record) is None


@pytest.mark.parametrize(
    "hostile",
    [
        "--dangerously-skip-permissions",
        "-m",
        "model with spaces",
        "model\nmodel",
        ";rm -rf /",
        "$(id)",
        "x" * 300,
    ],
)
def test_an_unsafe_model_name_is_refused_at_the_form(
    writable_client,  # type: ignore[no-untyped-def]
    workspace: dict[str, Any],
    hostile: str,
) -> None:
    """The choice becomes an element of a provider's argv, so the identifier
    rule is the security boundary: a name must start alphanumeric, which is
    exactly what stops a leading `-` from arriving as a flag.

    The rule already lived in the runner profiles, and the launch would have
    refused all of these -- but from inside a background launch, where the
    operator has neither the field nor the context to fix it.  The refusal is
    pulled forward to the request that carries the value, and nothing is
    recorded on the way: a hostile name that reached the ledger would be
    replayed into argv on every launch of that role afterwards.
    """

    response = writable_client.post("/api/agents", json=_body(model=hostile), headers=authorized())
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == ValidationError.__name__
    # And the message is one a person at the form can act on: it names what a
    # model name may contain and says that empty is a legitimate answer. The
    # inner refusal ("model is not a safe identifier") is right for a log and a
    # dead end on a form, so it is rewritten rather than passed through.
    assert response.json()["error"]["message"] == MODEL_NAME_REFUSED

    manager = CommonsManager(workspace["repo"], state_root=workspace["state_root"])
    assert not manager.snapshot().agents


def test_the_last_gate_before_the_ledger_refuses_it_too(
    workspace: dict[str, Any],
) -> None:
    """Not only the panel.  `create_agent` is reachable from the CLI and the
    MCP adapter as well, and it is the last point before the value becomes an
    immutable canonical event -- so it validates on its own account rather than
    trusting whichever adapter called it."""

    manager = CommonsManager(workspace["repo"], state_root=workspace["state_root"])
    session = manager.start_session(
        stable_instance_id="model-gate-window",
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
    with pytest.raises(ValidationError, match="model"):
        bound.create_agent(
            name="Backend",
            profile_id="claude-builder",
            rationale="a name no launch would accept",
            model="--model-of-my-own",
        )
    assert not bound.snapshot().agents


def test_hiring_from_a_preset_carries_the_preset_s_model(
    writable_client,  # type: ignore[no-untyped-def]
    writable: UIContext,
) -> None:
    """A preset is a saved role shape and the model is part of the shape it
    saved.  Hiring from one and quietly getting the profile's model instead
    would be the template not applying -- and the hire form hides the picker on
    that path, so nothing on screen would have said so."""

    preset = writable_client.post(
        "/api/agents",
        json=_body(name="Reviewer preset", template=True, model="claude-opus-4-6"),
        headers=authorized(),
    )
    assert preset.status_code == 200, preset.text

    hired = writable_client.post(
        "/api/agents",
        json={
            "name": "Reviewer",
            "rationale": "staffed from the saved shape",
            "from_preset_id": preset.json()["entity_ref"]["id"],
        },
        headers=authorized(),
    )
    assert hired.status_code == 200, hired.text
    assert role_model(_record(writable, hired.json()["entity_ref"]["id"])) == "claude-opus-4-6"


def test_an_explicit_choice_beats_the_preset_s(
    writable_client,  # type: ignore[no-untyped-def]
    writable: UIContext,
) -> None:
    """Inheritance is a default, not an override: the preset fills the field in
    only when this hire left it empty, exactly like every other preset field."""

    preset = writable_client.post(
        "/api/agents",
        json=_body(name="Builder preset", template=True, model="claude-opus-4-6"),
        headers=authorized(),
    )
    hired = writable_client.post(
        "/api/agents",
        json={
            "name": "Builder",
            "rationale": "same shape, different model",
            "from_preset_id": preset.json()["entity_ref"]["id"],
            "model": "claude-sonnet-4-5",
        },
        headers=authorized(),
    )
    assert hired.status_code == 200, hired.text
    assert role_model(_record(writable, hired.json()["entity_ref"]["id"])) == "claude-sonnet-4-5"


def test_reconfiguring_a_role_cannot_change_its_model(
    writable_client,  # type: ignore[no-untyped-def]
    writable: UIContext,
) -> None:
    """The decision, pinned as a decision rather than as a missing feature.

    A role's accumulated context is built for the model that built it, so
    moving a hired role to another model would need that context recomputed.
    `reconfigure_agent` takes a closed set of changes and `model` is not in it;
    a request that names one is refused, and the role keeps what it was hired
    with.
    """

    hired = writable_client.post(
        "/api/agents", json=_body(model="claude-opus-4-6"), headers=authorized()
    )
    agent_id = hired.json()["entity_ref"]["id"]
    response = writable_client.post(
        f"/api/agents/{agent_id}/reconfigure",
        json={
            "expected_revision": hired.json()["revision"],
            "changes": {"model": "claude-sonnet-4-5"},
            "reason": "trying to move a hired role to another model",
        },
        headers=authorized(),
    )
    assert response.status_code == 409, response.text
    assert role_model(_record(writable, agent_id)) == "claude-opus-4-6"


def test_a_replayed_role_record_is_never_trusted_to_name_a_model(
    writable: UIContext,
) -> None:
    """`role_model` reads a file this process may not have written.  Anything
    that is not a plain non-empty string reads as absent, so a malformed or
    hostile extensions object costs the role its model rather than putting an
    unexamined value on the launch path."""

    for record in (
        None,
        {},
        {"extensions": None},
        {"extensions": "claude-opus-4-6"},
        {"extensions": {"model": None}},
        {"extensions": {"model": ""}},
        {"extensions": {"model": ["claude-opus-4-6"]}},
        {"extensions": {"models": "claude-opus-4-6"}},
    ):
        assert role_model(record) is None
