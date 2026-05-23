"""Tests for public API surface and py.typed marker (issue-1064)."""

import pathlib
import importlib


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
