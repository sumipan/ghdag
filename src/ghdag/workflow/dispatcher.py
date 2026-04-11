"""workflow/dispatcher.py — WorkflowDispatcher: ポーリング + イベントマッチング + exec.md 投入"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

from ghdag.pipeline.order import OrderBuilder
from ghdag.pipeline.state import PipelineState
from ghdag.workflow.github import GitHubIssueClient
from ghdag.workflow.schema import PhaseHandler, WorkflowConfig


class WorkflowDispatcher:
    """ポーリングループで GitHub Issues を監視し、トリガー条件に一致する Issue を検出して
    対応するフェーズハンドラーを exec.md 経由で実行する。
    """

    def __init__(
        self,
        workflows: list[WorkflowConfig],
        github_client: GitHubIssueClient,
        pipeline_state: PipelineState,
        order_builder: OrderBuilder,
        queue_dir: str = "queue",
    ):
        self._workflows = workflows
        self._github = github_client
        self._state = pipeline_state
        self._order_builder = order_builder
        self._queue_dir = queue_dir

    def poll_once(self) -> list[dict]:
        """1回のポーリングを実行。マッチした Issue とアクションのリストを返す。

        Returns:
            [{"issue": <number>, "workflow": <name>, "handler": <name>}, ...]
        """
        results: list[dict] = []
        for workflow in self._workflows:
            for i, trigger in enumerate(workflow.triggers):
                issues = self._github.list_issues(trigger.label)
                if i >= len(workflow.handlers):
                    continue
                handler = workflow.handlers[i]
                for issue in issues:
                    results.append(
                        {
                            "issue": issue["number"],
                            "workflow": workflow.name,
                            "handler": handler.name,
                            "_issue_data": issue,
                            "_workflow": workflow,
                            "_handler": handler,
                            "_trigger_label": trigger.label,
                        }
                    )
        return results

    def dispatch(self, issue: dict, workflow: WorkflowConfig, handler: PhaseHandler) -> None:
        """Issue に対してハンドラーを実行。

        1. PipelineState で冪等性チェック
        2. OrderBuilder でテンプレート展開
        3. PipelineState.append_exec() で exec.md に DAG 投入
        4. GitHubIssueClient.update_label() でラベル遷移 (*-ready → *-running)
        """
        issue_number = issue["number"] if isinstance(issue, dict) else issue
        idempotency_key = f"{workflow.name}:{handler.name}:{issue_number}"

        if not self._state.check_idempotency(idempotency_key):
            return

        # Build order content
        context = {
            "issue_number": str(issue_number),
            "workflow_name": workflow.name,
            "handler_name": handler.name,
        }
        order_content = self._order_builder.build_order(handler.template, context)

        # Write order file
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        order_uuid = str(uuid.uuid4())
        result_uuid = str(uuid.uuid4())
        order_filename = self._state.write_order_file(
            ts, order_uuid, order_content, self._queue_dir
        )

        # Append to exec.md (idempotency marker + task line)
        exec_lines = [
            f"# idempotency: {idempotency_key}",
            (
                f"{order_uuid}: cat {self._queue_dir}/{order_filename}"
                f" | claude -p '受け取った内容を実行して' --dangerously-skip-permissions"
                f" | tee -a {self._queue_dir}/{ts}-claude-result-{result_uuid}.md"
            ),
        ]
        self._state.append_exec(exec_lines)

        # Transition label: *-ready → *-running
        trigger_label = self._get_trigger_label(workflow, handler)
        if trigger_label and trigger_label.endswith("-ready"):
            running_label = trigger_label.replace("-ready", "-running")
            self._github.update_label(issue_number, trigger_label, running_label)

    def run(self, max_iterations: int | None = None) -> None:
        """ポーリングループを開始。max_iterations=None で無限ループ。"""
        polling_interval = (
            self._workflows[0].polling_interval if self._workflows else 30
        )
        count = 0
        while max_iterations is None or count < max_iterations:
            matches = self.poll_once()
            for match in matches:
                self.dispatch(
                    match["_issue_data"],
                    match["_workflow"],
                    match["_handler"],
                )
            count += 1
            if max_iterations is None or count < max_iterations:
                time.sleep(polling_interval)

    def _get_trigger_label(self, workflow: WorkflowConfig, handler: PhaseHandler) -> str | None:
        """handler に対応する trigger label をインデックスで返す。"""
        for i, h in enumerate(workflow.handlers):
            if h.name == handler.name and i < len(workflow.triggers):
                return workflow.triggers[i].label
        return None
