"""Shared failure classification helpers for engine adapters."""

from __future__ import annotations

import logging
import os
import re

from ghdag.config.language import get_language_pack
from ghdag.core.engine_spec import ENGINE_SPECS
from ghdag.core.exceptions import GhdagError
from ghdag.core.models.metrics import FailureClass

logger = logging.getLogger(__name__)

# Always treated as a question ending; language packs can only add to it.
_BUILTIN_QUESTION_SUFFIXES: tuple[str, ...] = ("?",)

QUOTA_DEFAULT_PAUSE_SECONDS: int = int(
    os.environ.get("GHDAG_QUOTA_DEFAULT_PAUSE_SECONDS", 18000)
)


def last_nonempty_line(text: str) -> str:
    """Return the last non-empty line, stripped (empty string if none)."""
    stripped = text.rstrip()
    if not stripped:
        return ""
    return stripped.splitlines()[-1].strip()


def looks_like_question(text: str) -> bool:
    """Return True if the last non-empty line ends like a question to the user.

    ASCII ``?`` always counts; the active language pack's
    ``question_suffixes`` are added to it. If the pack cannot be loaded, a
    warning is logged and only ASCII ``?`` is used.
    """
    last_line = last_nonempty_line(text)
    if not last_line:
        return False
    try:
        extra = get_language_pack().question_suffixes
    except GhdagError as exc:
        logger.warning("language pack unavailable, using ASCII '?' only: %s", exc)
        extra = ()
    return last_line.endswith(_BUILTIN_QUESTION_SUFFIXES + extra)


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
    # codex (ChatGPT account auth): "You've hit your usage limit. ... try again at Sep 10th, 2026 2:13 AM."
    # Observed 2026-09-09. It contains none of the quota / rate limit words, so it went
    # undetected as PROCESS_ERROR; neither pause nor fallback kicked in and later steps
    # failed in a chain.
    if "usage limit" in lower:
        return True
    return "resets " in lower and "hit your session limit" in lower


# Whole-word match so that e.g. "author" is not taken as an auth error.
# `_` counts as a separator so snake_case codes such as claude's
# "authentication_error" (OAuth token expiry, invalid key) still match.
_AUTH_PATTERN = re.compile(
    r"(?<![a-z0-9])"
    r"(?:auth|authenticate|authentication|authorization|unauthenticated|unauthorized|forbidden)"
    r"(?![a-z0-9])"
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
        # Callers that pass binary="" rely on the legacy loose check; keep it.
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
