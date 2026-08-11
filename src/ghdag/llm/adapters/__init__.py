"""ghdag.llm.adapters — CLI 出力→テキスト/メトリクス変換アダプター。

EngineOutputAdapter Protocol は engine 固有の stdout 形式から
本文テキストと TokenUsage を抽出する責務を持つ。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ghdag.metrics.models import TokenUsage


@runtime_checkable
class EngineOutputAdapter(Protocol):
    """engine 固有の stdout 形式から本文テキストと使用量を抽出する。"""

    def extract_result_text(self, stdout: bytes, stderr: bytes) -> bytes:
        """stdout から result_path に書くべきテキスト bytes を返す。"""
        ...

    def extract_token_usage(self, stdout: bytes, stderr: bytes) -> TokenUsage | None:
        """stdout/stderr から TokenUsage を抽出する。取得不能なら None。"""
        ...


class _PassthroughAdapter:
    """未知エンジン用デフォルト: stdout パススルー、usage は None。"""

    def extract_result_text(self, stdout: bytes, stderr: bytes) -> bytes:
        return stdout

    def extract_token_usage(self, stdout: bytes, stderr: bytes) -> TokenUsage | None:
        return None


_DEFAULT_ADAPTER = _PassthroughAdapter()


def get_output_adapter(engine: str | None) -> EngineOutputAdapter:
    """エンジン名から適切な EngineOutputAdapter を返す。

    claude エンジンの output_format デフォルトは json。
    JSON parse 失敗時は raw stdout を返すフォールバックが ClaudeJsonAdapter に内蔵されている。
    """
    if engine == "claude":
        from ghdag.llm.adapters.claude_json import ClaudeJsonAdapter
        return ClaudeJsonAdapter()
    if engine == "cursor":
        from ghdag.llm.adapters.cursor import CursorAdapter
        return CursorAdapter()
    if engine == "codex":
        from ghdag.llm.adapters.codex import CodexAdapter
        return CodexAdapter()
    return _DEFAULT_ADAPTER


__all__ = ["EngineOutputAdapter", "get_output_adapter"]
