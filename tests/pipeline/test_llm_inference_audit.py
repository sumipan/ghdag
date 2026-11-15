"""Tests for compute_prompt_hash() and write_llm_inference_audit() — Issue #1275."""

from __future__ import annotations

import hashlib
import json
import re

import pytest

from ghdag.io.audit import compute_prompt_hash, write_llm_inference_audit

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
        prompt = "hello"
        expected = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
        assert compute_prompt_hash(prompt) == expected

    def test_compute_prompt_hash_stable(self):
        assert compute_prompt_hash("same") == compute_prompt_hash("same")

    def test_compute_prompt_hash_differs(self):
        assert compute_prompt_hash("a") != compute_prompt_hash("b")


class TestWriteLlmInferenceAudit:

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
        """On successful ghdag llm, an llm.inference record is appended."""
        from unittest.mock import patch

        from ghdag.llm.engines import LLMResult

        audit_path = tmp_path / "audit.jsonl"
        mock_result = LLMResult(
            body="ok\n",
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
