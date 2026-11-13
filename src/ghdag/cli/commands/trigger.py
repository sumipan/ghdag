"""ghdag trigger command."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ghdag.config.env import state_dir as resolve_state_dir
from ghdag.core.exceptions import CliUsageError, ConfigError


def _resolve_trigger_client():
    """Select issue client from GHDAG_FORGE (github vs local)."""
    import os

    from ghdag.forge import get_forge

    kind = (os.environ.get("GHDAG_FORGE") or "github").strip().lower()
    if kind == "github":
        from ghdag.github_client import create_github_client

        return create_github_client()
    return get_forge()


def _load_workflow_for_cli(args, *, workflows_path: Path):
    from ghdag.workflow.loader import load_workflows

    if not workflows_path.exists():
        raise CliUsageError(
            f"directory not found: {workflows_path}",
            code="WORKFLOWS_DIR_NOT_FOUND",
            hint="pass --workflows-dir PATH",
        )

    workflows = load_workflows(workflows_path)
    if not workflows:
        raise ConfigError(
            "no workflow definitions found",
            code="WORKFLOW_NONE_FOUND",
        )

    if args.workflow:
        matching = [w for w in workflows if w.name == args.workflow]
        if not matching:
            names = ", ".join(w.name for w in workflows)
            raise CliUsageError(
                f"workflow '{args.workflow}' not found",
                code="WORKFLOW_NOT_FOUND",
                hint=f"available: {names}",
            )
        workflow = matching[0]
    elif len(workflows) == 1:
        workflow = workflows[0]
    else:
        names = ", ".join(w.name for w in workflows)
        raise CliUsageError(
            f"multiple workflows found ({names}), specify --workflow",
            code="WORKFLOW_AMBIGUOUS",
            hint=f"specify --workflow with one of: {names}",
        )

    handler_name = args.handler
    if handler_name not in workflow.handlers:
        available = ", ".join(workflow.handlers.keys())
        raise CliUsageError(
            f"handler '{handler_name}' not found in workflow '{workflow.name}'",
            code="HANDLER_NOT_FOUND",
            hint=f"available: {available}",
        )

    return workflow, handler_name


def cmd_trigger(args) -> None:
    """ghdag trigger: run a handler one-shot against an Issue."""
    from ghdag.pipeline.llm_pipeline import LLMPipelineAPI
    from ghdag.pipeline.order import TemplateOrderBuilder
    from ghdag.pipeline.state import PipelineState
    from ghdag.workflow.dispatcher import WorkflowDispatcher

    workflows_path = Path(args.workflows_dir)
    workflow, handler_name = _load_workflow_for_cli(args, workflows_path=workflows_path)
    handler = workflow.handlers[handler_name]

    trigger = None
    trigger_rank = 0
    for rank, t in enumerate(workflow.triggers):
        if t.handler == handler_name:
            trigger = t
            trigger_rank = rank
            break

    github_client = _resolve_trigger_client()
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
        issue,
        workflow,
        handler,
        trigger=trigger,
        trigger_rank=trigger_rank,
        redispatch=args.redispatch,
        redispatch_reason=args.reason,
    )

    if args.output_json:
        payload = {
            "issue": args.issue_number,
            "handler": handler_name,
            "status": result.status,
            "reason": result.reason or None,
            "exec_lines": len(result.exec_lines),
        }
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"trigger: issue #{args.issue_number} handler={handler_name} → {result.status}")
        if result.reason:
            print(f"  reason: {result.reason}")
        if result.status == "dispatched":
            print(f"  exec_lines: {len(result.exec_lines)} step(s)")

    if result.status != "dispatched":
        sys.exit(1)
