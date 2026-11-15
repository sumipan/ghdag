"""Tests for PIPELINE_STATUS parser and engine integration — Issue #477."""

from __future__ import annotations

from ghdag.dag import check_pipeline_status


class TestCheckPipelineStatus:

    def test_done_status_returns_string(self, tmp_path):
        f = tmp_path / "result.md"
        f.write_text("PIPELINE_STATUS: IMPL_DONE\n", encoding="utf-8")
        assert check_pipeline_status(str(f)) == "IMPL_DONE"

    def test_no_pipeline_status_returns_none(self, tmp_path):
        f = tmp_path / "result.md"
        f.write_text("some output\nwithout status line\n", encoding="utf-8")
        assert check_pipeline_status(str(f)) is None

    def test_impl_failed_returns_string(self, tmp_path):
        f = tmp_path / "result.md"
        f.write_text("PIPELINE_STATUS: IMPL_FAILED\n", encoding="utf-8")
        assert check_pipeline_status(str(f)) == "IMPL_FAILED"

    def test_brushup_failed_returns_string(self, tmp_path):
        f = tmp_path / "result.md"
        f.write_text("PIPELINE_STATUS: BRUSHUP_FAILED\n", encoding="utf-8")
        assert check_pipeline_status(str(f)) == "BRUSHUP_FAILED"

    def test_no_space_after_colon(self, tmp_path):
        f = tmp_path / "result.md"
        f.write_text("PIPELINE_STATUS:IMPL_FAILED\n", encoding="utf-8")
        assert check_pipeline_status(str(f)) == "IMPL_FAILED"

    def test_leading_space_not_detected(self, tmp_path):
        f = tmp_path / "result.md"
        f.write_text("  PIPELINE_STATUS: IMPL_FAILED\n", encoding="utf-8")
        assert check_pipeline_status(str(f)) is None

    def test_missing_file_returns_none(self, tmp_path):
        assert check_pipeline_status(str(tmp_path / "nonexistent.md")) is None

    def test_empty_value_not_matched(self, tmp_path):
        f = tmp_path / "result.md"
        f.write_text("PIPELINE_STATUS:   \n", encoding="utf-8")
        assert check_pipeline_status(str(f)) is None

    def test_reply_block_failed_code(self, tmp_path) -> None:
        f = tmp_path / "result.md"
        f.write_text("```ghdag\nstatus: failed\ncode: SCHEMA\n```\n", encoding="utf-8")
        assert check_pipeline_status(str(f)) == "SCHEMA_FAILED"

    def test_invalid_reply_block_returns_reply_block_failed(self, tmp_path) -> None:
        f = tmp_path / "result.md"
        f.write_text("```ghdag\n: not yaml\n```\n", encoding="utf-8")
        assert check_pipeline_status(str(f)) == "REPLY_BLOCK_FAILED"

    def test_reply_block_ok_ignores_legacy_pipeline_status_line(self, tmp_path) -> None:
        f = tmp_path / "result.md"
        f.write_text(
            "PIPELINE_STATUS: X_FAILED\n```ghdag\nstatus: ok\n```\n",
            encoding="utf-8",
        )
        assert check_pipeline_status(str(f)) is None
