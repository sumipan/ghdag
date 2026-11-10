"""CLI entry for ghdag mcp (#6297)."""

from __future__ import annotations

import io
import json
from unittest.mock import patch

from ghdag.mcp.server import SUPPORTED_PROTOCOL_VERSION


def test_mcp_main_responds_to_initialize_on_stdin(capsys, tmp_path):
    from ghdag.cli import main

    repo = tmp_path / "repo"
    jobs = repo / "jobs"
    jobs.mkdir(parents=True)
    (jobs / "exec.jsonl").touch()
    (jobs / "done").mkdir()
    (jobs / "running").mkdir()

    stdin = io.StringIO(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": SUPPORTED_PROTOCOL_VERSION},
            }
        )
        + "\n"
    )

    with patch("sys.stdin", stdin), patch("sys.stdout", io.StringIO()) as stdout:
        main(
            [
                "mcp",
                "--repo-root",
                str(repo),
                "--exec-jsonl",
                str(jobs / "exec.jsonl"),
            ]
        )

    lines = [ln for ln in stdout.getvalue().splitlines() if ln.strip()]
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["result"]["protocolVersion"] == SUPPORTED_PROTOCOL_VERSION
