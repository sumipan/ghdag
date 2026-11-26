"""G6: MetricsRecorder unit tests."""

from __future__ import annotations

import json

import pytest

from ghdag.core.models.metrics import TaskMetrics
from ghdag.metrics.recorder import MetricsRecorder

FIXED_NOW = 1_700_000_000.0


def make_metrics(**kwargs) -> TaskMetrics:
    now = FIXED_NOW
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



def test_record_includes_correlation_id(tmp_path):
    """Refs sumipan/ghdag#8060 AC6."""
    output = tmp_path / "metrics.jsonl"
    recorder = MetricsRecorder(output)
    recorder.record(make_metrics(uuid="t-corr", correlation_id="corr-1"))

    data = json.loads(output.read_text().splitlines()[0])
    assert data["correlation_id"] == "corr-1"


def test_record_omits_none_correlation_id(tmp_path):
    """Refs sumipan/ghdag#8060 AC7."""
    output = tmp_path / "metrics.jsonl"
    recorder = MetricsRecorder(output)
    recorder.record(make_metrics(uuid="t-none", correlation_id=None))

    data = json.loads(output.read_text().splitlines()[0])
    assert "correlation_id" not in data


def test_record_expands_additional_tags(tmp_path):
    """When additional_tags is set, expand key-value pairs onto the JSONL record."""
    output = tmp_path / "metrics.jsonl"
    recorder = MetricsRecorder(output)
    recorder.record(make_metrics(uuid="t-tags", additional_tags={"template": "b1", "tier": "heavy"}))

    data = json.loads(output.read_text().splitlines()[0])
    assert data["template"] == "b1"
    assert data["tier"] == "heavy"


