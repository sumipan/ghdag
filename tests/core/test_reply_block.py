"""Reply block fence detection and YAML validation — Issues #6248, #7549."""

from __future__ import annotations

import pytest

from ghdag.core.reply_block import (
    ReplyBlock,
    ReplyBlockError,
    find_reply_block,
    is_rejected_reply,
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

    def test_ok_with_success_code_parses(self) -> None:
        block = parse_reply_block("```ghdag\nstatus: ok\ncode: IMPL_DONE\n```\n")
        assert block == ReplyBlock(status="ok", code="IMPL_DONE")

    def test_ok_with_invalid_code_raises(self) -> None:
        with pytest.raises(ReplyBlockError):
            parse_reply_block("```ghdag\nstatus: ok\ncode: impl_done\n```\n")

    def test_ok_code_must_not_end_with_failed_suffix(self) -> None:
        with pytest.raises(ReplyBlockError):
            parse_reply_block("```ghdag\nstatus: ok\ncode: IMPL_FAILED\n```\n")

    def test_ok_must_not_combine_code_and_children(self) -> None:
        with pytest.raises(ReplyBlockError):
            parse_reply_block(
                "```ghdag\n"
                "status: ok\n"
                "code: FANOUT_DONE\n"
                "children:\n"
                "  - id: a\n"
                "    prompt: x\n"
                "```\n"
            )


class TestReplyBlockProblems:
    """Issue #8165 — optional problems on status failed."""

    def test_failed_with_problems_preserves_item_text(self) -> None:
        block = parse_reply_block(
            "```ghdag\n"
            "status: failed\n"
            "code: IMPL\n"
            "problems:\n"
            "  - lint failed\n"
            "  - '  spaced  '\n"
            "```\n"
        )
        assert block == ReplyBlock(
            status="failed",
            code="IMPL",
            problems=("lint failed", "  spaced  "),
        )

    def test_failed_without_problems_yields_empty_tuple(self) -> None:
        block = parse_reply_block("```ghdag\nstatus: failed\ncode: IMPL\n```\n")
        assert block is not None
        assert block.problems == ()

    def test_failed_with_null_or_empty_problems_yields_empty_tuple(self) -> None:
        block = parse_reply_block(
            "```ghdag\nstatus: failed\ncode: IMPL\nproblems: []\n```\n"
        )
        assert block is not None
        assert block.problems == ()

    def test_ok_with_nonempty_problems_raises(self) -> None:
        with pytest.raises(ReplyBlockError) as exc:
            parse_reply_block("```ghdag\nstatus: ok\nproblems: [detail]\n```\n")
        assert exc.value.hint == "problems \u306f status: failed \u306e\u3068\u304d\u3060\u3051"

    def test_rejected_with_nonempty_problems_raises(self) -> None:
        with pytest.raises(ReplyBlockError) as exc:
            parse_reply_block(
                "```ghdag\n"
                "status: rejected\n"
                "reason: need context\n"
                "problems: [detail]\n"
                "```\n"
            )
        assert exc.value.hint == "problems \u306f status: failed \u306e\u3068\u304d\u3060\u3051"

    def test_wait_quota_with_nonempty_problems_raises(self) -> None:
        with pytest.raises(ReplyBlockError) as exc:
            parse_reply_block(
                "```ghdag\n"
                "status: wait_quota\n"
                "engines: [claude]\n"
                "problems: [detail]\n"
                "```\n"
            )
        assert exc.value.hint == "problems \u306f status: failed \u306e\u3068\u304d\u3060\u3051"

    def test_problems_must_be_a_list(self) -> None:
        with pytest.raises(ReplyBlockError):
            parse_reply_block(
                "```ghdag\nstatus: failed\ncode: IMPL\nproblems: one\n```\n"
            )

    def test_problems_entries_must_be_strings(self) -> None:
        with pytest.raises(ReplyBlockError):
            parse_reply_block(
                "```ghdag\n"
                "status: failed\n"
                "code: IMPL\n"
                "problems:\n"
                "  - 1\n"
                "```\n"
            )

    def test_problems_rejects_whitespace_only_item(self) -> None:
        with pytest.raises(ReplyBlockError):
            parse_reply_block(
                "```ghdag\n"
                "status: failed\n"
                "code: IMPL\n"
                "problems:\n"
                "  - '   '\n"
                "```\n"
            )


class TestIsRejectedReply:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            (
                "```ghdag\nstatus: rejected\nreason: x\n```\n",
                True,
            ),
            ("no block", False),
            ("```ghdag\nstatus: rejected\n```\n", False),
            (
                "```ghdag\nstatus: ok\ncode: IMPL_DONE\n```\n",
                False,
            ),
        ],
        ids=["rejected", "no_block", "invalid_rejected", "ok"],
    )
    def test_cases(self, text: str, expected: bool) -> None:
        """Issue #7967 AC3."""
        assert is_rejected_reply(text) is expected
