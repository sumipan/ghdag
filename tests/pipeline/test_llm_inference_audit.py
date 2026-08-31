"""Tests for compute_prompt_hash() and write_llm_inference_audit() — Issue #1275."""

from __future__ import annotations

import hashlib
import json
import re

import pytest

from ghdag.pipeline.audit import compute_prompt_hash, write_llm_inference_audit

_UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


class TestComputePromptHash:
    def test_compute_prompt_hash_hello(self):
        expected = hashlib.sha256(b"hello").hexdigest()[:16]
        assert compute_prompt_hash("hello") == expected

    def test_compute_prompt_hash_empty(self):
        expected = hashlib.sha256(b"").hexdigest()[:16]
        assert compute_prompt_hash("") == expected

    def test_compute_prompt_hash_multibyte(self):
        prompt = "こんにちは"
        expected = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
        assert compute_prompt_hash(prompt) == expected

    def test_compute_prompt_hash_stable(self):
        assert compute_prompt_hash("same") == compute_prompt_hash("same")

    def test_compute_prompt_hash_differs(self):
        assert compute_prompt_hash("a") != compute_prompt_hash("b")


class TestWriteLlmInferenceAudit:
    def test_write_llm_inference_audit_record_fields(self, tmp_path):
        audit_path = tmp_path / "audit.jsonl"
        write_llm_inference_audit(
            audit_path,
            prompt_hash="abc123",
            latency_ms=2345.67,
            engine="claude",
            model="claude-sonnet-4-6",
            correlation_id="issuesmith:impl:1275",
        )

        assert audit_path.exists()
        r = json.loads(audit_path.read_text().strip())
        assert r["schema_version"] == 1
        assert r["event_type"] == "llm.inference"
        assert "+09:00" in r["timestamp"]
        assert _UUID4_RE.match(r["uuid"])
        assert r["prompt_hash"] == "abc123"
        assert r["latency_ms"] == 2345.7
        assert r["engine"] == "claude"
        assert r["model"] == "claude-sonnet-4-6"
        assert r["correlation_id"] == "issuesmith:impl:1275"

    def test_write_llm_inference_audit_correlation_id_none(self, tmp_path):
        audit_path = tmp_path / "audit.jsonl"
        write_llm_inference_audit(
            audit_path,
            prompt_hash="abc123",
            latency_ms=100.0,
            engine="claude",
            model="claude-sonnet-4-6",
        )
        r = json.loads(audit_path.read_text().strip())
        assert r["correlation_id"] is None

    def test_write_llm_inference_audit_rotation(self, tmp_path, monkeypatch):
        import ghdag.io._rotate as rotate_mod

        monkeypatch.setattr(rotate_mod, "_MAX_AUDIT_BYTES", 5)

        audit_path = tmp_path / "audit.jsonl"
        audit_path.write_text("x" * 10 + "\n")

        write_llm_inference_audit(
            audit_path,
            prompt_hash="abc123",
            latency_ms=100.0,
            engine="claude",
            model="model",
        )

        assert len(sorted(tmp_path.glob("audit.*.jsonl"))) == 1
        lines = audit_path.read_text().strip().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["event_type"] == "llm.inference"

    def test_write_failure_logs_stderr_no_exception(self, tmp_path, capsys):
        audit_path = tmp_path / "audit.jsonl"
        audit_path.mkdir()

        write_llm_inference_audit(
            audit_path,
            prompt_hash="abc123",
            latency_ms=100.0,
            engine="claude",
            model="model",
        )

        captured = capsys.readouterr()
        assert "[audit] warning:" in captured.err
        assert "llm inference audit" in captured.err


class TestCliEmitsLlmInference:
    def test_cli_emits_llm_inference(self, tmp_path):
        """ghdag llm 成功時に llm.inference レコードが追記される。"""
        from unittest.mock import patch

        from ghdag.llm.engines import LLMResult

        audit_path = tmp_path / "audit.jsonl"
        mock_result = LLMResult(
            stdout="ok\n",
            stderr="",
            returncode=0,
            latency_ms=42.5,
        )

        with patch("ghdag.llm.engines.call", return_value=mock_result), \
             patch("ghdag.llm.engines.validate_engine_model", return_value="claude-sonnet-4-6"):
            from ghdag.cli import main
            with pytest.raises(SystemExit) as exc:
                main([
                    "llm", "test",
                    "--engine", "claude",
                    "--model", "claude-sonnet-4-6",
                    "--audit-path", str(audit_path),
                ])
        assert exc.value.code == 0

        records = [json.loads(line) for line in audit_path.read_text().splitlines()]
        assert len(records) == 2
        inference = next(r for r in records if r.get("event_type") == "llm.inference")
        assert inference["prompt_hash"] == compute_prompt_hash("test")
        assert inference["latency_ms"] == 42.5
        assert inference["engine"] == "claude"
        assert inference["model"] == "claude-sonnet-4-6"
        assert inference["latency_ms"] > 0
