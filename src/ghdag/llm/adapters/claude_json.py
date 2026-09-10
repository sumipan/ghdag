"""claude --output-format json / stream-json の stdout から本文・TokenUsage を抽出する。"""

from __future__ import annotations

import json
import re
from datetime import datetime

from ghdag.core.models.metrics import FailureClass, TokenUsage
from ghdag.core.parsers import parse_token_usage_json
from ghdag.core.ports.output import EngineError, EngineErrorKind
from ghdag.llm.adapters.failure_classification import (
    classify_common_failure,
    looks_like_question,
)
from ghdag.llm.capabilities import LLMParseError


def extract_stream_result(stdout: str) -> str:
    """stream-json JSONL 出力から最終 result テキストを抽出する。

    DAG 経路の result ファイル書き出しと call() の stream 検証で共用する。
    """
    last_result: str | None = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("type") == "result":
            result = obj.get("result", "")
            last_result = result if isinstance(result, str) else json.dumps(result)
    if last_result is None:
        raise LLMParseError(
            raw=stdout, reason="no result line in stream-json output"
        )
    return last_result


def parse_claude_result_payload(stdout: bytes) -> dict | None:
    """単一 JSON または stream-json JSONL から最終 result オブジェクト（dict）を返す。

    JSONL の場合は最後の ``{"type":"result"}`` 行を優先する。
    従来の ``--output-format json`` 単一オブジェクト（type 無し含む）も受理する。
    """
    if not stdout:
        return None
    try:
        text = stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None

    last_result: dict | None = None
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


class ClaudeJsonAdapter:
    """JSON / stream-json 形式の claude stdout を処理し、result テキストと使用量を取り出す。

    JSON parse に失敗した場合はフォールバックとして raw stdout を返し、
    TokenUsage は None を返す。result_path の中身が壊れない安全弁として機能する。
    """

    def extract_result_text(self, stdout: bytes, stderr: bytes) -> bytes:
        if not stdout:
            return stdout
        data = parse_claude_result_payload(stdout)
        if data is None:
            return stdout
        result = data.get("result") or ""
        if isinstance(result, str):
            return result.encode("utf-8")
        return json.dumps(result).encode("utf-8")

    def extract_token_usage(self, stdout: bytes, stderr: bytes) -> TokenUsage | None:
        data = parse_claude_result_payload(stdout)
        if data is None:
            return None
        return parse_token_usage_json(data)

    def extract_session_id(self, stdout: bytes, stderr: bytes) -> str | None:
        data = parse_claude_result_payload(stdout)
        if data is None:
            return None
        session_id = data.get("session_id")
        return session_id if isinstance(session_id, str) and session_id else None

    def extract_error(self, stdout: bytes, stderr: bytes) -> EngineError | None:
        data = parse_claude_result_payload(stdout)
        if data is None:
            return None

        subtype = data.get("subtype")
        is_error = bool(data.get("is_error"))
        if not is_error and subtype not in {"error_during_execution", "error"}:
            return None

        message = _extract_error_message(data)
        kind, retryable, resume_at = _classify_error(message)
        return EngineError(kind=kind, message=message, retryable=retryable, resume_at=resume_at)

    def classify_failure(
        self,
        returncode: int,
        stdout: bytes,
        stderr: bytes,
    ) -> FailureClass | None:
        classified = classify_common_failure("claude", stdout, stderr)
        if classified is not None:
            return classified
        text = self.extract_result_text(stdout, stderr).decode("utf-8", errors="replace")
        if looks_like_question(text):
            return FailureClass.INTERACTIVE_PROMPT
        return None


def _extract_error_message(data: dict) -> str:
    err = data.get("error")
    if isinstance(err, dict):
        message = err.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    if isinstance(err, str) and err.strip():
        return err.strip()
    errors = data.get("errors")
    if isinstance(errors, list):
        for item in errors:
            if isinstance(item, str) and item.strip():
                return item.strip()
            if isinstance(item, dict):
                msg = item.get("message")
                if isinstance(msg, str) and msg.strip():
                    return msg.strip()
    message = data.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()
    result = data.get("result")
    if isinstance(result, str) and result.strip():
        return result.strip()
    subtype = data.get("subtype") or "unknown"
    return f"claude engine error ({subtype})"


_RESET_AT_RE = re.compile(
    r"(?:reset(?:s)?(?:\s+at)?|try again at)\s*[:\-]?\s*([0-9T:\-\+\.\s]{16,40}Z?)",
    re.IGNORECASE,
)


def _parse_reset_at(message: str) -> datetime | None:
    m = _RESET_AT_RE.search(message)
    if not m:
        return None
    candidate = m.group(1).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        return None
    return parsed


def _classify_error(message: str) -> tuple[EngineErrorKind, bool, datetime | None]:
    lower = message.lower()
    resume_at = _parse_reset_at(message)
    if "quota" in lower and "exhaust" in lower:
        return EngineErrorKind.QUOTA_EXHAUSTED, False, resume_at
    if "rate limit" in lower or "ratelimit" in lower:
        return EngineErrorKind.RATE_LIMIT, True, None
    if "overloaded" in lower or "capacity" in lower:
        return EngineErrorKind.CAPACITY, True, None
    if any(token in lower for token in ("auth", "unauthorized", "forbidden")):
        return EngineErrorKind.AUTH, False, None
    return EngineErrorKind.UNKNOWN, False, None
