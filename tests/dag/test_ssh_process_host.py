"""Tests for SSH process host argument builders and wrapper script."""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
from pathlib import Path

from ghdag.dag._nodes import NodeSpec
from ghdag.dag._process_host import (
    build_remote_signal_argv,
    build_ssh_argv,
    build_wrapper_script,
    split_stdin_redirect,
)


def test_split_stdin_redirect() -> None:
    """Refs sumipan/ghdag#7651 — stdin redirect is split without heredocs."""
    assert split_stdin_redirect("cat < /a/b.md") == ("cat ", "/a/b.md")
    assert split_stdin_redirect("echo hi") == ("echo hi", None)
    assert split_stdin_redirect("cat <<EOF") == ("cat <<EOF", None)


def test_build_ssh_argv() -> None:
    """Refs sumipan/ghdag#7651 — ssh argv has batch mode and quoted remote command."""
    node = NodeSpec("n1", ("linux",), "build-1", "/srv/ghdag", max_parallel=1)
    argv = build_ssh_argv(node, "task-uuid", "echo $HOME; exit 0", None)
    assert argv[0] == "ssh"
    assert argv[1:4] == ["-o", "BatchMode=yes", "build-1"]
    assert len(argv) == 5
    remote = argv[4]
    assert "bash" in remote and "ghdag-wrap" in remote
    assert shlex.split(remote)[-1] == "echo $HOME; exit 0"


def test_build_remote_signal_argv() -> None:
    """Refs sumipan/ghdag#7651 — remote signal argv references pgid file and TERM."""
    node = NodeSpec("n1", ("linux",), "host", "/srv/w", max_parallel=1)
    argv = build_remote_signal_argv(node, "u-1", signal.SIGTERM)
    assert "ConnectTimeout=5" in argv
    remote = argv[-1]
    assert "/srv/w/.ghdag/u-1.pgid" in remote
    assert "kill -TERM" in remote


def _invoke_wrapper(
    tmp_path: Path,
    command: str,
    *,
    stdin: bytes = b"",
    previous_attempt: str = "",
) -> subprocess.CompletedProcess[bytes]:
    wrapper = build_wrapper_script()
    uuid = "wrap-test"
    argv = [
        "/bin/bash",
        "-c",
        wrapper,
        "ghdag-wrap",
        str(tmp_path),
        uuid,
        previous_attempt,
        command,
    ]
    return subprocess.run(
        argv,
        input=stdin,
        capture_output=True,
        check=False,
    )


def test_wrapper_passes_stdin_stdout_and_exit_code(tmp_path: Path) -> None:
    """Refs sumipan/ghdag#7651 — wrapper forwards stdin, stdout, and exit codes."""
    proc0 = _invoke_wrapper(tmp_path, "cat; exit 0", stdin=b"payload\n")
    assert proc0.returncode == 0
    assert proc0.stdout == b"payload\n"
    assert proc0.stderr == b""

    proc5 = _invoke_wrapper(tmp_path, "exit 5")
    assert proc5.returncode == 5
    assert proc5.stderr == b""


def test_wrapper_pgid_file_lifecycle_and_term_stops_children(tmp_path: Path) -> None:
    """Refs sumipan/ghdag#7651 — pgid file exists while running; TERM stops children."""
    pgid_path = tmp_path / ".ghdag" / "wrap-test.pgid"
    command = (
        "echo ready; "
        "sleep 300 & sleep 301; "
        "wait"
    )
    proc = subprocess.Popen(
        [
            "/bin/bash",
            "-c",
            build_wrapper_script(),
            "ghdag-wrap",
            str(tmp_path),
            "wrap-test",
            "",
            command,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert proc.stdout is not None
        line = proc.stdout.readline()
        assert line.strip() == b"ready"
        assert pgid_path.is_file()
        pgid = int(pgid_path.read_text(encoding="utf-8").strip())
        os.killpg(pgid, signal.SIGTERM)
        rc = proc.wait()
        assert rc == 143
        assert not pgid_path.exists()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
