"""Tests for ghdag.audit.span — latency span public API (nexus #3323 / #3301 サブ8)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def _emit(**kwargs):
    from ghdag.audit.span import emit_span

    return emit_span(**kwargs)


def _base_kwargs(tmp_path: Path, **overrides) -> dict:
    defaults = dict(
        orchestration_id="orch-abc123def456",
        name="memory_retrieval",
        route="direct",
        start_ts="2026-08-12T07:15:32.123456Z",
        duration_ms=145,
        status="ok",
        span_id="s-01abcd",
        parent_span_id="s-00root",
        log_path=tmp_path / "latency_span.jsonl",
    )
    defaults.update(overrides)
    return defaults


class TestPublicApi:
    def test_import_emit_span_and_make_span_id(self) -> None:
        from ghdag.audit.span import emit_span, make_span_id

        assert callable(emit_span)
        assert callable(make_span_id)

    def test_package_reexports(self) -> None:
        from ghdag.audit import emit_span, make_span_id

        assert callable(emit_span)
        assert callable(make_span_id)


class TestEmitSpanSchema:
    def test_writes_valid_json(self, tmp_path: Path) -> None:
        log = tmp_path / "latency_span.jsonl"
        _emit(**_base_kwargs(tmp_path, log_path=log))
        lines = log.read_text().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["orchestration_id"] == "orch-abc123def456"
        assert record["trace_id"] == "orch-abc123def456"
        assert record["span_id"] == "s-01abcd"
        assert record["parent_span_id"] == "s-00root"
        assert record["name"] == "memory_retrieval"
        assert record["route"] == "direct"
        assert record["start_ts"] == "2026-08-12T07:15:32.123456Z"
        assert record["duration_ms"] == 145
        assert record["status"] == "ok"

    def test_event_type_latency_span_default(self, tmp_path: Path) -> None:
        log = tmp_path / "latency_span.jsonl"
        _emit(**_base_kwargs(tmp_path, name="memory_retrieval", status="ok", log_path=log))
        record = json.loads(log.read_text().strip())
        assert record["event_type"] == "latency_span"

    def test_event_type_slack_e2e_completed(self, tmp_path: Path) -> None:
        log = tmp_path / "latency_span.jsonl"
        _emit(**_base_kwargs(tmp_path, name="slack_e2e", status="ok", log_path=log))
        record = json.loads(log.read_text().strip())
        assert record["event_type"] == "slack_e2e_completed"

    def test_event_type_slack_e2e_failed_error(self, tmp_path: Path) -> None:
        log = tmp_path / "latency_span.jsonl"
        _emit(**_base_kwargs(tmp_path, name="slack_e2e", status="error", log_path=log))
        record = json.loads(log.read_text().strip())
        assert record["event_type"] == "slack_e2e_failed"

    def test_event_type_slack_e2e_failed_timeout(self, tmp_path: Path) -> None:
        log = tmp_path / "latency_span.jsonl"
        _emit(**_base_kwargs(tmp_path, name="slack_e2e", status="timeout", log_path=log))
        record = json.loads(log.read_text().strip())
        assert record["event_type"] == "slack_e2e_failed"

    def test_attributes_included_when_provided(self, tmp_path: Path) -> None:
        log = tmp_path / "latency_span.jsonl"
        _emit(
            **_base_kwargs(
                tmp_path,
                log_path=log,
                attributes={"engine": "claude", "model": "claude-opus-5", "attempt": 1},
            )
        )
        record = json.loads(log.read_text().strip())
        assert record["attributes"]["engine"] == "claude"
        assert record["attributes"]["model"] == "claude-opus-5"
        assert record["attributes"]["attempt"] == 1

    def test_attributes_absent_when_none(self, tmp_path: Path) -> None:
        log = tmp_path / "latency_span.jsonl"
        _emit(**_base_kwargs(tmp_path, log_path=log, attributes=None))
        record = json.loads(log.read_text().strip())
        assert "attributes" not in record

    def test_parent_span_id_null_for_root(self, tmp_path: Path) -> None:
        log = tmp_path / "latency_span.jsonl"
        _emit(**_base_kwargs(tmp_path, log_path=log, parent_span_id=None))
        record = json.loads(log.read_text().strip())
        assert record["parent_span_id"] is None

    def test_appends_multiple_records(self, tmp_path: Path) -> None:
        log = tmp_path / "latency_span.jsonl"
        for i in range(3):
            _emit(**_base_kwargs(tmp_path, log_path=log, span_id=f"s-{i:02d}", duration_ms=100 + i))
        lines = log.read_text().splitlines()
        assert len(lines) == 3

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        log = tmp_path / "nested" / "dir" / "latency_span.jsonl"
        _emit(**_base_kwargs(tmp_path, log_path=log))
        assert log.exists()

    def test_duration_ms_coerced_to_int(self, tmp_path: Path) -> None:
        log = tmp_path / "latency_span.jsonl"
        _emit(**_base_kwargs(tmp_path, log_path=log, duration_ms=145.7))
        record = json.loads(log.read_text().strip())
        assert isinstance(record["duration_ms"], int)
        assert record["duration_ms"] == 145

    def test_row_fields_match_nexus_schema(self, tmp_path: Path) -> None:
        """AC: event_type / orchestration_id / name / route / start_ts / duration_ms / status / attributes."""
        log = tmp_path / "latency_span.jsonl"
        _emit(
            **_base_kwargs(
                tmp_path,
                log_path=log,
                attributes={"engine": "cursor"},
            )
        )
        record = json.loads(log.read_text().strip())
        for key in (
            "event_type",
            "orchestration_id",
            "name",
            "route",
            "start_ts",
            "duration_ms",
            "status",
            "attributes",
        ):
            assert key in record


class TestLatencySpanPathEnv:
    def test_latency_span_path_env_used_when_log_path_omitted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from ghdag.audit.span import emit_span

        target = tmp_path / "from_env" / "latency_span.jsonl"
        monkeypatch.setenv("LATENCY_SPAN_PATH", str(target))
        emit_span(
            orchestration_id="orch-env",
            name="memory_retrieval",
            route="direct",
            start_ts="2026-08-12T07:15:32.123456Z",
            duration_ms=10,
            status="ok",
            span_id="s-env01",
            parent_span_id=None,
        )
        assert target.exists()
        record = json.loads(target.read_text().strip())
        assert record["orchestration_id"] == "orch-env"


class TestEmitSpanValidation:
    def test_missing_orchestration_id_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            _emit(**_base_kwargs(tmp_path, orchestration_id=""))

    def test_missing_start_ts_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            _emit(**_base_kwargs(tmp_path, start_ts=""))

    def test_missing_span_id_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            _emit(**_base_kwargs(tmp_path, span_id=""))


class TestPiiExclusion:
    def test_no_prompt_parameter(self) -> None:
        import inspect

        from ghdag.audit.span import emit_span

        sig = inspect.signature(emit_span)
        for pii_param in (
            "prompt",
            "response",
            "body",
            "text",
            "user_id",
            "channel_text",
            "message",
        ):
            assert pii_param not in sig.parameters, f"PII parameter '{pii_param}' must not exist"

    def test_no_pii_in_written_record(self, tmp_path: Path) -> None:
        log = tmp_path / "latency_span.jsonl"
        _emit(**_base_kwargs(tmp_path, log_path=log, attributes={"engine": "claude"}))
        record = json.loads(log.read_text().strip())
        for pii_key in ("prompt", "response", "body", "user_id", "channel_text", "message"):
            assert pii_key not in record
            if "attributes" in record:
                assert pii_key not in record["attributes"]


class TestMakeSpanId:
    def test_returns_8_chars(self) -> None:
        from ghdag.audit.span import make_span_id

        sid = make_span_id()
        assert len(sid) == 8

    def test_unique_on_each_call(self) -> None:
        from ghdag.audit.span import make_span_id

        ids = {make_span_id() for _ in range(100)}
        assert len(ids) == 100
