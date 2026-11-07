"""Tests for ghdag.cli — AC1 - AC11.

Test approach:
- Parse validation via main(argv=[...])
- Mock DagEngine / WorkflowDispatcher; assert correct args
- Error cases assert SystemExit code
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


class TestWatchNormal:
    def test_watch_calls_dispatcher_run(self, tmp_path):
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()

        watch_state = {"workflows_loaded": False, "dispatcher_ran": False, "dispatcher_kwargs": {}}

        def fake_load(_dir):
            watch_state["workflows_loaded"] = True
            return []

        class FakeDispatcher:
            def __init__(self, **kwargs):
                watch_state["dispatcher_kwargs"] = kwargs

            def run(self, *args, **kwargs):
                watch_state["dispatcher_ran"] = True

        with patch("ghdag.workflow.loader.load_workflows", side_effect=fake_load), \
             patch("ghdag.workflow.dispatcher.WorkflowDispatcher", FakeDispatcher), \
             patch("ghdag.github_client.create_github_clients", return_value=[]), \
             patch("ghdag.pipeline.state.PipelineState"), \
             patch("ghdag.pipeline.order.TemplateOrderBuilder"):
            from ghdag.cli import main
            main(["watch", str(workflows_dir)])

        assert watch_state["workflows_loaded"] is True
        assert watch_state["dispatcher_ran"] is True

    def test_watch_builds_clients_without_rate_limit_wait(self, tmp_path):
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        create_calls: list[dict] = []

        def fake_create_github_clients(**kwargs):
            create_calls.append(kwargs)
            return []

        with patch("ghdag.workflow.loader.load_workflows", return_value=[]), \
             patch("ghdag.workflow.dispatcher.WorkflowDispatcher") as mock_disp_cls, \
             patch("ghdag.github_client.create_github_clients", side_effect=fake_create_github_clients), \
             patch("ghdag.pipeline.state.PipelineState"), \
             patch("ghdag.pipeline.order.TemplateOrderBuilder"):
            mock_disp_cls.return_value.run = lambda *a, **k: None
            from ghdag.cli import main
            main(["watch", str(workflows_dir)])

        assert len(create_calls) == 1
        assert create_calls[0] == {"rate_limit_max_wait_sec": 0}

    def test_watch_with_options(self, tmp_path):
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        exec_jsonl_path = str(tmp_path / "out.md")
        pause_file_path = str(tmp_path / "pause.txt")

        pipeline_state_kwargs = {}
        watch_state = {"dispatcher_ran": False, "dispatcher_kwargs": {}}

        class FakePipelineState:
            def __init__(self, **kwargs):
                pipeline_state_kwargs.update(kwargs)

        class FakeDispatcher:
            def __init__(self, **kwargs):
                watch_state["dispatcher_kwargs"] = kwargs

            def run(self, *args, **kwargs):
                watch_state["dispatcher_ran"] = True

        with patch("ghdag.workflow.loader.load_workflows", return_value=[]), \
             patch("ghdag.workflow.dispatcher.WorkflowDispatcher", FakeDispatcher), \
             patch("ghdag.github_client.create_github_clients", return_value=[]), \
             patch("ghdag.pipeline.state.PipelineState", FakePipelineState), \
             patch("ghdag.pipeline.order.TemplateOrderBuilder"):
            from ghdag.cli import main
            main([
                "watch", str(workflows_dir),
                "--interval", "60",
                "--exec-md", exec_jsonl_path,
                "--pause-file", pause_file_path,
            ])

        assert pipeline_state_kwargs == {
            "state_dir": ".pipeline-state",
            "exec_jsonl_path": exec_jsonl_path,
        }
        assert watch_state["dispatcher_kwargs"]["pause_file"] == pause_file_path
        assert watch_state["dispatcher_ran"] is True


# ---------------------------------------------------------------------------
# AC6: ghdag watch error cases
# ---------------------------------------------------------------------------


class TestWatchError:
    def test_watch_no_args_exits_2(self):
        from ghdag.cli import main

        with pytest.raises(SystemExit) as exc:
            main(["watch"])
        assert exc.value.code == 2

    def test_watch_nonexistent_dir_exits_1(self, capsys):
        from ghdag.cli import main

        with pytest.raises(SystemExit) as exc:
            main(["watch", "nonexistent_workflows_dir/"])
        assert exc.value.code == 1
        assert "not found" in capsys.readouterr().err


class TestWatchOrderBuilderConstruction:
    def test_watch_builds_order_builders_via_build_order_builders(self, tmp_path):
        """watch delegates order builder construction to build_order_builders (#6092)."""
        from unittest.mock import MagicMock

        from ghdag.workflow.schema import WorkflowConfig

        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        loaded = [WorkflowConfig(name="stub", triggers=[], handlers={}, polling_interval=0)]

        fake_mapping = {"stub-wf": MagicMock()}

        def fake_build(workflows):
            assert workflows is loaded
            return fake_mapping

        llm_kwargs: dict = {}

        class FakeLLMPipelineAPI:
            def __init__(self, **kwargs):
                llm_kwargs.update(kwargs)

        with patch("ghdag.workflow.loader.load_workflows", return_value=loaded), \
             patch("ghdag.pipeline.llm_pipeline.build_order_builders", side_effect=fake_build), \
             patch("ghdag.workflow.dispatcher.WorkflowDispatcher") as mock_disp_cls, \
             patch("ghdag.github_client.create_github_clients", return_value=[]), \
             patch("ghdag.pipeline.state.PipelineState"), \
             patch("ghdag.pipeline.llm_pipeline.LLMPipelineAPI", FakeLLMPipelineAPI), \
             patch("ghdag.pipeline.order.TemplateOrderBuilder"):
            mock_disp_cls.return_value.run = lambda *a, **k: None
            from ghdag.cli import main
            main(["watch", str(workflows_dir)])

        assert llm_kwargs["order_builders"] is fake_mapping
