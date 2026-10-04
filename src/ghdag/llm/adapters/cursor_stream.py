"""Extract body text and TokenUsage from cursor agent --output-format stream-json JSONL."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

from ghdag.core.models.metrics import FailureClass, TokenUsage
from ghdag.core.ports.output import EngineError, EngineErrorKind
from ghdag.llm.adapters.failure_classification import (
    classify_common_failure,
    looks_like_question,
)

_RETRIABLE_STDERR_RE = re.compile(
    r"^RetriableError: (?:\[(?P<code>[a-z_]+)\] ?)?"
)

_STDERR_AUTH_CODES = frozenset({"unauthenticated", "permission_denied"})


def reconstruct_assistant_turns(stdout: bytes) -> str | None:
    """Reconstruct each assistant turn body from stream-json JSONL, separated by blank lines.

    Turn boundaries are ``type == "tool_call"``, which finalize the preceding turn;
    consecutive tool_calls without assistant text in between produce no empty turn.
    The last turn is finalized at ``type == "result"`` or end of input.

    Within a turn, if complete full texts exist (see ``_is_complete_assistant_event``), the
    last one is used; otherwise deltas are concatenated in order. Completeness is decided by
    event structure only, never by comparing text with the delta concatenation (a repeated
    leading delta such as ``"l"`` / ``"l"`` must not be mistaken for a complete text).
    Returns ``None`` if no turn body is non-empty.
    """
    if not stdout:
        return None
    try:
        text = stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None

    turns: list[str] = []
    partials: list[str] = []
    last_complete: str | None = None

    def finalize_turn() -> None:
        nonlocal partials, last_complete
        if last_complete is not None:
            turns.append(last_complete)
        else:
            joined = "".join(partials)
            if joined:
                turns.append(joined)
        partials = []
        last_complete = None

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue

        event_type = obj.get("type")
        if event_type == "tool_call":
            finalize_turn()
            continue
        if event_type == "result":
            finalize_turn()
            break
        if event_type != "assistant":
            continue

        piece = _assistant_text_content(obj)
        if not piece:
            continue

        if _is_complete_assistant_event(obj):
            last_complete = piece
            partials = []
            continue

        partials.append(piece)

    else:
        finalize_turn()

    if not turns:
        return None
    return "\n\n".join(turns)


def _is_complete_assistant_event(obj: dict[Any, Any]) -> bool:
    """Whether an assistant event carries the complete text rather than a streaming delta.

    Complete texts have a non-empty ``model_call_id`` or lack ``timestamp_ms``
    (``--stream-partial-output`` deltas carry ``timestamp_ms``).
    """
    model_call_id = obj.get("model_call_id")
    if isinstance(model_call_id, str) and model_call_id:
        return True
    return "timestamp_ms" not in obj


def _assistant_text_content(obj: dict[Any, Any]) -> str:
    """Concatenate only the text content of an assistant event (non-text / non-str ignored)."""
    message = obj.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "text":
            continue
        value = item.get("text")
        if isinstance(value, str) and value:
            parts.append(value)
    return "".join(parts)


class CursorStreamAdapter:
    """Cursor adapter handling both stream-json and single JSON.

    Prefers the reconstructed assistant turn body if present; otherwise falls back to the
    result of the final ``{"type":"result"}`` line, then single JSON, then raw stdout.
    The legacy ``--output-format json`` single object is also accepted.
    """

    def extract_result_text(self, stdout: bytes, stderr: bytes) -> bytes:
        if not stdout:
            return stdout
        reconstructed = reconstruct_assistant_turns(stdout)
        if reconstructed:
            return reconstructed.encode("utf-8")
        data = parse_cursor_result_payload(stdout)
        if data is None:
            return stdout
        result = data.get("result")
        if isinstance(result, str):
            return result.encode("utf-8")
        if result is None:
            # type=result but no result key -> return raw rather than empty (do not corrupt)
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
        # If absent from the result line, scan the entire JSONL (backward compatibility)
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
        if data is not None and data.get("type") == "result":
            subtype = data.get("subtype")
            is_error = bool(data.get("is_error"))
            if not is_error and subtype not in {"error_during_execution", "error"}:
                return None
            message = (
                data.get("result") or data.get("message") or f"cursor engine error ({subtype})"
            )
            if not isinstance(message, str):
                message = str(message)
            kind, retryable = _classify_result_message(message)
            return EngineError(kind=kind, message=message, retryable=retryable)
        return _classify_stderr(stderr)

    def classify_failure(
        self,
        returncode: int,
        stdout: bytes,
        stderr: bytes,
    ) -> FailureClass | None:
        classified = classify_common_failure("cursor", stdout, stderr, returncode=returncode)
        if classified is not None:
            return classified
        if returncode != 0:
            data = parse_cursor_result_payload(stdout)
            if data is not None:
                result = data.get("result", "")
                if isinstance(result, str) and looks_like_question(result):
                    return FailureClass.INTERACTIVE_PROMPT
        return None

    def is_terminal_result_event(self, event: dict[str, Any]) -> bool:
        """Return whether this event line is the final result (vs. a progress event)."""
        return isinstance(event, dict) and event.get("type") == "result"


def _classify_stderr(stderr: bytes) -> EngineError | None:
    """Find a RetriableError line at the tail of stderr and convert it to EngineError."""
    text = stderr.decode("utf-8", errors="replace")
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        m = _RETRIABLE_STDERR_RE.match(stripped)
        if m is None:
            continue
        code = m.group("code") or ""
        if code in _STDERR_AUTH_CODES:
            return None
        kind = EngineErrorKind.RATE_LIMIT if code == "resource_exhausted" else EngineErrorKind.CAPACITY
        return EngineError(kind=kind, message=stripped, retryable=True)
    return None


def _classify_result_message(message: str) -> tuple[EngineErrorKind, bool]:
    """Convert the message string of a result payload to (EngineErrorKind, retryable)."""
    lower = message.lower()
    if "quota" in lower and "exhaust" in lower:
        return EngineErrorKind.QUOTA_EXHAUSTED, False
    if "rate limit" in lower or "ratelimit" in lower:
        return EngineErrorKind.RATE_LIMIT, True
    if "overloaded" in lower or "capacity" in lower:
        return EngineErrorKind.CAPACITY, True
    if "auth" in lower or "unauthorized" in lower or "forbidden" in lower:
        return EngineErrorKind.AUTH, False
    return EngineErrorKind.UNKNOWN, False


def parse_cursor_result_payload(stdout: bytes) -> dict[Any, Any] | None:
    """Return the final result object from a single JSON or stream-json JSONL."""
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
