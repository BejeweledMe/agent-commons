"""The runner must actually carry the tools the suite quietly leans on.

Several behaviour tests execute real panel functions under node and skip when
node is absent.  On a laptop that skip is a convenience; in CI it is silent
loss of coverage — the matrix would go green while the very tests that catch
formatter and vocabulary drift never ran.  GitHub sets ``CI=true`` on every
runner, so here the absence becomes a failure with a name instead.
"""

from __future__ import annotations

import os
import shutil

import pytest


def test_ci_carries_node_so_the_behaviour_harnesses_actually_run() -> None:
    if not os.environ.get("CI"):
        pytest.skip("only meaningful on a CI runner; locally the skips are honest")
    assert shutil.which("node"), (
        "CI has no node on PATH: the profileOptions and layout behaviour tests "
        "are silently skipping. ci.yml installs node from .node-version — "
        "restore that step rather than letting the coverage vanish."
    )
