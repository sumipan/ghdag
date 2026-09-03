"""tests/llm/test_call_text.py — TextResult dataclass と call_text 関数のユニットテスト。"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from unittest.mock import patch

import pytest

from ghdag.core.ports.output import EngineErrorKind
from ghdag.llm import TextResult, call_text
from ghdag.llm.engines import LLMResult

# ---------------------------------------------------------------------------
# TextResult dataclass テスト
# ---------------------------------------------------------------------------

class TestTextResult:
    def test_construction(self):
        raw = LLMResult(stdout="hello", stderr="", returncode=0)
        result = TextResult(body="hello", success=True, raw=raw)
        assert result.body == "hello"
        assert result.success is True
        assert result.raw is raw

    def test_frozen_raises_on_assignment(self):
        raw = LLMResult(stdout="hello", stderr="", returncode=0)
        result = TextResult(body="hello", success=True, raw=raw)
        with pytest.raises(FrozenInstanceError):
            result.body = "changed"  # type: ignore[misc]

    def test_stderr_delegates_to_raw(self):
        raw = LLMResult(stdout="", stderr="warn message", returncode=0)
        result = TextResult(body="", success=True, raw=raw)
        assert result.stderr == "warn message"

    def test_returncode_delegates_to_raw(self):
        raw = LLMResult(stdout="", stderr="", returncode=1)
        result = TextResult(body="", success=False, raw=raw)
        assert result.returncode == 1

    def test_stderr_and_returncode_from_raw(self):
        raw = LLMResult(stdout="out", stderr="err", returncode=2)
        result = TextResult(body="out", success=False, raw=raw)
        assert result.stderr == "err"
        assert result.returncode == 2

    def test_session_id_delegates_to_raw(self):
        raw = LLMResult(stdout="out", stderr="", returncode=0, session_id="sess-abc")
        result = TextResult(body="out", success=True, raw=raw)
        assert result.session_id == "sess-abc"


# ---------------------------------------------------------------------------
# call_text 関数テスト
# ---------------------------------------------------------------------------

def _make_llm_result(stdout: str, stderr: str = "", returncode: int = 0) -> LLMResult:
    return LLMResult(stdout=stdout, stderr=stderr, returncode=returncode)


class TestCallText:
    def test_error_stream_sets_success_false_and_empty_body(self):
        error_jsonl = '{"type":"error","message":"Selected model is at capacity."}'
        mock_result = _make_llm_result(stdout=error_jsonl, returncode=0)
        with patch("ghdag.llm.engines.call", return_value=mock_result):
            result = call_text("test prompt", engine="codex")
        assert result.success is False
        assert result.body == ""
        assert result.error is not None
        assert result.error.kind is EngineErrorKind.CAPACITY

    def test_claude_engine_extracts_result_field(self):
        """ClaudeJsonAdapter が {"result": "extracted"} から "extracted" を返すこと。"""
        import json
        json_stdout = json.dumps({"result": "extracted", "type": "result"})
        mock_result = _make_llm_result(stdout=json_stdout)
        with patch("ghdag.llm.engines.call", return_value=mock_result) as mock_call:
            result = call_text("test prompt", engine="claude")
        mock_call.assert_called_once()
        assert isinstance(result, TextResult)
        assert result.body == "extracted"
        assert result.success is True

    def test_adapter_empty_output_falls_back_to_raw_stdout(self):
        """adapter が空バイトを返した場合 body が raw.stdout にフォールバックすること。"""
        # CodexAdapter は agent_message が 0 件の場合に空バイトを返す
        mock_result = _make_llm_result(stdout="fallback text")
        with patch("ghdag.llm.engines.call", return_value=mock_result):
            with patch("ghdag.llm.adapters.get_output_adapter") as mock_get_adapter:
                mock_adapter = type("MockAdapter", (), {
                    "extract_result_text": lambda self, out, err: b"",
                })()
                mock_get_adapter.return_value = mock_adapter
                result = call_text("test prompt", engine="codex")
        assert result.body == "fallback text"

    def test_nonzero_returncode_sets_success_false(self):
        """call() が returncode=1 を返した場合 success=False かつ body にはフォールバック値。"""
        mock_result = _make_llm_result(stdout="error output", stderr="some error", returncode=1)
        with patch("ghdag.llm.engines.call", return_value=mock_result):
            result = call_text("test prompt", engine="claude")
        assert result.success is False
        assert result.returncode == 1
        assert result.error is None
        # body は adapter 出力または raw.stdout フォールバック — 空でないこと
        assert result.body

    def test_returns_text_result_type(self):
        mock_result = _make_llm_result(stdout="output")
        with patch("ghdag.llm.engines.call", return_value=mock_result):
            result = call_text("hello", engine="claude")
        assert isinstance(result, TextResult)

    def test_passes_kwargs_to_call(self):
        """call_text が call() に引数を正しく転送すること。"""
        mock_result = _make_llm_result(stdout="ok")
        with patch("ghdag.llm.engines.call", return_value=mock_result) as mock_call:
            call_text(
                "my prompt",
                engine="claude",
                model="claude-sonnet-5",
                timeout=30,
                stdin_text="input",
                dangerously_skip_permissions=True,
            )
        call_kwargs = mock_call.call_args
        assert call_kwargs[0][0] == "my prompt"
        assert call_kwargs[1]["engine"] == "claude"
        assert call_kwargs[1]["model"] == "claude-sonnet-5"
        assert call_kwargs[1]["timeout"] == 30
        assert call_kwargs[1]["stdin_text"] == "input"
        assert call_kwargs[1]["dangerously_skip_permissions"] is True

    def test_import_from_ghdag_llm(self):
        """from ghdag.llm import call_text, TextResult が成功すること。"""
        from ghdag.llm import TextResult as TR
        from ghdag.llm import call_text as ct
        assert TR is TextResult
        assert ct is call_text

    def test_codex_engine_extracts_agent_message(self):
        """CodexAdapter が JSONL の agent_message テキストを body に抽出すること。"""
        import json
        jsonl_line = json.dumps({
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "hello"},
        })
        mock_result = _make_llm_result(stdout=jsonl_line)
        with patch("ghdag.llm.engines.call", return_value=mock_result):
            result = call_text("test prompt", engine="codex")
        assert result.body == "hello"
        assert result.success is True

    def test_cursor_engine_passes_through_stdout(self):
        """CursorAdapter が stdout をそのまま body にパススルーすること。"""
        mock_result = _make_llm_result(stdout="cursor output")
        with patch("ghdag.llm.engines.call", return_value=mock_result):
            result = call_text("test prompt", engine="cursor")
        assert result.body == "cursor output"
        assert result.success is True

    def test_passthrough_engine_passes_through_stdout(self):
        """未知 engine で _PassthroughAdapter が stdout をそのまま body にパススルーすること。"""
        mock_result = _make_llm_result(stdout="raw text")
        with patch("ghdag.llm.engines.call", return_value=mock_result):
            result = call_text("test prompt", engine="unknown")
        assert result.body == "raw text"
        assert result.success is True

    def test_passthrough_empty_stdout_returns_empty(self):
        """_PassthroughAdapter で stdout が空の場合 body == "" となること。"""
        mock_result = _make_llm_result(stdout="")
        with patch("ghdag.llm.engines.call", return_value=mock_result):
            result = call_text("test prompt", engine="unknown")
        assert result.body == ""

    def test_json_text_preserved_in_body(self):
        """result フィールドに JSON 文字列が入っていた場合、body にそのまま保持されること。"""
        import json
        json_stdout = json.dumps({"result": '{"key": "val"}', "type": "result"})
        mock_result = _make_llm_result(stdout=json_stdout)
        with patch("ghdag.llm.engines.call", return_value=mock_result):
            result = call_text("test prompt", engine="claude")
        assert result.body == '{"key": "val"}'


class TestCallTextCwd:
    def test_call_text_forwards_cwd(self, tmp_path):
        mock_result = _make_llm_result(stdout="ok")
        with patch("ghdag.llm.engines.call", return_value=mock_result) as mock_call:
            call_text("hello", engine="claude", cwd=tmp_path)
        assert mock_call.call_args.kwargs["cwd"] == tmp_path
