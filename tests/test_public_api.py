"""Tests for public API surface and py.typed marker (issue-1064)."""

import pathlib


def test_py_typed_marker_exists():
    import ghdag
    p = pathlib.Path(ghdag.__file__).parent / "py.typed"
    assert p.exists(), "py.typed marker missing — PEP 561 compliance required"


def test_top_level_exports_only_ghdag_error():
    import ghdag

    assert ghdag.__all__ == ["GhdagError"]
    assert hasattr(ghdag, "GhdagError")


def test_definition_packages_importable():
    from ghdag.dag import DagEngine
    from ghdag.io.queue import QueueTask, QueueTaskStore
    from ghdag.pipeline.llm_pipeline import LLMPipelineAPI
    from ghdag.pipeline.state import PipelineState
    from ghdag.quota import QuotaGate
    from ghdag.status import IssueStatus, RunningTask, issue_status, running_tasks
    from ghdag.workflow import WorkflowDispatcher

    assert LLMPipelineAPI is not None
    assert PipelineState is not None
    assert DagEngine is not None
    assert WorkflowDispatcher is not None
    assert QuotaGate is not None
    assert QueueTask is not None
    assert QueueTaskStore is not None
    assert IssueStatus is not None
    assert RunningTask is not None
    assert callable(issue_status)
    assert callable(running_tasks)


def test_check_pipeline_status_importable():
    from ghdag.dag import check_pipeline_status
    assert check_pipeline_status is not None


def test_check_pipeline_status_in_dag_all():
    import ghdag.dag
    assert "check_pipeline_status" in ghdag.dag.__all__


def test_check_pipeline_status_is_same_object():
    import ghdag.dag
    import ghdag.dag._util
    assert ghdag.dag.check_pipeline_status is ghdag.dag._util.check_pipeline_status


def test_check_pipeline_status_impl_done(tmp_path):
    from ghdag.dag import check_pipeline_status
    f = tmp_path / "result.md"
    f.write_text("PIPELINE_STATUS: IMPL_DONE\n", encoding="utf-8")
    assert check_pipeline_status(str(f)) == "IMPL_DONE"


def test_check_pipeline_status_no_status_returns_none(tmp_path):
    from ghdag.dag import check_pipeline_status
    f = tmp_path / "result.md"
    f.write_text("some output\nno status line\n", encoding="utf-8")
    assert check_pipeline_status(str(f)) is None


def test_check_pipeline_status_missing_file_returns_none(tmp_path):
    from ghdag.dag import check_pipeline_status
    assert check_pipeline_status(str(tmp_path / "nonexistent.md")) is None


def test_default_check_rejected_importable():
    from ghdag.dag import default_check_rejected
    assert callable(default_check_rejected)


def test_default_check_rejected_in_dag_all():
    import ghdag.dag
    assert "default_check_rejected" in ghdag.dag.__all__


def test_default_check_rejected_is_same_object():
    import ghdag.dag
    import ghdag.dag._util
    assert ghdag.dag.default_check_rejected is ghdag.dag._util.default_check_rejected
