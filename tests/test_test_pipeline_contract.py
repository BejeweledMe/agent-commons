"""Contracts for fast feedback without weakening the full green gate."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
COMPONENT_PATHS = {
    "test-domain": (
        "tests/core",
        "tests/domain",
        "tests/services",
        "tests/storage",
        "tests/coordination",
        "tests/index",
        "tests/integrations",
    ),
    "test-runtime": ("tests/runtime", "tests/mcp"),
    "test-ui": ("tests/ui", "tests/cli"),
    "test-contracts": (
        "tests/contract",
        "tests/e2e",
        "tests/evals",
        "tests/evals_harness",
        "tests/benchmarks",
        "tests/schemas",
        "tests/security",
        "tests/test_ci_environment.py",
        "tests/test_platform_support.py",
        "tests/test_test_pipeline_contract.py",
        "tests/test_tutorial_contract.py",
    ),
}


def _recipe(makefile: str, target: str) -> str:
    match = re.search(
        rf"(?ms)^{re.escape(target)}:[^\n]*\n(?P<body>(?:(?:\t|#|\s*$).*\n)*)",
        makefile,
    )
    assert match is not None, f"missing make target {target}"
    return " ".join(match.group("body").split())


def test_component_targets_are_hermetic_and_partition_every_test_module() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "PYTEST = env -u AGENT_COMMONS_STATE_ROOT -u AGENT_COMMONS_STATE_BASE" in makefile
    assert "-u AGENT_COMMONS_SESSION_ID" in makefile
    assert (
        "check: node-version-check lint format-check frontend-work-test frontend-gallery-test test"
        in makefile
    )
    assert "frontend-work-deps: node-version-check" in makefile
    assert "frontend-gallery-deps: node-version-check" in makefile
    assert "$(UV) run --locked python tools/check_node_version.py" in makefile
    assert "test:\n\t$(PYTEST) -q" in makefile
    assert "test-contracts: frontend-work-deps" not in makefile
    assert "frontend-work-test: frontend-work-deps" in makefile
    assert "frontend-gallery-test: frontend-gallery-deps" in makefile
    assert "npm ci --ignore-scripts --prefix frontend/work" in makefile
    assert "npm ci --ignore-scripts --prefix frontend/gallery" in makefile
    assert "npm test --prefix frontend/work" in makefile
    assert "npm test --prefix frontend/gallery" in makefile

    covered: list[Path] = []
    for target, entries in COMPONENT_PATHS.items():
        recipe = _recipe(makefile, target)
        assert "$(PYTEST) -q" in recipe
        for entry in entries:
            assert entry in recipe
            path = ROOT / entry
            if path.is_dir():
                covered.extend(sorted(set(path.rglob("test_*.py")) | set(path.rglob("*_test.py"))))
            else:
                covered.append(path)

    discovered = set((ROOT / "tests").rglob("test_*.py")) | set((ROOT / "tests").rglob("*_test.py"))
    assert len(covered) == len(set(covered)), "component targets must not duplicate test modules"
    assert set(covered) == discovered, "new tests must be assigned to one fast-feedback target"


def test_ci_keeps_full_matrix_but_deduplicates_equivalent_runs() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert re.search(
        r"(?m)^on:\n  push:\n    branches:\n      - main\n  pull_request:$",
        workflow,
    )
    concurrency_group = (
        "group: ci-${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}"
    )
    assert concurrency_group in workflow
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in workflow
    assert "os: [ubuntu-latest, macos-latest]" in workflow
    assert 'python: ["3.11", "3.12", "3.13", "3.14"]' in workflow
    assert workflow.count("- run: make check") == 1
    assert "cache-dependency-path: |" in workflow
    assert "frontend/work/package-lock.json" in workflow
    assert "frontend/gallery/package-lock.json" in workflow


@pytest.mark.parametrize(
    ("required", "observed", "exit_code", "diagnostic"),
    [
        ("24", "v24.1.2", 0, "Node 24.1.2 matches .node-version"),
        ("24", "v23.11.0", 1, "requires Node 24"),
        ("24", "v25.0.0", 1, "requires Node 24"),
        ("25", "v25.2.3", 0, "Node 25.2.3 matches .node-version"),
        ("24", "unrecognized", 1, "could not determine the Node version"),
        ("invalid", "v24.1.2", 1, ".node-version must contain a Node major"),
        ("24", None, 1, "Node is not available"),
    ],
)
def test_node_version_guard_checks_the_version_file_and_selected_binary(
    tmp_path: Path,
    required: str,
    observed: str | None,
    exit_code: int,
    diagnostic: str,
) -> None:
    """Exercise the real guard with a file and executable, without installing Node."""

    tools = tmp_path / "tools"
    tools.mkdir()
    guard = tools / "check_node_version.py"
    shutil.copyfile(ROOT / "tools" / guard.name, guard)
    (tmp_path / ".node-version").write_text(required + "\n", encoding="utf-8")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    if observed is not None:
        node = binaries / "node"
        node.write_text(
            "#!/bin/sh\nprintf '%s\\n' " + shlex.quote(observed) + "\n", encoding="utf-8"
        )
        node.chmod(0o700)
    result = subprocess.run(
        [sys.executable, str(guard)],
        cwd=tmp_path,
        env={**os.environ, "PATH": str(binaries)},
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == exit_code
    assert diagnostic in result.stdout + result.stderr
