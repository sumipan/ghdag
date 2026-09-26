"""Shared failure classification helpers for engine adapters."""

from __future__ import annotations

import os
import re

from ghdag.core.engine_spec import ENGINE_SPECS
from ghdag.core.models.metrics import FailureClass

QUOTA_DEFAULT_PAUSE_SECONDS: int = int(
    os.environ.get("GHDAG_QUOTA_DEFAULT_PAUSE_SECONDS", 18000)
)


def last_nonempty_line(text: str) -> str:
    """末尾の非空行を返す（無ければ空文字）。"""
    stripped = text.rstrip()
    if not stripped:
        return ""
    return stripped.splitlines()[-1].strip()


def looks_like_question(text: str) -> bool:
    """最終行がユーザーへの質問で終わるかを判定する（ASCII `?` / 全角 `？`）。"""
    last_line = last_nonempty_line(text)
    if not last_line:
        return False
    return last_line.endswith(("?", "？"))


def classify_common_failure(
    binary: str,
    stdout: bytes,
    stderr: bytes,
    returncode: int | None = None,
) -> FailureClass | None:
    text = _decode_streams(stdout, stderr)
    if _is_environment_error(text, binary, returncode):
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
    # codex（ChatGPT アカウント認証）: "You've hit your usage limit. ... try again at Sep 10th, 2026 2:13 AM."
    # 2026-09-09 実測。quota / rate limit のどの語も含まないため未検知で PROCESS_ERROR 扱いになり、
    # pause も fallback も効かず後続ステップが連鎖失敗した。
    if "usage limit" in lower:
        return True
    return "resets " in lower and "hit your session limit" in lower


# Whole-word match so that e.g. "author" is not taken as an auth error.
_AUTH_PATTERN = re.compile(
    r"\b(?:auth|authentication|authorization|unauthenticated|unauthorized|forbidden)\b"
    r"|oauth session expired|invalid api key|not logged in",
    re.IGNORECASE,
)

_ENV_ERROR_REASONS = r"(?:command not found|no such file or directory|permission denied)"


def _is_auth_error(message: str) -> bool:
    return _AUTH_PATTERN.search(message) is not None


def _match_names(binary: str) -> list[str]:
    """Names to match: the engine name and its real CLI binary (cursor -> agent)."""
    names = [binary]
    spec = ENGINE_SPECS.get(binary)
    if spec is not None and spec.cli not in names:
        names.append(spec.cli)
    return names


def _is_environment_error(message: str, binary: str, returncode: int | None = None) -> bool:
    lower = message.lower()
    if not binary:
        # nexus failure_classifier.py calls with binary=""; keep the legacy loose check.
        return "no such file or directory" in lower or "permission denied" in lower
    if returncode == 127 and "command not found" in lower:
        return True
    names = "|".join(re.escape(name.lower()) for name in _match_names(binary))
    # bash / sh: "bash: line 1: agent: command not found" (name after line start, space, `:` or quote)
    shell_format = rf"""(?:^|[\s:'"])(?:{names})['"]?: {_ENV_ERROR_REASONS}"""
    # Python errno: "[Errno 2] No such file or directory: 'agent'"
    errno_format = rf"(?:no such file or directory|permission denied): '(?:{names})'"
    return (
        re.search(shell_format, lower, re.MULTILINE) is not None
        or re.search(errno_format, lower) is not None
    )
