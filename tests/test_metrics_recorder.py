"""G6: MetricsRecorder unit tests."""

from __future__ import annotations

import concurrent.futures
import json
import time

import pytest

from ghdag.metrics.models import TaskMetrics
from ghdag.metrics.recorder import MetricsRecorder


def make_metrics(**kwargs) -> TaskMetrics:
    now = time.time()
    defaults = {
        "uuid": "test-uuid",
        "engine": "claude",
        "model": "claude-opus-4-6",
        "wall_time_sec": 1.5,
        "token_count": 100,
        "status": "success",
        "started_at": now - 1.5,
        "finished_at": now,
    }
    defaults.update(kwargs)
    return TaskMetrics(**defaults)


def test_record_single_line(tmp_path):
    output = tmp_path / "metrics.jsonl"
    recorder = MetricsRecorder(output)
    recorder.record(make_metrics(uuid="t1"))

    lines = output.read_text().splitlines()
    assert len(lines) == 1
    data = json.loads(lines[0])
    for field in ("uuid", "engine", "model", "wall_time_sec", "token_count", "status", "started_at", "finished_at", "timestamp"):
        assert field in data
    assert data["uuid"] == "t1"
    assert "+09:00" in data["timestamp"]


def test_record_three_lines(tmp_path):
    output = tmp_path / "metrics.jsonl"
    recorder = MetricsRecorder(output)
    for i in range(3):
        recorder.record(make_metrics(uuid=f"t{i}"))

    lines = output.read_text().splitlines()
    assert len(lines) == 3
    for line in lines:
        json.loads(line)


def test_record_missing_directory(tmp_path):
    output = tmp_path / "nonexistent" / "dir" / "out.jsonl"
    recorder = MetricsRecorder(output)
    with pytest.warns(UserWarning):
        recorder.record(make_metrics())


def test_record_errors_log(tmp_path):
    output = tmp_path / "metrics.jsonl"
    output.write_text("")
    output.chmod(0o444)

    recorder = MetricsRecorder(output)
    with pytest.warns(UserWarning):
        recorder.record(make_metrics())

    error_log = output.parent / (output.name + ".errors.log")
    assert error_log.exists()
    content = error_log.read_text()
    assert "Traceback" in content


def test_record_parallel_writes(tmp_path):
    output = tmp_path / "metrics.jsonl"
    recorder = MetricsRecorder(output)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(recorder.record, make_metrics(uuid=f"t{i}")) for i in range(2)]
        concurrent.futures.wait(futures)

    lines = output.read_text().splitlines()
    assert len(lines) == 2
    for line in lines:
        json.loads(line)
