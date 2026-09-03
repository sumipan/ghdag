"""claude --output-format json の stdout から本文テキストと TokenUsage を抽出するアダプター。"""

from __future__ import annotations

import json
import re
from datetime import datetime

from ghdag.core.models.metrics import FailureClass, TokenUsage
from ghdag.core.parsers import parse_token_usage_json
from ghdag.core.ports.output import EngineError, EngineErrorKind
from ghdag.llm.adapters.failure_classification import classify_common_failure


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
        kind, retryable, resume_at = _classify_error(message)
        return EngineError(kind=kind, message=message, retryable=retryable, resume_at=resume_at)

    def classify_failure(
        self,
        returncode: int,
        stdout: bytes,
        stderr: bytes,
    ) -> FailureClass | None:
        return classify_common_failure("claude", stdout, stderr)


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


