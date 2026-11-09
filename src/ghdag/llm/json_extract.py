"""Deterministic extraction of JSON text from LLM prose, fences, and wrappers."""

from __future__ import annotations

import json

from ghdag.llm.capabilities import LLMParseError

__all__ = ["extract_json", "extract_json_text"]


def extract_json_text(text: str) -> str:
    """Return a substring of ``text`` that ``json.loads`` accepts.

    Tries, in order: the last fenced block whose body parses as JSON; the
    leftmost balanced ``{...}`` or ``[...]`` value (string literals and
    escapes respected); the stripped full string.

    Raises:
        LLMParseError: when no JSON can be extracted (``reason == "empty"`` or
            parse failure).
    """
    if text == "":
        raise LLMParseError(raw=text, reason="empty")

    stripped = text.strip()
    if not stripped:
        raise LLMParseError(raw=text, reason="empty")

    for body in reversed(_fence_bodies(text)):
        candidate = body.strip()
        if not candidate:
            continue
        try:
            json.loads(candidate)
        except json.JSONDecodeError:
            continue
        return candidate

    balanced = _first_balanced_json(stripped)
    if balanced is not None:
        return balanced

    try:
        json.loads(stripped)
    except json.JSONDecodeError as e:
        raise LLMParseError(raw=text, reason=str(e)) from e
    return stripped


def extract_json(text: str):
    """Parse JSON extracted from ``text``."""
    return json.loads(extract_json_text(text))


def _fence_bodies(text: str) -> list[str]:
    bodies: list[str] = []
    i = 0
    while True:
        start = text.find("```", i)
        if start == -1:
            break
        line_end = text.find("\n", start + 3)
        if line_end == -1:
            break
        close = text.find("```", line_end + 1)
        if close == -1:
            break
        bodies.append(text[line_end + 1 : close])
        i = close + 3
    return bodies


def _first_balanced_json(text: str) -> str | None:
    for i, ch in enumerate(text):
        if ch not in "{[":
            continue
        end = _balanced_json_end(text, i)
        if end is None:
            continue
        candidate = text[i : end + 1]
        try:
            json.loads(candidate)
        except json.JSONDecodeError:
            continue
        return candidate
    return None


def _balanced_json_end(text: str, start: int) -> int | None:
    opener = text[start]
    if opener not in "{[":
        return None
    closer = "}" if opener == "{" else "]"
    stack = [closer]
    i = start + 1
    in_string = False
    escape = False
    while i < len(text):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            i += 1
            continue
        if ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if not stack or ch != stack[-1]:
                return None
            stack.pop()
            if not stack:
                return i
        i += 1
    return None
