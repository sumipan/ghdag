"""EngineOutputAdapter Protocol."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from ghdag.core.models.metrics import TokenUsage


class EngineErrorKind(str, Enum):
    CAPACITY = "CAPACITY"
    RATE_LIMIT = "RATE_LIMIT"
    AUTH = "AUTH"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class EngineError:
    kind: EngineErrorKind
    message: str
    retryable: bool


@runtime_checkable
class EngineOutputAdapter(Protocol):
    """engine 固有の stdout 形式から本文テキストと使用量を抽出する。"""

    def extract_result_text(self, stdout: bytes, stderr: bytes) -> bytes:
        """stdout から result_path に書くべきテキスト bytes を返す。"""
        ...

    def extract_token_usage(self, stdout: bytes, stderr: bytes) -> TokenUsage | None:
        """stdout/stderr から TokenUsage を抽出する。取得不能なら None。"""
        ...

    def extract_session_id(self, stdout: bytes, stderr: bytes) -> str | None:
        """stdout/stderr から再開可能な session_id を抽出する。取得不能なら None。"""
        ...

    def extract_error(self, stdout: bytes, stderr: bytes) -> EngineError | None:
        """stdout/stderr からエンジンエラーを抽出する。エラー未検出なら None。"""
        ...
