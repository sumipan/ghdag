"""Reply block fence detection and YAML validation — Issue #6248."""

from __future__ import annotations

import pytest

from ghdag.core.reply_block import (
    ReplyBlock,
    ReplyBlockError,
    find_reply_block,
    parse_reply_block,
)


class TestFindReplyBlock:
    def test_no_fence_returns_none(self) -> None:
        assert find_reply_block("no block") is None

    def test_ghdag_fanout_info_string_not_matched(self) -> None:
        text = "```ghdag_fanout\nstatus: ok\n```\n"
        assert find_reply_block(text) is None

    def test_nested_ghdag_inside_markdown_fence_ignored(self) -> None:
        text = (
            "```markdown\n"
            "```ghdag\n"
            "status: ok\n"
            "```\n"
            "```\n"
        )
        assert find_reply_block(text) is None


class TestParseReplyBlock:
    def test_ok_status_minimal(self) -> None:
        block = parse_reply_block("text\n```ghdag\nstatus: ok\n```\n")
        assert block == ReplyBlock(status="ok")

    def test_no_block_returns_none(self) -> None:
        assert parse_reply_block("no block") is None

    def test_unknown_status_raises_with_hint(self) -> None:
        with pytest.raises(ReplyBlockError) as exc:
            parse_reply_block("```ghdag\nstatus: nope\n```\n")
        err = exc.value
        assert err.code == "REPLY_BLOCK_INVALID"
        assert err.hint is not None
        assert "wait_quota" in err.hint

    def test_unknown_key_raises_with_hint(self) -> None:
        with pytest.raises(ReplyBlockError) as exc:
            parse_reply_block("```ghdag\nstatus: ok\nfoo: 1\n```\n")
        assert "foo" in (exc.value.hint or "")

    def test_after_without_timezone_raises(self) -> None:
        with pytest.raises(ReplyBlockError):
            parse_reply_block(
                "```ghdag\n"
                "status: wait_quota\n"
                "engines: [claude]\n"
                "after: 2026-01-01T00:00:00\n"
                "```\n"
            )

    def test_unquoted_tz_aware_after_is_accepted(self) -> None:
        block = parse_reply_block(
            "```ghdag\n"
            "status: wait_quota\n"
            "engines: [claude]\n"
            "after: 2026-01-01T00:00:00+09:00\n"
            "```\n"
        )
        assert block is not None
        assert block.after == "2026-01-01T00:00:00+09:00"

    def test_rejected_requires_nonempty_reason(self) -> None:
        with pytest.raises(ReplyBlockError):
            parse_reply_block("```ghdag\nstatus: rejected\n```\n")

    def test_failed_requires_code(self) -> None:
        with pytest.raises(ReplyBlockError):
            parse_reply_block("```ghdag\nstatus: failed\n```\n")
