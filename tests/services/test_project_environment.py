from __future__ import annotations

import json
import sys

from agent_commons.services.project_environment import _version_probe, read_project_environment


def test_manifest_presence_is_advisory_not_build_verification(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"postinstall": "must never run"}})
    )
    (tmp_path / "package-lock.json").write_text("{}")
    (tmp_path / ".node-version").write_text("22.23.0")
    (tmp_path / "node_modules").mkdir()
    result = read_project_environment(tmp_path, version_probe=lambda name, root: "22.23.0")
    assert result["state"] == "observed"
    assert result["build_verified"] is False
    assert result["dependencies"] == [{"ecosystem": "node", "state": "present"}]
    assert result["tools"][0]["version_matches"] is True
    assert "postinstall" not in json.dumps(result)
    (tmp_path / "node_modules").rmdir()
    result = read_project_environment(tmp_path, version_probe=lambda name, root: "20.0.0")
    assert result["state"] == "attention"
    assert result["tools"][0]["version_matches"] is False
    assert result["dependencies"][0]["state"] == "missing"


def test_manifest_symlinks_oversize_and_invalid_values_are_not_read_through(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "secret.json"
    outside.write_text('{"token":"do-not-expose"}')
    (repo / "package.json").symlink_to(outside)
    (repo / "pyproject.toml").write_bytes(b"x" * (256 * 1024 + 1))
    (repo / "requirements.txt").write_text("synthetic")
    (repo / ".python-version").write_text("token=do-not-expose")
    result = read_project_environment(repo, version_probe=lambda name, root: None)
    assert result["manifests"][:2] == [
        {"name": "package.json", "state": "unavailable"},
        {"name": "pyproject.toml", "state": "unavailable"},
    ]
    assert "do-not-expose" not in json.dumps(result)
    (repo / "package.json").unlink()
    (repo / "package.json").write_text("not valid json")
    assert (
        read_project_environment(repo, version_probe=lambda name, root: None)["manifests"][0][
            "state"
        ]
        == "invalid"
    )


def test_unknown_project_does_not_probe_tools(tmp_path):
    def forbidden(*args):
        raise AssertionError("unrelated tool probe")

    result = read_project_environment(tmp_path, version_probe=forbidden)
    assert result["state"] == "unknown"
    assert result["tools"] == []


def test_fixed_tool_probe_discards_environment_hooks_and_all_nonversion_output(
    tmp_path, monkeypatch
):
    import agent_commons.services.project_environment as environment

    repo = tmp_path / "repo"
    repo.mkdir()
    tool = tmp_path / "node"
    tool.write_text(
        f"#!{sys.executable}\n"
        "import os,sys\nassert sys.argv[1:] == ['--version']\n"
        "assert not (set(os.environ) & {'NODE_OPTIONS','PYTHONPATH','API_KEY'})\n"
        "print('v22.23.0')\n"
    )
    tool.chmod(0o700)
    monkeypatch.setattr(
        environment, "resolve_trusted_executable", lambda *args, **kwargs: str(tool)
    )
    for name in ("NODE_OPTIONS", "PYTHONPATH", "API_KEY"):
        monkeypatch.setenv(name, "private")
    assert _version_probe("node", repo) == "22.23.0"
    tool.write_text(f"#!{sys.executable}\nprint('token=private')\n")
    assert _version_probe("node", repo) is None
    tool.write_text(
        f"#!{sys.executable}\nimport time\nprint('x'*1000, flush=True)\ntime.sleep(30)\n"
    )
    assert _version_probe("node", repo) is None


def test_workspace_executable_is_never_a_version_probe(tmp_path, monkeypatch):
    tool = tmp_path / "node"
    tool.write_text("#!/bin/sh\ntouch ran\n")
    tool.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert _version_probe("node", tmp_path) is None
    assert not (tmp_path / "ran").exists()
