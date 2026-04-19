"""workflow/dispatcher.py — WorkflowDispatcher: ポーリング + イベントマッチング + exec.md 投入"""

from __future__ import annotations

import json
import logging
import shlex
import subprocess
import time
from pathlib import Path

from ghdag.pipeline.llm_pipeline import LLMPipelineAPI
from ghdag.workflow.github import GitHubIssueClient
from ghdag.workflow.schema import (
    DispatchResult,
    HandlerConfig,
    TriggerConfig,
    WorkflowConfig,
)

logger = logging.getLogger(__name__)


class WorkflowDispatcher:
    """ポーリングループで GitHub Issues を監視し、トリガー条件に一致する Issue を検出して
    対応するハンドラーを exec.md 経由で実行する。
    """

    def __init__(
        self,
        workflows: list[WorkflowConfig],
        github_client: GitHubIssueClient,
        pipeline: LLMPipelineAPI,
        queue_dir: str = "queue",
    ):
        self._workflows = workflows
        self._github = github_client
        self._pipeline = pipeline
        self._queue_dir = queue_dir

    def poll_once(self) -> list[dict]:
        """1回のポーリングを実行。マッチした Issue とアクションのリストを返す。

        Returns:
            [{"issue": <number>, "workflow": <name>, "handler": <name>, ...}, ...]
        """
        results: list[dict] = []
        for workflow in self._workflows:
            for trigger_rank, trigger in enumerate(workflow.triggers):
                issues = self._github.list_issues(trigger.label)
                handler_name = trigger.handler
                if handler_name not in workflow.handlers:
                    continue
                handler = workflow.handlers[handler_name]
                for issue in issues:
                    results.append(
                        {
                            "issue": issue["number"],
                            "workflow": workflow.name,
                            "handler": handler_name,
                            "_issue_data": issue,
                            "_workflow": workflow,
                            "_handler": handler,
                            "_trigger": trigger,
                            "_trigger_rank": trigger_rank,
                        }
                    )
        return results

    def dispatch(
        self,
        issue: dict,
        workflow: WorkflowConfig,
        handler: HandlerConfig,
        trigger: TriggerConfig | None = None,
        trigger_rank: int | None = None,
    ) -> DispatchResult:
        """Issue に対してハンドラーを実行。

        Args:
            issue: GitHub Issue dict
            workflow: WorkflowConfig
            handler: HandlerConfig
            trigger: 対応する TriggerConfig（省略時は workflow から解決）
            trigger_rank: triggers リスト内の序列（省略時は workflow から解決）

        Returns:
            DispatchResult: status が "dispatched" | "skipped" | "reset"
        """
        issue_number = issue["number"] if isinstance(issue, dict) else issue

        # trigger / trigger_rank を解決
        if trigger is None or trigger_rank is None:
            trigger, trigger_rank = self._resolve_trigger(workflow, handler)

        # 1. 後退遷移ガード
        current_running_rank = self._get_current_running_rank(issue, workflow)
        if current_running_rank is not None and trigger_rank <= current_running_rank:
            logger.info(
                "Backward transition blocked: issue #%d trigger_rank=%d running_rank=%d",
                issue_number, trigger_rank, current_running_rank,
            )
            return DispatchResult(status="skipped", reason="backward transition")

        # 2. reset ハンドラー
        if handler.type == "reset":
            self._handle_reset(issue, workflow, trigger)
            return DispatchResult(status="reset", reason="reset handler")

        # 3. 冪等性チェック
        handler_name = trigger.handler if trigger else ""
        idempotency_key = f"{workflow.name}:{handler_name}:{issue_number}"
        if not self._pipeline._state.check_idempotency(idempotency_key):
            return DispatchResult(status="skipped", reason="already dispatched")

        # 4. Issue コンテキスト取得
        if handler.on_trigger and handler.on_trigger.issue_context:
            self._write_design_md(issue)

        # 4b. context_hook 実行
        base_context: dict[str, str] = {
            "issue_number": str(issue_number),
            "workflow_name": workflow.name,
            "handler_name": handler_name,
        }
        if handler.context_hook:
            base_context.update(self._run_context_hook(handler.context_hook, issue_number))

        # 5. パイプライン投入
        exec_lines = self._pipeline.submit(
            steps=handler.steps,
            base_context=base_context,
            idempotency_key=idempotency_key,
        )

        # 6. ラベル遷移（*-ready → *-running）
        if trigger and trigger.label.endswith("-ready"):
            running_label = trigger.label.replace("-ready", "-running")
            self._github.update_label(issue_number, trigger.label, running_label)

        return DispatchResult(status="dispatched", exec_lines=exec_lines)

    def run(self, max_iterations: int | None = None) -> None:
        """ポーリングループを開始。max_iterations=None で無限ループ。"""
        polling_interval = (
            self._workflows[0].polling_interval if self._workflows else 30
        )
        count = 0
        while max_iterations is None or count < max_iterations:
            matches = self.poll_once()
            for match in matches:
                try:
                    self.dispatch(
                        match["_issue_data"],
                        match["_workflow"],
                        match["_handler"],
                        trigger=match["_trigger"],
                        trigger_rank=match["_trigger_rank"],
                    )
                except Exception:
                    issue_number = match["_issue_data"].get("number", "?")
                    handler_name = match.get("handler", "?")
                    logger.exception(
                        "dispatch failed: issue #%s handler=%s — skipping",
                        issue_number,
                        handler_name,
                    )
            count += 1
            if max_iterations is None or count < max_iterations:
                time.sleep(polling_interval)

    # --- internal helpers ---

    def _resolve_trigger(
        self, workflow: WorkflowConfig, handler: HandlerConfig
    ) -> tuple[TriggerConfig | None, int]:
        """handler に対応する trigger と rank を workflow から解決する。"""
        for rank, trigger in enumerate(workflow.triggers):
            if trigger.handler in workflow.handlers:
                if workflow.handlers[trigger.handler] is handler:
                    return trigger, rank
        return None, 0

    def _get_current_running_rank(self, issue: dict, workflow: WorkflowConfig) -> int | None:
        """Issue の現在 -running ラベルのうち最大序列を返す。なければ None。"""
        issue_label_names = {lb["name"] for lb in issue.get("labels", [])}
        max_rank: int | None = None

        for rank, trigger in enumerate(workflow.triggers):
            if not trigger.label.endswith("-ready"):
                continue
            running_label = trigger.label.replace("-ready", "-running")
            if running_label in issue_label_names:
                if max_rank is None or rank > max_rank:
                    max_rank = rank

        return max_rank

    def _write_design_md(self, issue: dict) -> None:
        """Issue body + comments を queue/issue-{N}-design.md に書き出す。"""
        issue_number = issue["number"]
        comments = self._github.get_issue_comments(issue_number)

        lines = [f"# Issue #{issue_number}: {issue.get('title', '')}", ""]
        body = issue.get("body") or ""
        if body:
            lines += [body, ""]

        for comment in comments:
            author = comment.get("author", "")
            created_at = comment.get("created_at", "")
            body_c = comment.get("body", "")
            lines += [f"### {author} ({created_at})", "", body_c, ""]

        design_path = Path(self._queue_dir) / f"issue-{issue_number}-design.md"
        design_path.write_text("\n".join(lines), encoding="utf-8")

    def _handle_reset(
        self, issue: dict, workflow: WorkflowConfig, trigger: TriggerConfig
    ) -> None:
        """冪等キー削除 + トリガーラベルと同プレフィックスのラベルをすべてクリア。"""
        issue_number = issue["number"]

        # 冪等キー削除
        self._pipeline._state.remove_idempotency_matching(workflow.name, issue_number)

        # トリガーラベルからプレフィックスを抽出（例: "issuesmith:reset" → "issuesmith:"）
        prefix = trigger.label.rsplit(":", 1)[0] + ":" if ":" in trigger.label else ""

        # 同プレフィックスのラベルをすべて除去
        if prefix:
            issue_label_names = [lb["name"] for lb in issue.get("labels", [])]
            for label in issue_label_names:
                if label.startswith(prefix):
                    self._github.remove_label(issue_number, label)

    def _run_context_hook(
        self, hook_cmd: str, issue_number: int, *, timeout: int = 30
    ) -> dict[str, str]:
        """context_hook コマンドを実行し、stdout の JSON を dict として返す。

        Args:
            hook_cmd: シェルコマンド文字列（shlex.split で分割）
            issue_number: Issue 番号（引数として渡す）
            timeout: タイムアウト秒数
        Returns:
            hook の stdout を JSON パースした dict（全値を str に変換）
        Raises:
            subprocess.TimeoutExpired: hook がタイムアウト
            ValueError: stdout が有効な JSON でない
        """
        full_cmd = f"{hook_cmd} {shlex.quote(str(issue_number))}"
        logger.info("Running context_hook: %s", full_cmd)
        result = subprocess.run(
            full_cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            logger.warning(
                "context_hook failed (rc=%d): %s",
                result.returncode,
                result.stderr.strip(),
            )
            return {}

        stdout = result.stdout.strip()
        if not stdout:
            return {}

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"context_hook の stdout が有効な JSON ではありません: {e}"
            ) from e

        return {str(k): str(v) for k, v in data.items()}
