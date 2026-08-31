"""Backsliding contracts for the decision and implementation documentation map."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
DOCS = ROOT / "docs"
ACTIVE_PROGRAMME = "agent-platform-implementation-program.md"
ROUTED_DOCUMENTS = (
    DOCS / "README.md",
    DOCS / "pivot-ui-only-plan.md",
    DOCS / "architecture-improvement-implementation-plan.md",
    DOCS / "visual_multi_agent_orchestrator_prd.md",
    DOCS / "visual_orchestrator_plan.md",
)
DOCUMENT_CATEGORIES = {
    "pivot-ui-only-plan.md": "historical-analysis",
    "architecture-improvement-implementation-plan.md": "historical-plan",
    "visual_multi_agent_orchestrator_prd.md": "current-product-direction",
    "visual_orchestrator_plan.md": "current-navigation",
}


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


def test_routed_documents_declare_stable_categories_and_active_programme() -> None:
    for document in ROUTED_DOCUMENTS:
        text = document.read_text(encoding="utf-8")
        assert ACTIVE_PROGRAMME in text, document

    category_pattern = re.compile(r"(?m)^\*\*Document category:\*\* `([^`]+)`$")
    for filename, expected_category in DOCUMENT_CATEGORIES.items():
        text = (DOCS / filename).read_text(encoding="utf-8")
        match = category_pattern.search(text)
        assert match is not None, filename
        assert match.group(1) == expected_category


def test_documentation_map_separates_history_from_current_direction() -> None:
    documentation_map = (DOCS / "README.md").read_text(encoding="utf-8")
    historical = _section(documentation_map, "Historical plans")
    current = _section(documentation_map, "Current direction summaries")

    assert "pivot-ui-only-plan.md" in historical
    assert "architecture-improvement-implementation-plan.md" in historical
    assert "visual_multi_agent_orchestrator_prd.md" not in historical
    assert "visual_orchestrator_plan.md" not in historical
    assert "visual_multi_agent_orchestrator_prd.md" in current
    assert "visual_orchestrator_plan.md" in current


def test_changed_document_links_resolve_inside_the_repository() -> None:
    documents = (*ROUTED_DOCUMENTS, DOCS / "adr" / "README.md")
    link_pattern = re.compile(r"\[[^\]]+\]\(([^)\s]+)(?:\s+[^)]*)?\)")

    missing: list[str] = []
    for document in documents:
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
