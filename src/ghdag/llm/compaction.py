"""ghdag.llm.compaction — resume-session handoff summary compaction."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ghdag.llm.engines import TextResult, call_text
from ghdag.llm.session import SessionRecord, SessionStore

# genshijin is a handoff-summary style for compaction prompts only.
# It must NOT be applied to result files, Slack replies, diary, or reviews.
GENSHIJIN_HANDOFF_PROMPT = """\
You are a session handoff compressor. No human-facing tone, honorifics, or recap of history is needed.
Output, as densely as possible, only the minimal context the next session needs to continue the same work.

Strictly follow this JSON schema for the output (do not drop any markers):
{
  "facts": ["established facts"],
  "unresolved": ["unresolved items"],
  "next": ["next action instructions"],
  "files": ["required file references"],
  "constraints": ["constraints"]
}

Rules:
- Prioritize facts, unresolved items, next instructions, file references, and constraints
- Avoid honorifics, duplicated background explanations, and recaps of history
- Do not write unnecessary preambles or closing remarks
- Write values in the language of the conversation being summarized.
"""

DEFAULT_TOKEN_THRESHOLD = 100_000


@dataclass(frozen=True)
class CompactionResult:
    """Outcome of a compaction attempt before resume injection."""

    status: str  # compacted | skipped | fallback
    reason: str
    session_id: str | None
    parent_session_id: str | None
    summary_tokens: int | None = None
    tokens_before: int | None = None
    tokens_after: int | None = None
    compacted_key: str | None = None


@dataclass
class CompactionPolicy:
    """Opt-in policy: compact only when enabled and over the token threshold."""

    token_threshold: int = DEFAULT_TOKEN_THRESHOLD
    enabled: bool = False
    supported_engines: frozenset[str] = field(default_factory=lambda: frozenset({"claude"}))

    def should_compact(
        self,
        parent_record: SessionRecord,
        *,
        token_usage: int | None = None,
    ) -> bool:
        if not self.enabled:
            return False
        if parent_record.engine not in self.supported_engines:
            return False
        if token_usage is None:
            return False
        return token_usage >= self.token_threshold

    def decide_reason(
        self,
        parent_record: SessionRecord | None,
        *,
        token_usage: int | None = None,
    ) -> str | None:
        """Return skip/fallback reason, or None when compaction should proceed."""
        if parent_record is None:
            return "session_miss"
        if not self.enabled:
            return "policy_disabled"
        if parent_record.engine not in self.supported_engines:
            return "engine_unsupported"
        if token_usage is None:
            return "missing_token_usage"
        if token_usage < self.token_threshold:
            return "below_threshold"
        return None


def estimate_summary_tokens(summary: str) -> int:
    """Rough token estimate for audit (≈4 chars / token)."""
    if not summary:
        return 0
    return max(1, (len(summary) + 3) // 4)


def compact_resume_session(
    *,
    store: SessionStore,
    parent_key: str,
    parent_record: SessionRecord | None,
    compacted_key: str,
    policy: CompactionPolicy | None = None,
    token_usage: int | None = None,
    call_text_fn: Callable[..., TextResult] | None = None,
    cwd: Path | str | None = None,
    model: str | None = None,
) -> CompactionResult:
    """Compact a parent resume session into a new handoff session, or fall back.

    On any skip/failure, ``session_id`` is the parent session (when available)
    so callers can continue with conventional ``--resume``.
    """
    policy = policy or CompactionPolicy()
    call_fn = call_text_fn or call_text
    parent_session_id = parent_record.session_id if parent_record else None

    skip_reason = policy.decide_reason(parent_record, token_usage=token_usage)
    if skip_reason is not None:
        status = "fallback" if skip_reason == "session_miss" else "skipped"
        return CompactionResult(
            status=status,
            reason=skip_reason,
            session_id=parent_session_id,
            parent_session_id=parent_session_id,
            tokens_before=token_usage,
            tokens_after=token_usage,
        )

    assert parent_record is not None  # for type checkers

    try:
        summary_result = call_fn(
            GENSHIJIN_HANDOFF_PROMPT,
            engine=parent_record.engine,
            model=model,
            resume_session_id=parent_record.session_id,
            cwd=cwd,
        )
    except Exception:
        return CompactionResult(
            status="fallback",
            reason="prompt_error",
            session_id=parent_session_id,
            parent_session_id=parent_session_id,
            tokens_before=token_usage,
            tokens_after=token_usage,
        )

    if not summary_result.success or not summary_result.body.strip():
        return CompactionResult(
            status="fallback",
            reason="prompt_error",
            session_id=parent_session_id,
            parent_session_id=parent_session_id,
            tokens_before=token_usage,
            tokens_after=token_usage,
        )

    summary = summary_result.body.strip()
    summary_tokens = estimate_summary_tokens(summary)
    seed_prompt = (
        "Continue the prior work using only this handoff summary as context.\n"
        f"{summary}"
    )

    try:
        seeded = call_fn(
            seed_prompt,
            engine=parent_record.engine,
            model=model,
            cwd=cwd,
        )
    except Exception:
        return CompactionResult(
            status="fallback",
            reason="prompt_error",
            session_id=parent_session_id,
            parent_session_id=parent_session_id,
            summary_tokens=summary_tokens,
            tokens_before=token_usage,
            tokens_after=token_usage,
        )

    new_session_id = seeded.session_id
    if not seeded.success or not new_session_id:
        return CompactionResult(
            status="fallback",
            reason="prompt_error",
            session_id=parent_session_id,
            parent_session_id=parent_session_id,
            summary_tokens=summary_tokens,
            tokens_before=token_usage,
            tokens_after=token_usage,
        )

    store.record_compacted(
        compacted_key,
        parent_record.engine,
        new_session_id,
        parent_key,
        summary_tokens=summary_tokens,
    )

    return CompactionResult(
        status="compacted",
        reason="over_threshold",
        session_id=new_session_id,
        parent_session_id=parent_session_id,
        summary_tokens=summary_tokens,
        tokens_before=token_usage,
        tokens_after=summary_tokens,
        compacted_key=compacted_key,
    )


def lookup_parent_token_usage(metrics_path: Path, parent_uuid: str) -> int | None:
    """Read the latest token_count for ``parent_uuid`` from metrics JSONL."""
    if not metrics_path.is_file():
        return None
    latest: int | None = None
    try:
        for line in metrics_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec: dict[str, Any] = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if rec.get("uuid") != parent_uuid:
                continue
            token_count = rec.get("token_count")
            if isinstance(token_count, int):
                latest = token_count
    except OSError:
        return None
    return latest
