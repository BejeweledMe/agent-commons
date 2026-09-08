"""The fixed Grok pipe must preserve instructions without argv or disk copies."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

from agent_commons.errors import ConfigurationError
from agent_commons.runtime import (
    BuiltinProfileId,
    RunOutcome,
    RunReason,
    SafeEnvironment,
    SubprocessRunner,
)
from agent_commons.runtime.exec_gate import gated_argv
from agent_commons.runtime.model import invocation_instruction_bytes
from tests.runtime.test_launch_plan import _profile


def _invocation(tmp_path: Path):
    return _profile(BuiltinProfileId.GROK_BUILDER).build_invocation(
        "SYNTHETIC_GROK_PIPE_PAYLOAD\nUnicode: 界🙂\n",
        workspace_root=tmp_path,
        delegation_id="delegation.synthetic-grok-stdin",
    )


def test_grok_instruction_is_absent_from_provider_and_inert_gate_argv(tmp_path: Path) -> None:
    invocation = _invocation(tmp_path)
    assert invocation_instruction_bytes(invocation) == invocation.stdin
    assert invocation.argv[-2:] == ("--prompt-file", "/dev/stdin")
    marker = "SYNTHETIC_GROK_PIPE_PAYLOAD"
    assert all(marker not in argument for argument in invocation.argv)
    assert all(marker not in argument for argument in gated_argv(invocation.argv))


@pytest.mark.parametrize(
    "extra",
    [
        ("-p", "alternate"),
        ("-palternate",),
        ("--single", "alternate"),
        ("--single=alternate",),
        ("--prompt-json", "[]"),
        ("--prompt-json=[]",),
        ("--prompt-file", "/dev/stdin"),
        ("--prompt-file=/dev/stdin",),
    ],
)
def test_grok_instruction_extraction_rejects_alternate_transports(
    tmp_path: Path, extra: tuple[str, ...]
) -> None:
    invocation = _invocation(tmp_path)
    changed = replace(invocation, argv=(*invocation.argv[:-2], *extra, *invocation.argv[-2:]))
    with pytest.raises(ConfigurationError, match="invalid fixed stdin transport"):
        invocation_instruction_bytes(changed)


@pytest.mark.parametrize("path", ["-", "/tmp/prompt.txt", "/dev/fd/0"])
def test_grok_instruction_extraction_never_accepts_another_file(tmp_path: Path, path: str) -> None:
    invocation = _invocation(tmp_path)
    changed = replace(invocation, argv=(*invocation.argv[:-1], path))
    with pytest.raises(ConfigurationError, match="invalid fixed stdin transport"):
        invocation_instruction_bytes(changed)


def test_grok_instruction_extraction_requires_a_single_complete_pipe_transport(
    tmp_path: Path,
) -> None:
    invocation = _invocation(tmp_path)
    for changed in (
        replace(invocation, argv=invocation.argv[:-2]),
        replace(invocation, argv=invocation.argv[:-1]),
        replace(invocation, argv=(*invocation.argv, "positional prompt")),
        replace(invocation, stdin=b""),
    ):
        with pytest.raises(ConfigurationError, match="invalid fixed stdin transport"):
            invocation_instruction_bytes(changed)


@pytest.mark.skipif(sys.platform not in {"darwin", "linux"}, reason="POSIX Grok pipe transport")
@pytest.mark.parametrize("start_rejected", [False, True])
def test_real_grok_shaped_file_reader_obeys_exec_gate_without_prompt_files(
    tmp_path: Path, start_rejected: bool
) -> None:
    invocation = _invocation(tmp_path)
    marker = tmp_path / "provider-started"
    # This process emulates only explicit file reading, never a model/provider.
    # Use the same --prompt-file operand as the real Grok profile.
    reader = (
        "import pathlib,sys;"
        "pathlib.Path(sys.argv[1]).touch();"
        "sys.stdout.buffer.write(pathlib.Path(sys.argv[3]).read_bytes())"
    )
    probe = replace(
        invocation,
        argv=(sys.executable, "-I", "-c", reader, str(marker), *invocation.argv[-2:]),
    )

    def on_started(_pid: int) -> None:
        assert not marker.exists()
        if start_rejected:
            raise RuntimeError("synthetic canonical start rejection")

    result = SubprocessRunner(environment=SafeEnvironment.from_mapping({})).run(
        probe,
        cwd=tmp_path,
        child_session_id="session.synthetic-grok-stdin",
        timeout_seconds=10,
        max_output_bytes=1024,
        on_started=on_started,
    )
    if start_rejected:
        assert result.outcome is RunOutcome.FAILED
        assert result.reason is RunReason.CONTROL_ERROR
        assert result.stdout == b""
        assert not marker.exists()
        assert list(tmp_path.iterdir()) == []
    else:
        assert result.outcome is RunOutcome.SUCCEEDED
        assert result.stdout == invocation.stdin
        assert result.stderr == b""
        assert set(tmp_path.iterdir()) == {marker}
