from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_runtime_examples_never_store_operator_config_inside_the_state_base() -> None:
    documents = [
        ROOT / "README.md",
        ROOT / "docs" / "QUICKSTART.md",
        ROOT / "docs" / "USER_WORKFLOWS.md",
        *sorted((ROOT / "docs" / "tutorials").glob("*.md")),
    ]
    unsafe = re.compile(r"AGENT_COMMONS_STATE_BASE[^\n]*(?:runtime|profile)[^\n]*\.ya?ml")

    offenders = [path.relative_to(ROOT) for path in documents if unsafe.search(path.read_text())]

    assert offenders == []


def test_first_delegation_uses_a_distinct_operator_directory() -> None:
    tutorial = (ROOT / "docs" / "tutorials" / "FIRST_DELEGATION.md").read_text()

    assert 'export FIRST_DELEGATION_OPERATOR_DIR="$PWD/first-delegation-operator"' in tutorial
    assert 'export RUNTIME_CONFIG="$FIRST_DELEGATION_OPERATOR_DIR/' in tutorial
    assert (
        'mkdir -p "$FIRST_DELEGATION_ROOT" "$AGENT_COMMONS_STATE_BASE" \\\n'
        '  "$FIRST_DELEGATION_OPERATOR_DIR"' in tutorial
    )
