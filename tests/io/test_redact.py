"""Tests for ghdag.io.redact."""

from __future__ import annotations

import io
import json
import subprocess
import threading
from pathlib import Path

from ghdag.dag.task_launcher import _stdout_line_reader_redacting
from ghdag.io.redact import init_redact_patterns, redact_json_value, redact_text

_GITHUB_PAT = "github_pat_" + ("a" * 80)


def test_default_patterns_redact_tokens():
    init_redact_patterns(None)
    line = f"https://x-access-token:{_GITHUB_PAT}@github.com/org/repo"
    result = redact_text(line)
    assert _GITHUB_PAT not in result.text
    assert "[REDACTED:github_pat]" in result.text
    assert result.count == 1


def test_synthetic_slack_and_google_keys():
    init_redact_patterns(None)
    raw = "xoxb-1-abc xapp-1-def AIza" + ("0" * 35)
    result = redact_text(raw)
    assert "xoxb-1-abc" not in result.text
    assert "xapp-1-def" not in result.text
    assert ("AIza" + ("0" * 35)) not in result.text
    assert result.count >= 3


def test_custom_patterns_file(tmp_path: Path):
    path = tmp_path / "patterns.txt"
    path.write_text("# comment\nCUSTOM_SECRET\tsekret-[0-9]+\n", encoding="utf-8")
    init_redact_patterns(path)
    result = redact_text("value=sekret-999")
    assert result.text == "value=[REDACTED:CUSTOM_SECRET]"
    assert result.count == 1


def test_redact_json_value_recurses():
    init_redact_patterns(None)
    payload = {"outer": {"token": f"ghp_{'A' * 36}"}, "items": ["plain"]}
    redacted, count = redact_json_value(payload)
    assert count == 1
    assert "ghp_" not in json.dumps(redacted)


def test_no_change_returns_identical_line():
    init_redact_patterns(None)
    line = '{"type":"system","message":"hello"}\n'
    result = redact_text(line)
    assert result.text == line
    assert result.count == 0


def test_events_reader_redacts_and_appends_count(tmp_path: Path):
    init_redact_patterns(None)
    pat = "github_pat_" + ("z" * 80)
    line = json.dumps({"url": f"https://x-access-token:{pat}@github.com"}) + "\n"
    proc = subprocess.Popen(
        ["bash", "-c", f"printf %s '{line}'"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    buf = io.BytesIO()
    events_path = tmp_path / "events.jsonl"
    thread = threading.Thread(
        target=_stdout_line_reader_redacting,
        args=(proc, buf, events_path),
        daemon=True,
    )
    thread.start()
    proc.wait()
    thread.join(timeout=2.0)
    text = events_path.read_text(encoding="utf-8")
    assert pat not in text
    assert "[REDACTED:github_pat]" in text
    summary = json.loads(text.strip().splitlines()[-1])
    assert summary["redacted_count"] >= 1
