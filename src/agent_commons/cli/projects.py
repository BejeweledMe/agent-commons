"""Explicit operator recovery for the private, cross-project panel registry."""

from pathlib import Path

import click

from agent_commons.errors import ValidationError
from agent_commons.ui.project_registry import ProjectRegistry

from ._shared import CLIState


@click.group("project")
def project_group() -> None:
    """Inspect panel projects and explicitly recover operational-state bindings."""


@project_group.command("list")
@click.pass_obj
def list_projects(state: CLIState) -> None:
    """List project IDs, revisions and availability without private paths."""
    state.emit(ProjectRegistry().list_projects(read_only=True))


@project_group.command("recover-state")
@click.argument("project_id")
@click.argument("expected_revision")
@click.option(
    "--state-root",
    "replacement",
    type=click.Path(path_type=Path),
    required=True,
    help="Existing, owned exact state root; never a state base or a new directory.",
)
@click.option(
    "--verify-existing",
    is_flag=True,
    help="Revalidate filesystem identity at the exact registered existing state root.",
)
@click.option("--idempotency-key", required=True)
@click.pass_obj
def recover_state(
    state: CLIState,
    project_id: str,
    expected_revision: str,
    replacement: Path,
    idempotency_key: str,
    verify_existing: bool,
) -> None:
    """Recover a missing root, or verify its existing registered filesystem identity.

    Stop panels using this project before recovery, then restart the common
    panel. --verify-existing requires the exact existing registered path. This
    changes the private registry only; it does not restore or rewrite state data.
    """
    if state.read_only:
        raise ValidationError("Project recovery is unavailable in read-only mode.")
    state.emit(
        ProjectRegistry().recover_state(
            project_id,
            expected_revision,
            state_root=replacement,
            idempotency_key=idempotency_key,
            verify_existing=verify_existing,
        )
    )
