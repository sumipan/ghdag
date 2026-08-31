"""EngineOutputAdapter Protocol."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ghdag.core.models.metrics import TokenUsage


@runtime_checkable
class EngineOutputAdapter(Protocol):
    """engine 固有の stdout 形式から本文テキストと使用量を抽出する。"""

    def extract_result_text(self, stdout: bytes, stderr: bytes) -> bytes:
        """stdout から result_path に書くべきテキスト bytes を返す。"""
        ...

    def extract_token_usage(self, stdout: bytes, stderr: bytes) -> TokenUsage | None:
        """stdout/stderr から TokenUsage を抽出する。取得不能なら None。"""
        ...
