"""Tests for DefaultHooks logging-only behavior — Issue #1090 AC-2, AC-3."""

from __future__ import annotations

from ghdag.dag.hooks import DefaultHooks


class TestDefaultHooksCheckRejected:
    def test_reply_block_rejected(self, tmp_path) -> None:
        f = tmp_path / "result.md"
        f.write_text(
            "```ghdag\nstatus: rejected\nreason: retry\n```\n",
            encoding="utf-8",
        )
        hooks = DefaultHooks()
        assert hooks.check_rejected(str(f)) is True


    def test_legacy_rejected_on_first_line(self, tmp_path) -> None:
        f = tmp_path / "result.md"
        f.write_text("REJECTED: x\n", encoding="utf-8")
        hooks = DefaultHooks()
        assert hooks.check_rejected(str(f)) is True



class TestDefaultHooksLoggingOnly:
    def test_no_args_init(self):
        """AC-2: DefaultHooks() can be instantiated with no arguments."""
        hooks = DefaultHooks()
        assert hooks is not None


