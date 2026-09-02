"""claude --output-format json の stdout から本文テキストと TokenUsage を抽出するアダプター。"""

from __future__ import annotations

import json

from ghdag.core.models.metrics import TokenUsage
from ghdag.core.parsers import parse_token_usage_json
from ghdag.core.ports.output import EngineError, EngineErrorKind


class ClaudeJsonAdapter:
    """JSON 形式の claude stdout を処理し、result テキストと使用量を取り出す。

    JSON parse に失敗した場合はフォールバックとして raw stdout を返し、
    TokenUsage は None を返す。result_path の中身が壊れない安全弁として機能する。
    """

    def extract_result_text(self, stdout: bytes, stderr: bytes) -> bytes:
        if not stdout:
            return stdout
        try:
            data = json.loads(stdout.decode("utf-8"))
            return (data.get("result") or "").encode("utf-8")
        except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
            return stdout

    def extract_token_usage(self, stdout: bytes, stderr: bytes) -> TokenUsage | None:
        if not stdout:
            return None
        try:
            data = json.loads(stdout.decode("utf-8"))
            return parse_token_usage_json(data)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def extract_session_id(self, stdout: bytes, stderr: bytes) -> str | None:
        if not stdout:
            return None
        try:
            data = json.loads(stdout.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        session_id = data.get("session_id") if isinstance(data, dict) else None
        return session_id if isinstance(session_id, str) and session_id else None

    def extract_error(self, stdout: bytes, stderr: bytes) -> EngineError | None:
        if not stdout:
            return None
        try:
            data = json.loads(stdout.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        if not isinstance(data, dict):
            return None

        subtype = data.get("subtype")
        is_error = bool(data.get("is_error"))
        if not is_error and subtype not in {"error_during_execution", "error"}:
            return None

        message = _extract_error_message(data)
        kind, retryable = _classify_error(message)
        return EngineError(kind=kind, message=message, retryable=retryable)


def _extract_error_message(data: dict) -> str:
    err = data.get("error")
    if isinstance(err, dict):
        message = err.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    if isinstance(err, str) and err.strip():
        return err.strip()
    message = data.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()
    subtype = data.get("subtype") or "unknown"
    return f"claude engine error ({subtype})"


def _classify_error(message: str) -> tuple[EngineErrorKind, bool]:
    lower = message.lower()
    if "rate limit" in lower or "ratelimit" in lower:
        return EngineErrorKind.RATE_LIMIT, True
    if "overloaded" in lower or "capacity" in lower:
        return EngineErrorKind.CAPACITY, True
    if any(token in lower for token in ("auth", "unauthorized", "forbidden")):
        return EngineErrorKind.AUTH, False
    return EngineErrorKind.UNKNOWN, False
