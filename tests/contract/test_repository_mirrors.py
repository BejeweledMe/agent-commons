"""Repository-level contracts decided on 2026-09-18.

- ``repo/skills-mirror``: ``.agents/skills`` and ``.claude/skills`` stay a
  byte-identical dual-client mirror.
- The ADR index in ``docs/adr/README.md`` may not contradict the status a
  numbered ADR declares about itself.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
ADR_DIR = ROOT / "docs" / "adr"

# Keywords that mean "this decision is in force / built"; the index row of an
# ADR whose own status line carries one of them must carry one as well.
SETTLED = ("accepted", "implemented", "merged")
WITHDRAWN = ("withdrawn", "withdrawal", "rejected")


def _tree(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_agent_skill_mirrors_are_byte_identical() -> None:
    agents = _tree(ROOT / ".agents" / "skills")
    claude = _tree(ROOT / ".claude" / "skills")

    assert agents, ".agents/skills is empty"
    assert agents.keys() == claude.keys(), sorted(agents.keys() ^ claude.keys())
    drifted = [name for name in agents if agents[name] != claude[name]]
    assert drifted == []


def _declared_status(adr: Path) -> str:
    for line in adr.read_text(encoding="utf-8").splitlines()[:40]:
        lowered = line.lower()
        if "status" in lowered or "статус" in lowered:
            return lowered
    raise AssertionError(f"{adr.name} declares no status line")


def _index_rows() -> dict[str, str]:
    index = (ADR_DIR / "README.md").read_text(encoding="utf-8")
    rows = re.findall(r"^\| \[(\d{4})\]\([^)]+\) \| ([^|]+) \|", index, flags=re.M)
    return {number: status.strip().lower() for number, status in rows}


def test_adr_index_status_agrees_with_each_adr() -> None:
    rows = _index_rows()
    for adr in sorted(ADR_DIR.glob("[0-9][0-9][0-9][0-9]-*.md")):
        number = adr.name[:4]
        assert number in rows, f"{adr.name} missing from the index table"
        declared = _declared_status(adr)
        indexed = rows[number]
        if any(word in declared for word in WITHDRAWN):
            assert any(word in indexed for word in WITHDRAWN), adr.name
        elif any(word in declared for word in SETTLED) or "принято" in declared:
            assert any(word in indexed for word in SETTLED), adr.name
