"""Preview or remove only this checkout's disposable root build/cache directories."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

TARGETS = ("build", "dist", ".pytest_cache", ".ruff_cache")


def git(root: Path, *args: str) -> str:
    # An inherited alternate index/worktree must not hide tracked content.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("Cannot verify the checkout with Git; nothing removed.") from error
    if result.returncode:
        raise ValueError("Cannot verify the checkout with Git; nothing removed.")
    return result.stdout


def plan_cleanup(root: Path) -> list[Path]:
    """Validate every candidate before returning any deletion targets."""
    root = root.resolve()
    if Path(git(root, "rev-parse", "--show-toplevel").rstrip("\n")).resolve() != root:
        raise ValueError("Cleanup must run from the repository root; nothing removed.")
    planned = []
    for name in TARGETS:
        target = root / name
        if target.is_symlink():
            raise ValueError(f"Refusing symlink root {name}; nothing removed.")
        if not target.exists():
            continue
        if not target.is_dir():
            raise ValueError(f"Refusing non-directory root {name}; nothing removed.")
        # Match case-insensitively even on Linux: macOS may resolve Build as build.
        if git(root, "ls-files", "--cached", "-z", "--", f":(icase){name}"):
            raise ValueError(f"Refusing tracked content in {name}; nothing removed.")
        planned.append(target)
    return planned


def clean(root: Path, *, apply: bool = False) -> None:
    planned = plan_cleanup(root)
    if not planned:
        print("No disposable root build/cache directories found.")
    for target in planned:
        if apply:
            shutil.rmtree(target)
        print(f"{'Removed' if apply else 'Would remove'} {target.name}/")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="Remove the previewed directory kinds."
    )
    args = parser.parse_args()
    try:
        clean(Path(__file__).resolve().parents[1], apply=args.apply)
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
