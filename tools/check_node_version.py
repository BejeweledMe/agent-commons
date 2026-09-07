"""Refuse frontend checks when PATH disagrees with the repository's Node major."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


def main() -> int:
    version_file = Path(__file__).resolve().parents[1] / ".node-version"
    try:
        required = version_file.read_text(encoding="utf-8").strip()
    except OSError:
        print("Cannot read the repository .node-version file.", file=sys.stderr)
        return 1
    if re.fullmatch(r"[1-9][0-9]*", required) is None:
        print(".node-version must contain a Node major, such as 24.", file=sys.stderr)
        return 1
    try:
        result = subprocess.run(
            ["node", "--version"], capture_output=True, text=True, timeout=10, check=False
        )
    except OSError:
        print("Node is not available; install/select the major in .node-version.", file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired:
        print(
            "Node version check timed out; inspect the selected Node executable.", file=sys.stderr
        )
        return 1
    version = re.fullmatch(r"v([0-9]+)\.([0-9]+)\.([0-9]+)", result.stdout.strip())
    if result.returncode != 0 or version is None:
        print("The selected executable could not determine the Node version.", file=sys.stderr)
        return 1
    actual = ".".join(version.groups())
    if version.group(1) != required:
        print(
            f"This repository requires Node {required} from .node-version; PATH selects {actual}. "
            "Select the required Node major and rerun the same make target.",
            file=sys.stderr,
        )
        return 1
    print(f"Node {actual} matches .node-version.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
