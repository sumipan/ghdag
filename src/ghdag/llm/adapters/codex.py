"""Adapter extracting body text and TokenUsage from codex --json JSONL stdout."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from ghdag.core.models.metrics import FailureClass, TokenUsage
from ghdag.core.ports.output import EngineError, EngineErrorKind
from ghdag.llm.adapters.failure_classification import (
    QUOTA_DEFAULT_PAUSE_SECONDS,
    classify_common_failure,
    looks_like_question,
)


class CodexAdapter:
    """Process codex stdout in JSONL format and extract result text and usage.

    codex --json emits multi-line JSONL. The body is stored in item.text of
    item.completed + agent_message, and usage in the usage of turn.completed.

    LLMResult.stdout stays raw JSONL; callers that need text must go through
    extract_result_text() (note the asymmetry with the claude engine).
    """

    def extract_result_text(self, stdout: bytes, stderr: bytes) -> bytes:
        texts: list[str] = []
        for line in stdout.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                obj.get("type") == "item.completed"
                and isinstance(obj.get("item"), dict)
                and obj["item"].get("type") == "agent_message"
            ):
                text = obj["item"].get("text", "")
                if isinstance(text, str):
                    texts.append(text)
        return "\n".join(texts).encode("utf-8")

    def extract_token_usage(self, stdout: bytes, stderr: bytes) -> TokenUsage | None:
        for line in stdout.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") == "turn.completed":
                usage = obj.get("usage")
                if not isinstance(usage, dict):
                    return None
                input_tokens = usage.get("input_tokens", 0) or 0
                output_tokens = usage.get("output_tokens", 0) or 0
                cache_read = usage.get("cached_input_tokens", 0) or 0
                cache_write = usage.get("cache_write_input_tokens", 0) or 0
                return TokenUsage(
                    token_count=input_tokens + output_tokens,
                    cache_read_tokens=cache_read,
                    cache_creation_tokens=cache_write,
                    cost_usd=None,
                )
        return None

    def extract_session_id(self, stdout: bytes, stderr: bytes) -> str | None:
        """Prefer thread.started.thread_id; accept legacy session_id for backward compatibility."""
        fallback: str | None = None
        for line in stdout.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            if obj.get("type") == "thread.started":
                thread_id = obj.get("thread_id")
                if isinstance(thread_id, str) and thread_id:
                    return thread_id
            session_id = obj.get("session_id")
            if isinstance(session_id, str) and session_id and fallback is None:
                fallback = session_id
        return fallback

    def extract_error(self, stdout: bytes, stderr: bytes) -> EngineError | None:
        for line in stdout.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") not in {"error", "turn.failed"}:
                continue
            message = _extract_error_message(obj)
            observed_at = datetime.now(timezone.utc)
            kind, retryable, resume_at = _classify_error(message, observed_at)
            return EngineError(
                kind=kind,
                message=message,
                retryable=retryable,
                resume_at=resume_at,
            )
        return None

    def classify_failure(
        self,
        returncode: int,
        stdout: bytes,
        stderr: bytes,
    ) -> FailureClass | None:
        classified = classify_common_failure("codex", stdout, stderr, returncode=returncode)
        if classified is not None:
            return classified
        if returncode != 0:
            last_agent_message = _last_agent_message_text(stdout)
            if looks_like_question(last_agent_message):
                return FailureClass.INTERACTIVE_PROMPT
        text = _decode_streams(stdout, stderr)
        lower = text.lower()
        if "failed to load models cache" in lower:
            return FailureClass.ENGINE_ERROR
        if "sigkill" in lower or returncode in {137, -9}:
            return FailureClass.ENGINE_ERROR
        if "stream" in lower and any(token in lower for token in ("disconnect", "closed", "broken pipe")):
            return FailureClass.ENGINE_ERROR
        return None


def _last_agent_message_text(stdout: bytes) -> str:
    """Return the text of the last agent_message item in the stdout JSONL."""
    last = ""
    for line in stdout.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            obj.get("type") == "item.completed"
            and isinstance(obj.get("item"), dict)
            and obj["item"].get("type") == "agent_message"
        ):
            text = obj["item"].get("text", "")
            if isinstance(text, str):
                last = text
    return last


def _extract_error_message(obj: dict) -> str:
    for key in ("message", "error", "detail"):
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            nested = value.get("message")
            if isinstance(nested, str) and nested.strip():
                return nested.strip()
    event_type = obj.get("type")
    return f"codex engine error ({event_type})"


_RESET_AT_RE = re.compile(
    r"(?:reset(?:s)?(?:\s+at)?|try again at)\s*[:\-]?\s*([0-9T:\-\+\.\s]{16,40}Z?)",
    re.IGNORECASE,
)


# Human-readable format returned by codex with ChatGPT account auth (observed 2026-09-09):
#   "You've hit your usage limit. ... or try again at Sep 10th, 2026 2:13 AM."
# No timezone is given, so it is interpreted as local time.
_HUMAN_RESET_AT_RE = re.compile(
    r"try again at\s+([A-Za-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})\s+(\d{1,2}):(\d{2})\s*(AM|PM)",
    re.IGNORECASE,
)


def _parse_human_reset_at(message: str) -> datetime | None:
    m = _HUMAN_RESET_AT_RE.search(message)
    if not m:
        return None
    month, day, year, hour, minute, ampm = m.groups()
    text = f"{month[:3].title()} {int(day)} {year} {int(hour)}:{minute} {ampm.upper()}"
    try:
        naive = datetime.strptime(text, "%b %d %Y %I:%M %p")
    except ValueError:
        return None
    return naive.replace(tzinfo=datetime.now().astimezone().tzinfo)


def _parse_reset_at(message: str) -> datetime | None:
    human = _parse_human_reset_at(message)
    if human is not None:
        return human
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


def _classify_error(message: str, observed_at: datetime) -> tuple[EngineErrorKind, bool, datetime | None]:
    lower = message.lower()
    if "quota" in lower and "exhaust" in lower:
        resume_at = _parse_reset_at(message) or (
            observed_at + timedelta(seconds=QUOTA_DEFAULT_PAUSE_SECONDS)
        )
        return EngineErrorKind.QUOTA_EXHAUSTED, False, resume_at
    if "usage limit" in lower:
        # "You've hit your usage limit. ... try again at Sep 10th, 2026 2:13 AM." (observed 2026-09-09)
        resume_at = _parse_reset_at(message) or (
            observed_at + timedelta(seconds=QUOTA_DEFAULT_PAUSE_SECONDS)
        )
        return EngineErrorKind.QUOTA_EXHAUSTED, False, resume_at
    if "rate limit" in lower or "ratelimit" in lower:
        return EngineErrorKind.RATE_LIMIT, True, None
    if "capacity" in lower or "overloaded" in lower:
        return EngineErrorKind.CAPACITY, True, None
    if any(token in lower for token in ("auth", "unauthorized", "forbidden")):
        return EngineErrorKind.AUTH, False, None
    return EngineErrorKind.UNKNOWN, False, None


def _decode_streams(stdout: bytes, stderr: bytes) -> str:
    stdout_text = stdout.decode("utf-8", errors="replace")
    stderr_text = stderr.decode("utf-8", errors="replace")
    if stdout_text and stderr_text:
        return f"{stdout_text}\n{stderr_text}"
    return stdout_text or stderr_text
