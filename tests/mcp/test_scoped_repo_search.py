"""Search absence requires complete safe-source coverage, including minified files."""

import hashlib
import subprocess
from types import SimpleNamespace

import pytest

from agent_commons.errors import LifecycleConflictError
from agent_commons.mcp.scoped_repo import ScopedRepoReader
from agent_commons.security import SecurityPolicy


def reader(tmp_path, files):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(("/usr/bin/git", "init", "-q", str(repo)), check=True, capture_output=True)
    for name, body in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body if isinstance(body, bytes) else body.encode())
    manager = SimpleNamespace(repo_root=repo, policy=SecurityPolicy())
    return ScopedRepoReader(manager)


def test_redacted_minified_bundle_is_incomplete_not_absent_and_not_an_oracle(tmp_path):
    source = 'const token=CancellationToken();const feature="conversation-dialog";\nvisible();\n'
    scoped = reader(tmp_path, {"bundle.js": source})
    read = scoped.read("bundle.js")
    assert read["content_complete"] is False
    assert read["redactions"][0]["line"] == 1
    assert "conversation-dialog" not in read["content"]
    hidden = scoped.search("conversation-dialog", prefix="bundle.js")
    absent = scoped.search("definitely-not-present", prefix="bundle.js")
    assert hidden == absent
    assert hidden["matches"] == []
    assert hidden["coverage"]["complete"] is False
    assert hidden["coverage"]["redacted_files_total"] == 1
    assert hidden["coverage"]["redacted_files"] == [{"path": "bundle.js", "line_count": 1}]
    assert scoped.search("redacted source line", prefix="bundle.js")["matches"] == []
    visible = scoped.search("visible", prefix="bundle.js")
    assert len(visible["matches"]) == 1
    assert visible["coverage"]["complete"] is False
    registered = scoped.read_registered_artifact(
        source_path="bundle.js",
        expected_revision="sha256:" + hashlib.sha256(source.encode()).hexdigest(),
        expected_size=len(source.encode()),
    )
    assert registered["content_complete"] is False
    assert registered["content"] == read["content"]


def test_safe_match_late_in_minified_line_is_inside_bounded_snippet(tmp_path):
    scoped = reader(tmp_path, {"bundle.js": "x;" * 5_000 + "conversation-dialog" + "y;" * 1_000})
    response = scoped.search("conversation-dialog", prefix="bundle.js")
    match = response["matches"][0]
    assert response["coverage"]["complete"] is True
    assert "conversation-dialog" in match["text"]
    assert len(match["text"]) <= 1_000
    assert match["column"] == 10_001
    assert match["text_start_column"] == 9_801
    assert match["text_truncated"] is True
    assert match["text"][match["column"] - match["text_start_column"] :].startswith(
        "conversation-dialog"
    )


def test_columns_count_characters_and_short_line_remains_complete(tmp_path):
    scoped = reader(tmp_path, {"bundle.js": "начало marker end\n"})
    response = scoped.search("marker")
    assert response["matches"] == [
        {
            "path": "bundle.js",
            "line": 1,
            "column": 8,
            "text": "начало marker end",
            "text_start_column": 1,
            "text_truncated": False,
        }
    ]
    assert scoped.read("bundle.js")["content_complete"] is True


def test_file_after_500_is_unscanned_until_prefix_is_narrowed(tmp_path):
    files = {f"pkg/{index:03d}.js": "safe text\n" for index in range(501)}
    files["pkg/500.js"] = "late-feature\n"
    scoped = reader(tmp_path, files)
    broad = scoped.search("late-feature", prefix="pkg/")
    assert broad["matches"] == []
    assert broad["coverage"]["complete"] is False
    assert broad["coverage"]["file_limit_reached"] is True
    assert broad["coverage"]["files_matching_prefix"] == 501
    assert broad["coverage"]["files_scanned"] == 500
    narrow = scoped.search("late-feature", prefix="pkg/500.js")
    assert len(narrow["matches"]) == 1
    assert narrow["coverage"]["complete"] is True
    assert narrow["coverage"]["file_limit_reached"] is False


def test_binary_file_is_explicitly_unreadable_not_a_complete_negative(tmp_path):
    scoped = reader(tmp_path, {"image.bin": b"\xff\x00\xfe"})
    result = scoped.search("marker")
    assert result["matches"] == []
    assert result["coverage"]["complete"] is False
    assert result["coverage"]["unreadable_files_total"] == 1
    assert result["coverage"]["unreadable_files"] == [{"path": "image.bin"}]


def test_match_limit_is_explicit_and_does_not_claim_exhaustive_coverage(tmp_path):
    scoped = reader(tmp_path, {"source.txt": "marker one\nmarker two\n"})
    limited = scoped.search("marker", max_matches=1)
    assert len(limited["matches"]) == 1
    assert limited["coverage"]["complete"] is False
    assert limited["coverage"]["match_limit_reached"] is True
    complete = scoped.search("marker", max_matches=2)
    assert len(complete["matches"]) == 2
    assert complete["coverage"]["match_limit_reached"] is True  # Conservative at exact boundary.


def test_complete_negative_with_safe_text_is_explicit(tmp_path):
    scoped = reader(tmp_path, {"source.txt": "visible source\n"})
    result = scoped.search("absent")
    assert result["schema"] == "agent_commons.repo_search.v2"
    assert result["matches"] == []
    assert result["coverage"]["complete"] is True
    assert result["coverage"]["files_scanned"] == 1
    empty = scoped.search("absent", prefix="not-present/")
    assert empty["coverage"]["complete"] is True
    assert empty["coverage"]["files_matching_prefix"] == 0


def test_diagnostic_lists_are_bounded_without_hiding_totals(tmp_path):
    files = {f"redacted/{index}.js": "token=CancellationToken();\n" for index in range(21)}
    files.update({f"binary/{index}.bin": b"\xff" for index in range(21)})
    result = reader(tmp_path, files).search("missing")
    coverage = result["coverage"]
    assert coverage["redacted_files_total"] == coverage["unreadable_files_total"] == 21
    assert len(coverage["redacted_files"]) == len(coverage["unreadable_files"]) == 20
    assert coverage["diagnostics_truncated"] is True
    assert coverage["complete"] is False


def test_mutated_snapshot_still_fails_closed(tmp_path):
    scoped = reader(tmp_path, {"source.txt": "original\n"})
    (scoped.root / "source.txt").write_text("changed\n")
    with pytest.raises(LifecycleConflictError):
        scoped.search("absent")
