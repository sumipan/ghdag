"""Tests for ghdag.io.done — done-marker I/O consolidation (nexus Issue #2675)."""

from __future__ import annotations

from pathlib import Path


class TestIsDoneAndMarkDone:
    def test_not_done(self, tmp_path: Path) -> None:
        from ghdag.io.done import is_done

        assert is_done(tmp_path, "uuid-y") is False


class TestLoadDone:
    def test_load_done_nonexistent_dir(self, tmp_path: Path) -> None:
        from ghdag.io.done import load_done_from_dir

        assert load_done_from_dir(tmp_path / "nonexistent") == set()


class TestReadDoneContent:
    def test_missing_returns_none(self, tmp_path: Path) -> None:
        from ghdag.io.done import read_done_content

        assert read_done_content(tmp_path, "missing") is None


class TestDepSucceeded:
    def test_missing(self, tmp_path: Path) -> None:
        from ghdag.io.done import dep_succeeded

        assert dep_succeeded(tmp_path, "dep") is False


class TestDoneFailureClass:
    def test_success_marker_is_not_a_failure_class(self) -> None:
        from ghdag.io.done import done_failure_class

        assert done_failure_class("0") is None
        assert done_failure_class('{"status":"ok"}\n') is None

    def test_marker_mapping_table(self) -> None:
        from ghdag.core.models.metrics import FailureClass
        from ghdag.core.vocabulary import (
            DONE_EMPTY_RESULT,
            DONE_ENGINE_ERROR,
            DONE_TIMEOUT,
        )
        from ghdag.io.done import done_failure_class

        assert done_failure_class("REJECTED_FINAL") is FailureClass.REJECTED
        assert done_failure_class(f"{DONE_ENGINE_ERROR}\n") is FailureClass.ENGINE_ERROR
        assert (
            done_failure_class("PIPELINE_FAILED:PLAN_FAILED")
            is FailureClass.PIPELINE_FAILED
        )
        assert done_failure_class("3") is FailureClass.PROCESS_ERROR
        assert done_failure_class("CANCELLED") is None
        assert done_failure_class("???") is FailureClass.UNKNOWN_FAILURE
        assert done_failure_class(DONE_EMPTY_RESULT) is FailureClass.EMPTY_RESULT
        assert done_failure_class(DONE_TIMEOUT) is FailureClass.TIMEOUT


class TestShimCompat:
    def test_pipeline_status_reexports_io_functions(self) -> None:
        import ghdag.io.done as io_done
        import ghdag.pipeline.status as pipeline_status

        assert pipeline_status.dep_succeeded is io_done.dep_succeeded
        assert getattr(pipeline_status, "read_done_content", None) is None
