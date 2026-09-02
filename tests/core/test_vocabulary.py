"""Snapshot tests for ghdag.core.vocabulary shared markers and regexes."""

from __future__ import annotations

from ghdag.core.vocabulary import (
    DONE_DEP_FAILED,
    DONE_EMPTY_RESULT,
    DONE_ENGINE_ENV_ERROR,
    DONE_FANOUT_CHILD_FAILED,
    DONE_FANOUT_PARSE_FAILED,
    DONE_ORPHAN_ARCHIVED,
    DONE_PIPELINE_FAILED_PREFIX,
    DONE_REJECTED,
    DONE_REJECTED_FINAL,
    DONE_SKIPPED_MISSING_INPUT,
    DONE_SUCCESS,
    DONE_TIMEOUT,
    DONE_UNKNOWN_FAILURE,
    FANOUT_ANCHOR,
    FANOUT_KEY,
    FANOUT_SEPARATOR,
    PIPELINE_STATUS_RE,
    QUEUE_FILE_RE,
)


class TestDoneMarkers:
    def test_done_success(self) -> None:
        assert DONE_SUCCESS == "0"

    def test_done_rejected(self) -> None:
        assert DONE_REJECTED == "REJECTED"

    def test_done_rejected_final(self) -> None:
        assert DONE_REJECTED_FINAL == "REJECTED_FINAL"

    def test_done_timeout(self) -> None:
        assert DONE_TIMEOUT == "TIMEOUT"

    def test_done_dep_failed(self) -> None:
        assert DONE_DEP_FAILED == "DEP_FAILED"

    def test_done_empty_result(self) -> None:
        assert DONE_EMPTY_RESULT == "EMPTY_RESULT"

    def test_done_engine_environment_error(self) -> None:
        assert DONE_ENGINE_ENV_ERROR == "ENGINE_ENVIRONMENT_ERROR"

    def test_done_pipeline_failed_prefix(self) -> None:
        assert DONE_PIPELINE_FAILED_PREFIX == "PIPELINE_FAILED:"

    def test_done_fanout_child_failed(self) -> None:
        assert DONE_FANOUT_CHILD_FAILED == "FANOUT_CHILD_FAILED"

    def test_done_fanout_parse_failed(self) -> None:
        assert DONE_FANOUT_PARSE_FAILED == "FANOUT_PARSE_FAILED"

    def test_done_skipped_missing_input(self) -> None:
        assert DONE_SKIPPED_MISSING_INPUT == "SKIPPED_MISSING_INPUT"

    def test_done_unknown_failure(self) -> None:
        assert DONE_UNKNOWN_FAILURE == "UNKNOWN_FAILURE"

    def test_done_orphan_archived(self) -> None:
        assert DONE_ORPHAN_ARCHIVED == "ORPHAN_ARCHIVED"


class TestQueueFileRe:
    def test_matches_canonical_name(self) -> None:
        name = "20260831120000-claude-order-12345678-1234-1234-1234-123456789abc.md"
        m = QUEUE_FILE_RE.match(name)
        assert m is not None
        assert m.group(1) == "20260831120000"
        assert m.group(2) == "claude"
        assert m.group(3) == "order"
        assert m.group(4) == "12345678-1234-1234-1234-123456789abc"

    def test_matches_result_and_stderr(self) -> None:
        for kind in ("result", "stderr"):
            name = f"20260831120000-gemini-{kind}-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.md"
            assert QUEUE_FILE_RE.match(name) is not None

    def test_rejects_invalid_names(self) -> None:
        assert QUEUE_FILE_RE.match("not-a-queue-file.md") is None
        assert QUEUE_FILE_RE.match("20260831120000-claude-order-not-a-uuid.md") is None
        assert QUEUE_FILE_RE.match("short-claude-order-12345678-1234-1234-1234-123456789abc.md") is None


class TestPipelineStatusRe:
    def test_matches_accepted(self) -> None:
        text = "hello\nPIPELINE_STATUS: ACCEPTED\n"
        assert PIPELINE_STATUS_RE.findall(text) == ["ACCEPTED"]

    def test_matches_impl_failed(self) -> None:
        text = "PIPELINE_STATUS: IMPL_FAILED"
        assert PIPELINE_STATUS_RE.findall(text) == ["IMPL_FAILED"]

    def test_no_match_without_prefix(self) -> None:
        assert PIPELINE_STATUS_RE.findall("STATUS: ACCEPTED") == []


class TestFanoutVocabulary:
    def test_fanout_separator(self) -> None:
        assert FANOUT_SEPARATOR == "--fo--"

    def test_fanout_anchor(self) -> None:
        assert FANOUT_ANCHOR == "ghdag_fanout:"

    def test_fanout_key(self) -> None:
        assert FANOUT_KEY == "ghdag_fanout"
