"""ghdag watch command."""

from __future__ import annotations

from pathlib import Path

from ghdag.config.env import state_dir as resolve_state_dir
from ghdag.core.exceptions import CliUsageError


def _resolve_watch_clients():
    """Select issue clients from GHDAG_FORGE (github vs local)."""
    import os

    from ghdag.forge import get_forge

    kind = (os.environ.get("GHDAG_FORGE") or "github").strip().lower()
    if kind == "github":
        from ghdag.github_client import create_github_clients

        return create_github_clients(rate_limit_max_wait_sec=0)
    return [get_forge()]


def cmd_watch(args) -> None:
    """Thin wrapper that builds a WorkflowDispatcher and calls run()."""
    from ghdag.pipeline.llm_pipeline import LLMPipelineAPI, build_order_builders
    from ghdag.pipeline.order import TemplateOrderBuilder
    from ghdag.pipeline.state import PipelineState
    from ghdag.workflow.dispatcher import WorkflowDispatcher
    from ghdag.workflow.loader import load_workflows

    workflows_path = Path(args.workflows_dir)
    if not workflows_path.exists():
        raise CliUsageError(
            f"directory not found: {workflows_path}",
            code="WORKFLOWS_DIR_NOT_FOUND",
            hint="pass --workflows-dir PATH",
        )

    workflows = load_workflows(workflows_path)
    for wf in workflows:
        wf.polling_interval = args.interval

    github_clients = _resolve_watch_clients()
    exec_jsonl_resolved = Path(args.exec_jsonl).resolve()
    queue_dir = str(exec_jsonl_resolved.parent)
    if isinstance(args.state_dir, str):
        state_dir = args.state_dir
    elif exec_jsonl_resolved.parent.name == "jobs":
        state_dir = str(resolve_state_dir(exec_jsonl_resolved.parent.parent) / ".pipeline-state")
    else:
        state_dir = str(resolve_state_dir(".") / ".pipeline-state")
    pipeline_state = PipelineState(
        state_dir=state_dir,
        exec_jsonl_path=str(exec_jsonl_resolved),
    )
    order_builders = build_order_builders(workflows)
    default_template_dir = next(
        (wf.template_dir for wf in workflows if wf.template_dir), "templates"
    )
    default_order_builder = TemplateOrderBuilder(default_template_dir)

    pipeline = LLMPipelineAPI(
        pipeline_state=pipeline_state,
        order_builder=default_order_builder,
        queue_dir=queue_dir,
        order_builders=order_builders,
    )
    dispatcher = WorkflowDispatcher(
        workflows=workflows,
        workflows_dir=workflows_path,
        github_client=github_clients,
        pipeline=pipeline,
        queue_dir=queue_dir,
        pause_file=args.pause_file,
    )
    dispatcher.run(max_iterations=1 if args.once else None)
