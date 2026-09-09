"""cursor agent CLI 用アダプター（JSON / テキスト stdout 両対応）。"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from ghdag.core.models.metrics import FailureClass, TokenUsage
from ghdag.core.ports.output import EngineError
from ghdag.llm.adapters.failure_classification import classify_common_failure


class CursorAdapter:
    def extract_result_text(self, stdout: bytes, stderr: bytes) -> bytes:
        if not stdout:
            return stdout
        obj = _parse_json_object(stdout)
        if obj is None:
            return stdout
        result = obj.get("result")
        if isinstance(result, str):
            return result.encode("utf-8")
        return stdout

    def extract_token_usage(self, stdout: bytes, stderr: bytes) -> TokenUsage | None:
        obj = _parse_json_object(stdout)
        if obj is None:
            return None
        usage = obj.get("usage")
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
        return None

    def classify_failure(
        self,
        returncode: int,
        stdout: bytes,
        stderr: bytes,
    ) -> FailureClass | None:
        return classify_common_failure("cursor", stdout, stderr)


def _parse_json_object(stdout: bytes) -> dict[Any, Any] | None:
    """単一 JSON オブジェクト、または JSONL 先頭のオブジェクトを返す。"""
    text = stdout.decode("utf-8", errors="replace").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict):
        return data
    for obj in _iter_json_objects(stdout):
        return obj
    return None


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
