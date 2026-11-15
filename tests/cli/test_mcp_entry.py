"""CLI entry for ghdag mcp (#6297)."""

from __future__ import annotations

import io
import json
from unittest.mock import patch

import pytest

from ghdag.mcp.server import SUPPORTED_PROTOCOL_VERSION


def test_mcp_status_reads_done_from_ghdag_state_dir(capsys, tmp_path, monkeypatch):
    """Refs sumipan/nexus#6801."""
    import json

    from ghdag.cli import main

    monkeypatch.setenv("GHDAG_STATE_DIR", str(tmp_path / "state"))
    repo = tmp_path / "repo"
    jobs = repo / "jobs"
    jobs.mkdir(parents=True)
    uid = "00000000-0000-0000-0000-000000000099"
    (jobs / "exec.jsonl").write_text(
        json.dumps({"uuid": uid, "command": "echo x", "depends": []}) + "\n",
        encoding="utf-8",
    )
    state_done = tmp_path / "state" / "done"
    state_done.mkdir(parents=True)
    (state_done / uid).write_text("0\n", encoding="utf-8")

    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": SUPPORTED_PROTOCOL_VERSION},
        },
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "ghdag_status", "arguments": {"uuids": [uid]}},
        },
    ]
    stdin = io.StringIO("\n".join(json.dumps(m) for m in messages) + "\n")

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
    status_line = json.loads(lines[-1])
    task = status_line["result"]["structuredContent"]["tasks"][0]
    assert task["uuid"] == uid
    assert task["status"] == "success"


def test_mcp_main_read_path_reaches_ghdag_read(capsys, tmp_path):
    """Refs sumipan/nexus#6837 — CLI --read-path wires ghdag_read allowlist."""
    from ghdag.cli import main

    repo = tmp_path / "repo"
    state = repo / "state"
    jobs = repo / "jobs"
    state.mkdir(parents=True)
    jobs.mkdir(parents=True)
    rel = "state/x.json"
    body = '{"from":"host"}'
    (repo / rel).write_text(body, encoding="utf-8")
    (jobs / "exec.jsonl").touch()
    (jobs / "done").mkdir()
    (jobs / "running").mkdir()

    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": SUPPORTED_PROTOCOL_VERSION},
        },
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "ghdag_read", "arguments": {"path": rel}},
        },
    ]
    stdin = io.StringIO("\n".join(json.dumps(m) for m in messages) + "\n")

    with patch("sys.stdin", stdin), patch("sys.stdout", io.StringIO()) as stdout:
        main(
            [
                "mcp",
                "--repo-root",
                str(repo),
                "--exec-jsonl",
                str(jobs / "exec.jsonl"),
                "--read-path",
                rel,
            ]
        )

    lines = [ln for ln in stdout.getvalue().splitlines() if ln.strip()]
    payload = json.loads(lines[-1])
    assert payload["result"]["structuredContent"]["content"] == body


def test_mcp_main_rejects_dotdot_read_path(capsys, tmp_path):
    """Refs sumipan/nexus#6837 — invalid --read-path stops before serving."""
    from ghdag.cli import main

    repo = tmp_path / "repo"
    jobs = repo / "jobs"
    jobs.mkdir(parents=True)
    (jobs / "exec.jsonl").touch()

    with pytest.raises(SystemExit) as exc:
        main(
            [
                "mcp",
                "--repo-root",
                str(repo),
                "--exec-jsonl",
                str(jobs / "exec.jsonl"),
                "--read-path",
                "..",
            ]
        )
    assert exc.value.code != 0
