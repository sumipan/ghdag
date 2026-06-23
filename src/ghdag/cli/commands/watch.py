"""ghdag watch コマンド。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import cast


def cmd_watch(args) -> None:
    """WorkflowDispatcher を構築し run() を呼ぶ薄いラッパー。"""
    from ghdag.github_client import create_github_clients
    from ghdag.pipeline.llm_pipeline import LLMPipelineAPI
    from ghdag.pipeline.order import OrderBuilder, TemplateOrderBuilder
    from ghdag.pipeline.state import PipelineState
    from ghdag.workflow.dispatcher import WorkflowDispatcher
    from ghdag.workflow.loader import load_workflows

    workflows_path = Path(args.workflows_dir)
    if not workflows_path.exists():
        print(f"error: directory not found: {workflows_path}", file=sys.stderr)
        sys.exit(1)

    workflows = load_workflows(workflows_path)
    for wf in workflows:
        wf.polling_interval = args.interval

    github_clients = create_github_clients()
    exec_jsonl_resolved = Path(args.exec_jsonl).resolve()
    queue_dir = str(exec_jsonl_resolved.parent)
    pipeline_state = PipelineState(
        state_dir=".pipeline-state",
        exec_jsonl_path=str(exec_jsonl_resolved),
    )
    order_builders: dict[str, TemplateOrderBuilder] = {
        wf.name: TemplateOrderBuilder(wf.template_dir or "templates")
        for wf in workflows
    }
    default_template_dir = next(
        (wf.template_dir for wf in workflows if wf.template_dir), "templates"
    )
    default_order_builder = TemplateOrderBuilder(default_template_dir)

    pipeline = LLMPipelineAPI(
        pipeline_state=pipeline_state,
        order_builder=default_order_builder,
        queue_dir=queue_dir,
        order_builders=cast(dict[str, OrderBuilder], order_builders),
    )
    dispatcher = WorkflowDispatcher(
        workflows=workflows,
        github_client=github_clients,
        pipeline=pipeline,
        queue_dir=queue_dir,
    )
    dispatcher.run(max_iterations=1 if args.once else None)
