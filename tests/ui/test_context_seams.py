"""Structural guard for the A4 UI composition split."""

from agent_commons.ui.actions import UIActions
from agent_commons.ui.context import UIContext
from agent_commons.ui.reads import UIReads


def test_context_keeps_read_and_write_workflows_in_dedicated_modules() -> None:
    """The compatibility facade must not regain panel workflows during new work."""

    assert issubclass(UIContext, UIReads)
    assert issubclass(UIContext, UIActions)
    assert UIContext.catalog is UIReads.catalog
    assert UIContext.attention is UIReads.attention
    assert UIContext.create_agent is UIActions.create_agent
    assert UIContext.run_role_on_task is UIActions.run_role_on_task
