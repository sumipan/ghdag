"""cursor agent --output-format stream-json の JSONL から本文・TokenUsage を抽出する。"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from ghdag.core.models.metrics import FailureClass, TokenUsage
from ghdag.core.ports.output import EngineError
from ghdag.llm.adapters.failure_classification import (
    classify_common_failure,
    looks_like_question,
)


class CursorStreamAdapter:
    """stream-json / 単一 JSON の両方を処理する cursor 用アダプター。

    最終 ``{"type":"result"}`` 行から result テキスト・session_id・usage を取る。
    従来の ``--output-format json`` 単一オブジェクトも受理する。
    """

    def extract_result_text(self, stdout: bytes, stderr: bytes) -> bytes:
        if not stdout:
            return stdout
        data = parse_cursor_result_payload(stdout)
        if data is None:
            return stdout
        result = data.get("result")
        if isinstance(result, str):
            return result.encode("utf-8")
        if result is None:
            # type=result だが result キー無し → 空ではなく raw を返す（壊さない）
            if data.get("type") == "result":
                return b""
            return stdout
        return json.dumps(result).encode("utf-8")

    def extract_token_usage(self, stdout: bytes, stderr: bytes) -> TokenUsage | None:
        data = parse_cursor_result_payload(stdout)
        if data is None:
            return None
        usage = data.get("usage")
        if not isinstance(usage, dict):
            return None
        input_tokens = usage.get("inputTokens") or 0
        output_tokens = usage.get("outputTokens") or 0
        if not isinstance(input_tokens, int):
            input_tokens = 0
        if not isinstance(output_tokens, int):
            output_tokens = 0
        total = input_tokens + output_tokens
        if total <= 0:
            return None
        cache_read = usage.get("cacheReadTokens")
        cache_write = usage.get("cacheWriteTokens")
        return TokenUsage(
            token_count=total,
            cache_read_tokens=cache_read if isinstance(cache_read, int) else None,
            cache_creation_tokens=cache_write if isinstance(cache_write, int) else None,
            cost_usd=None,
        )

    def extract_session_id(self, stdout: bytes, stderr: bytes) -> str | None:
        data = parse_cursor_result_payload(stdout)
        if data is not None:
            session_id = data.get("session_id")
            if isinstance(session_id, str) and session_id:
                return session_id
            chat_id = data.get("chat_id")
            if isinstance(chat_id, str) and chat_id:
                return chat_id
        # result 行に無い場合は JSONL 全体を走査（後方互換）
        fallback: str | None = None
        for obj in _iter_json_objects(stdout):
            session_id = obj.get("session_id")
            if isinstance(session_id, str) and session_id:
                return session_id
            chat_id = obj.get("chat_id")
            if isinstance(chat_id, str) and chat_id and fallback is None:
                fallback = chat_id
        return fallback

    def extract_error(self, stdout: bytes, stderr: bytes) -> EngineError | None:
        data = parse_cursor_result_payload(stdout)
        if data is None:
            return None
        subtype = data.get("subtype")
        is_error = bool(data.get("is_error"))
        if not is_error and subtype not in {"error_during_execution", "error"}:
            return None
        message = data.get("result") or data.get("message") or f"cursor engine error ({subtype})"
        if not isinstance(message, str):
            message = str(message)
        from ghdag.core.ports.output import EngineErrorKind

        return EngineError(
            kind=EngineErrorKind.UNKNOWN,
            message=message,
            retryable=False,
        )

    def classify_failure(
        self,
        returncode: int,
        stdout: bytes,
        stderr: bytes,
    ) -> FailureClass | None:
        classified = classify_common_failure("cursor", stdout, stderr)
        if classified is not None:
            return classified
        data = parse_cursor_result_payload(stdout)
        if data is not None:
            result = data.get("result", "")
            if isinstance(result, str) and looks_like_question(result):
                return FailureClass.INTERACTIVE_PROMPT
        return None

    def is_terminal_result_event(self, event: dict[str, Any]) -> bool:
        """このイベント行が最終 result か（進捗イベントか）を判定する。"""
        return isinstance(event, dict) and event.get("type") == "result"


def parse_cursor_result_payload(stdout: bytes) -> dict[Any, Any] | None:
    """単一 JSON または stream-json JSONL から最終 result オブジェクトを返す。"""
    if not stdout:
        return None
    try:
        text = stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None

    last_result: dict[Any, Any] | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("type") == "result":
            last_result = obj
    if last_result is not None:
        return last_result

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _iter_json_objects(stdout: bytes) -> Iterator[dict[Any, Any]]:
    for line in stdout.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            yield obj
