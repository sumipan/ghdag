"""Tests for public API surface and py.typed marker (issue-1064)."""

import pathlib


def test_py_typed_marker_exists():
    import ghdag
    p = pathlib.Path(ghdag.__file__).parent / "py.typed"
    assert p.exists(), "py.typed marker missing — PEP 561 compliance required"


def test_llm_pipeline_api_importable():
    from ghdag import LLMPipelineAPI
    assert LLMPipelineAPI is not None


def test_pipeline_state_importable():
    from ghdag import PipelineState
    assert PipelineState is not None


def test_dag_engine_importable():
    from ghdag import DagEngine
    assert DagEngine is not None


def test_workflow_dispatcher_importable():
    from ghdag import WorkflowDispatcher
    assert WorkflowDispatcher is not None


def test_all_new_symbols_in_dunder_all():
    import ghdag
    expected = {"LLMPipelineAPI", "PipelineState", "DagEngine", "WorkflowDispatcher"}
    missing = expected - set(ghdag.__all__)
    assert not missing, f"Missing from __all__: {missing}"


def test_existing_symbols_still_in_dunder_all():
    import ghdag
    existing = {"QueueTask", "QueueTaskStore"}
    missing = existing - set(ghdag.__all__)
    assert not missing, f"Existing symbols removed from __all__: {missing}"


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
