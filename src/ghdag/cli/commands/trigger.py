"""ghdag trigger コマンド。"""

from __future__ import annotations

import sys
from pathlib import Path


def cmd_trigger(args) -> None:
    """ghdag trigger: Issue に対してワンショットでハンドラーを実行する。"""
    from ghdag.pipeline.llm_pipeline import LLMPipelineAPI
    from ghdag.pipeline.order import TemplateOrderBuilder
    from ghdag.pipeline.state import PipelineState
    from ghdag.workflow.dispatcher import WorkflowDispatcher
    from ghdag.github_client import create_github_client
    from ghdag.workflow.loader import load_workflows

    workflows_path = Path(args.workflows_dir)
    if not workflows_path.exists():
        print(f"error: directory not found: {workflows_path}", file=sys.stderr)
        sys.exit(1)

    workflows = load_workflows(workflows_path)
    if not workflows:
        print("error: no workflow definitions found", file=sys.stderr)
        sys.exit(1)

    if args.workflow:
        matching = [w for w in workflows if w.name == args.workflow]
        if not matching:
            print(f"error: workflow '{args.workflow}' not found", file=sys.stderr)
            sys.exit(1)
        workflow = matching[0]
    elif len(workflows) == 1:
        workflow = workflows[0]
    else:
        names = ", ".join(w.name for w in workflows)
        print(
            f"error: multiple workflows found ({names}), specify --workflow",
            file=sys.stderr,
        )
        sys.exit(1)

    handler_name = args.handler
    if handler_name not in workflow.handlers:
        available = ", ".join(workflow.handlers.keys())
        print(
            f"error: handler '{handler_name}' not found in workflow '{workflow.name}' "
            f"(available: {available})",
            file=sys.stderr,
        )
        sys.exit(1)

    handler = workflow.handlers[handler_name]

    trigger = None
    trigger_rank = 0
    for rank, t in enumerate(workflow.triggers):
        if t.handler == handler_name:
            trigger = t
            trigger_rank = rank
            break

    github_client = create_github_client()
    exec_jsonl_resolved = Path(args.exec_jsonl).resolve()
    queue_dir = str(exec_jsonl_resolved.parent)
    pipeline_state = PipelineState(
        state_dir=".pipeline-state",
        exec_jsonl_path=str(exec_jsonl_resolved),
    )
    template_dir = workflow.template_dir or "templates"
    order_builder = TemplateOrderBuilder(template_dir)

    pipeline = LLMPipelineAPI(
        pipeline_state=pipeline_state,
        order_builder=order_builder,
        queue_dir=queue_dir,
    )
    dispatcher = WorkflowDispatcher(
        workflows=[workflow],
        github_client=github_client,
        pipeline=pipeline,
        queue_dir=queue_dir,
    )

    issue = github_client.get_issue(args.issue_number)

    result = dispatcher.dispatch(
        issue, workflow, handler, trigger=trigger, trigger_rank=trigger_rank,
    )
    print(f"trigger: issue #{args.issue_number} handler={handler_name} → {result.status}")
    if result.reason:
        print(f"  reason: {result.reason}")
    if result.status == "dispatched":
        print(f"  exec_lines: {len(result.exec_lines) - 1} step(s)")
    if result.status != "dispatched":
        sys.exit(1)
