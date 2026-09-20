"""Backsliding contracts for the decision and implementation documentation map."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
DOCS = ROOT / "docs"
ACTIVE_PROGRAMME = "plans/2026-09-18-consolidation-and-wave-4-plan.md"
ROUTED_DOCUMENTS = (
    DOCS / "README.md",
    DOCS / "adr" / "README.md",
    DOCS / ACTIVE_PROGRAMME,
)
ARCHIVED_PLANS = DOCS / "archive" / "plans"
ARCHIVE_STATUSES = ("Status: completed", "Status: superseded")


def test_adr_index_covers_every_numbered_record() -> None:
    index = (DOCS / "adr" / "README.md").read_text(encoding="utf-8")
    numbered_adrs = sorted((DOCS / "adr").glob("[0-9][0-9][0-9][0-9]-*.md"))

    assert numbered_adrs
    for adr in numbered_adrs:
        assert f"]({adr.name})" in index

    normalized = " ".join(index.split())
    assert "uv run agent-commons decision list" in normalized
    assert "not a second decision registry" in normalized


def _section(text: str, heading: str) -> str:
    marker = f"## {heading}\n"
    assert marker in text
    body = text.split(marker, 1)[1]
    return body.split("\n## ", 1)[0]


def test_documentation_map_routes_to_the_active_programme() -> None:
    for document in ROUTED_DOCUMENTS:
        assert document.exists(), document

    documentation_map = (DOCS / "README.md").read_text(encoding="utf-8")
    assert ACTIVE_PROGRAMME in documentation_map
    assert "adr/README.md" in documentation_map


def test_archived_plans_declare_a_status_stamp() -> None:
    archived = sorted(ARCHIVED_PLANS.glob("*.md"))
    assert archived, ARCHIVED_PLANS

    unstamped: list[str] = []
    for plan in archived:
        lines = plan.read_text(encoding="utf-8").splitlines()
        stamped = (
            len(lines) > 2
            and lines[0].strip() == "```"
            and lines[1].startswith(ARCHIVE_STATUSES)
            and "```" in lines[2:9]
        )
        if not stamped:
            unstamped.append(str(plan.relative_to(ROOT)))

    assert not unstamped, "archived plans without a status stamp:\n" + "\n".join(unstamped)


def test_documentation_map_separates_history_from_current_direction() -> None:
    documentation_map = (DOCS / "README.md").read_text(encoding="utf-8")
    historical = _section(documentation_map, "Historical plans")
    current = _section(documentation_map, "Current direction summaries")

    assert "docs/archive/plans/" in historical

    top_level = {document.name for document in DOCS.glob("*.md") if document.name != "README.md"}
    leaked = sorted(name for name in top_level if name in historical)
    assert not leaked, "historical section points at current top-level documents: " + ", ".join(
        leaked
    )

    assert "PRODUCT.md" in current


def _markdown_documents() -> list[Path]:
    documents = sorted(DOCS.rglob("*.md"))
    documents.extend(sorted(ROOT.glob("*.md")))
    return documents


def test_documentation_links_resolve_inside_the_repository() -> None:
    link_pattern = re.compile(r"\[[^\]]+\]\(([^)\s]+)(?:\s+[^)]*)?\)")

    missing: list[str] = []
    for document in _markdown_documents():
        text = document.read_text(encoding="utf-8")
        for match in link_pattern.finditer(text):
            raw_target = match.group(1).strip("<>")
            target = raw_target.split("#", 1)[0]
            if not target or target.startswith(("https://", "http://", "mailto:")):
                continue
            resolved = (document.parent / target).resolve()
            if not resolved.exists():
                line = text.count("\n", 0, match.start()) + 1
                missing.append(f"{document.relative_to(ROOT)}:{line}: {raw_target}")

    assert not missing, "unresolved documentation links:\n" + "\n".join(missing)
