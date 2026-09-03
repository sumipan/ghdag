"""Shared failure classification helpers for engine adapters."""

from __future__ import annotations

from ghdag.core.models.metrics import FailureClass


def classify_common_failure(binary: str, stdout: bytes, stderr: bytes) -> FailureClass | None:
    text = _decode_streams(stdout, stderr)
    if _is_environment_error(text, binary):
        return FailureClass.ENGINE_ENVIRONMENT_ERROR
    if _is_quota_exhausted_error(text):
        return FailureClass.QUOTA_EXHAUSTED
    if _is_auth_error(text):
        return FailureClass.AUTH
    return None


def _decode_streams(stdout: bytes, stderr: bytes) -> str:
    stdout_text = stdout.decode("utf-8", errors="replace")
    stderr_text = stderr.decode("utf-8", errors="replace")
    if stdout_text and stderr_text:
        return f"{stdout_text}\n{stderr_text}"
    return stdout_text or stderr_text


def _is_quota_exhausted_error(message: str) -> bool:
    lower = message.lower()
    if "session limit" in lower:
        return True
    if "you've reached your monthly" in lower:
        return True
    return "resets " in lower and "hit your session limit" in lower


def _is_auth_error(message: str) -> bool:
    lower = message.lower()
    return any(
        token in lower
        for token in ("auth", "unauthorized", "forbidden", "oauth session expired")
    )


def _is_environment_error(message: str, binary: str) -> bool:
    lower = message.lower()
    return binary.lower() in lower and (
        "no such file or directory" in lower
        or "permission denied" in lower
    )
