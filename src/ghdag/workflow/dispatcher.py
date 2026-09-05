"""workflow/dispatcher.py — WorkflowDispatcher: ポーリング + イベントマッチング + exec.jsonl 投入"""

from __future__ import annotations

import json
import logging
import re
import shlex
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ghdag.exceptions import GhdagError
from ghdag.github_client import GitHubIssuePort
from ghdag.pipeline.audit import (
    AuditContext,
    append_audit_record,
    write_rate_limit_audit,
)
from ghdag.pipeline.audit_query import detect_correlation_bursts
from ghdag.pipeline.llm_pipeline import LLMPipelineAPI
from ghdag.pipeline.order import OrderBuilder
from ghdag.pipeline.state import build_idempotency_key
from ghdag.workflow.render import build_live_trampoline
from ghdag.workflow.schema import (
    DispatchResult,
    HandlerConfig,
    StepConfig,
    TriggerConfig,
    WorkflowConfig,
)


class _LiveRenderOrderBuilder:
    """Wrap an OrderBuilder so ``render: live`` steps emit a trampoline order."""

    def __init__(
        self,
        inner: OrderBuilder,
        *,
        template_dir: Path,
        live_templates: set[str],
    ) -> None:
        self._inner = inner
        self._template_dir = template_dir
        self._live_templates = live_templates

    def build_order(self, step_id: str, context: dict[str, str]) -> str:
        if step_id not in self._live_templates:
            return self._inner.build_order(step_id, context)
        template_path = (self._template_dir / f"{step_id}.md").resolve()
        return build_live_trampoline(template_path, context)

_READY_LABEL_RE = re.compile(r"^(.+)([-:])ready$")

logger = logging.getLogger(__name__)

_RATE_LIMIT_THRESHOLD = 100
_BURST_WINDOW_SEC = 600
_BURST_THRESHOLD = 10
_BURST_COOLDOWN_SEC = 3600
_PAUSE_REASON_MAX_CHARS = 500
_JST = timezone(timedelta(hours=9))


class ContextHookError(GhdagError, ValueError):
    """Raised when context_hook stdout is not valid JSON."""


class WorkflowDispatcher:
    """ポーリングループで GitHub Issues を監視し、トリガー条件に一致する Issue を検出して
    対応するハンドラーを exec.jsonl 経由で実行する。
    """

    def __init__(
        self,
        workflows: list[WorkflowConfig],
        github_client: GitHubIssuePort | list[GitHubIssuePort],
        pipeline: LLMPipelineAPI,
        queue_dir: str = "queue",
        pause_file: str | Path | None = None,
    ):
        self._workflows = workflows
        # 単一クライアントとクライアントのリストの両方を受け付ける。
        # 複数リポ watch 時はリポジトリごとのクライアントを渡す。
        if isinstance(github_client, (list, tuple)):
            self._githubs: list[GitHubIssuePort] = list(github_client)
        else:
            self._githubs = [github_client]
        self._pipeline = pipeline
        self._queue_dir = queue_dir
        self._burst_warned: dict[str, float] = {}
        self._pause_file = Path(pause_file) if pause_file is not None else None
        self._paused = False

    def poll_once(self) -> list[dict]:
        """1回のポーリングを実行。マッチした Issue とアクションのリストを返す。

        各 trigger の評価は独立しており、ある trigger で list_issues が失敗しても
        他の trigger の評価は続行する（per-trigger exception isolation）。失敗した
        trigger は warning ログを出した上でスキップする。これにより、ある workflow
        の過渡的な GitHub API 失敗が、別 workflow のディスパッチを巻き添えで停止させる
        事象を防ぐ。

        Returns:
            [{"issue": <number>, "workflow": <name>, "handler": <name>, ...}, ...]
        """
        results: list[dict] = []
        for github in self._githubs:
            for workflow in self._workflows:
                for trigger_rank, trigger in enumerate(workflow.triggers):
                    handler_name = trigger.handler
                    if handler_name not in workflow.handlers:
                        continue
                    try:
                        issues = github.list_issues(trigger.label)
                    except Exception as exc:
                        logger.warning(
                            "poll_once: trigger label=%r in workflow=%r failed "
                            "(%s: %s) — skipping this trigger and continuing with others",
                            trigger.label,
                            workflow.name,
                            type(exc).__name__,
                            exc,
                        )
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
                                "_github": github,
                            }
                        )
                if workflow.nonterminal_closed is not None:
                    self._poll_nonterminal_closed(github, workflow, results)
        return results

    def dispatch(
        self,
        issue: dict,
        workflow: WorkflowConfig,
        handler: HandlerConfig,
        trigger: TriggerConfig | None = None,
        trigger_rank: int | None = None,
        github: GitHubIssuePort | None = None,
        *,
        redispatch: bool = False,
        redispatch_reason: str | None = None,
    ) -> DispatchResult:
        """Issue に対してハンドラーを実行。

        Args:
            issue: GitHub Issue dict
            workflow: WorkflowConfig
            handler: HandlerConfig
            trigger: 対応する TriggerConfig（省略時は workflow から解決）
            trigger_rank: triggers リスト内の序列（省略時は workflow から解決）
            github: この Issue を取得したクライアント（省略時は先頭クライアント）
            redispatch: True のとき世代を +1 して新しい run を開始する
            redispatch_reason: redispatch 時の理由（audit.jsonl に記録）

        Returns:
            DispatchResult: status が "dispatched" | "skipped" | "reset"
        """
        github = github if github is not None else self._githubs[0]
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
            if trigger is None:
                return DispatchResult(status="skipped", reason="no trigger for reset")
            self._handle_reset(issue, workflow, trigger, github)
            return DispatchResult(status="reset", reason="reset handler")

        # 3. 冪等性チェック
        handler_name = trigger.handler if trigger else ""
        generation = 0
        if redispatch:
            generation = self._pipeline.increment_generation(
                workflow.name, handler_name, issue_number,
            )
            reason = redispatch_reason if redispatch_reason else "(no reason)"
            self._append_redispatch_audit(
                workflow_name=workflow.name,
                handler_name=handler_name,
                issue_number=issue_number,
                generation=generation,
                reason=reason,
            )
        else:
            generation = self._pipeline.get_generation(
                workflow.name, handler_name, issue_number,
            )

        idempotency_key = build_idempotency_key(
            workflow.name, handler_name, issue_number, generation,
        )
        if not self._pipeline.check_idempotency(idempotency_key):
            logger.warning(
                "dispatch skipped (already dispatched) — issue=#%d, handler=%s, key=%s\n"
                "  → To retry failed steps: ghdag dag recover --issue %d --handler %s --dry-run\n"
                "  → To start a new run:    ghdag trigger --issue %d --handler %s --redispatch --reason \"...\"",
                issue_number,
                handler_name,
                idempotency_key,
                issue_number,
                handler_name,
                issue_number,
                handler_name,
            )
            return DispatchResult(status="skipped", reason="already dispatched")

        # 4. Issue コンテキスト取得
        if handler.on_trigger and handler.on_trigger.issue_context:
            self._write_design_md(issue, github)

        # 4b. context_hook 実行
        base_context: dict[str, str] = {
            "issue_number": str(issue_number),
            "workflow_name": workflow.name,
            "handler_name": handler_name,
        }
        if handler.context_hook:
            base_context.update(self._run_context_hook(handler.context_hook, issue_number))

        # 5. パイプライン投入（render: live は trampoline をインラインオーダーとして渡す）
        audit_ctx = AuditContext(source=workflow.name, correlation_id=idempotency_key)
        exec_lines = self._submit_steps(
            workflow=workflow,
            steps=handler.steps,
            base_context=base_context,
            idempotency_key=idempotency_key,
            audit_ctx=audit_ctx,
        )

        # 6. ラベル遷移（*-ready / *:ready → *-running / *:running）
        if trigger:
            m = _READY_LABEL_RE.match(trigger.label)
            if m:
                running_label = f"{m.group(1)}{m.group(2)}running"
                github.update_label(issue_number, trigger.label, running_label)

        return DispatchResult(status="dispatched", exec_lines=exec_lines)

    def _submit_steps(
        self,
        *,
        workflow: WorkflowConfig,
        steps: list[StepConfig],
        base_context: dict[str, str],
        idempotency_key: str,
        audit_ctx: AuditContext,
    ) -> list[str]:
        """Submit steps, wrapping the order builder when any step uses render: live."""
        live_templates = {s.template for s in steps if s.render == "live"}
        if not live_templates:
            return self._pipeline.submit(
                steps=steps,
                base_context=base_context,
                idempotency_key=idempotency_key,
                audit_context=audit_ctx,
            )

        template_dir = Path(workflow.template_dir) if workflow.template_dir else Path("templates")
        inner = self._pipeline._resolve_order_builder(workflow.name)
        wrapped = _LiveRenderOrderBuilder(
            inner,
            template_dir=template_dir,
            live_templates=live_templates,
        )
        builders = self._pipeline._order_builders
        had_entry = workflow.name in builders
        previous = builders.get(workflow.name)
        builders[workflow.name] = wrapped
        try:
            return self._pipeline.submit(
                steps=steps,
                base_context=base_context,
                idempotency_key=idempotency_key,
                audit_context=audit_ctx,
            )
        finally:
            if had_entry:
                builders[workflow.name] = previous  # type: ignore[assignment]
            else:
                builders.pop(workflow.name, None)

    def run(self, max_iterations: int | None = None) -> None:
        """ポーリングループを開始。max_iterations=None で無限ループ。"""
        polling_interval = (
            self._workflows[0].polling_interval if self._workflows else 30
        )
        count = 0
        while max_iterations is None or count < max_iterations:
            if self._pause_file is not None and self._pause_file.exists():
                if not self._paused:
                    reason = self._read_pause_reason(self._pause_file)
                    logger.info("dispatcher paused: pause_file=%s", self._pause_file)
                    self._append_dispatcher_audit_event(
                        event="dispatcher_pause",
                        reason=reason,
                    )
                    self._paused = True
                count += 1
                if max_iterations is None or count < max_iterations:
                    time.sleep(polling_interval)
                continue

            if self._pause_file is not None and self._paused:
                logger.info("dispatcher resumed: pause_file removed: %s", self._pause_file)
                self._append_dispatcher_audit_event(
                    event="dispatcher_resume",
                    reason="pause file removed",
                )
                self._paused = False

            matches = self.poll_once()
            self._observe_rate_limit()
            self._observe_correlation_burst()
            for match in matches:
                github = match.get("_github") or self._githubs[0]
                try:
                    self.dispatch(
                        match["_issue_data"],
                        match["_workflow"],
                        match["_handler"],
                        trigger=match["_trigger"],
                        trigger_rank=match["_trigger_rank"],
                        github=github,
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

    def _append_redispatch_audit(
        self,
        *,
        workflow_name: str,
        handler_name: str,
        issue_number: int,
        generation: int,
        reason: str,
    ) -> None:
        audit_path = Path(self._queue_dir) / "audit.jsonl"
        record = {
            "timestamp": datetime.now(_JST).isoformat(),
            "schema_version": 1,
            "event_type": "redispatch",
            "workflow": workflow_name,
            "handler": handler_name,
            "issue_number": issue_number,
            "generation": generation,
            "reason": reason,
        }
        try:
            append_audit_record(audit_path, record)
        except OSError:
            logger.debug("redispatch audit write failed", exc_info=True)

    def _append_dispatcher_audit_event(self, *, event: str, reason: str) -> None:
        audit_path = Path(self._queue_dir) / "audit.jsonl"
        record = {
            "timestamp": datetime.now(_JST).isoformat(),
            "schema_version": 1,
            "event": event,
            "reason": reason,
        }
        try:
            append_audit_record(audit_path, record)
        except OSError:
            logger.debug("dispatcher audit write failed", exc_info=True)

    def _read_pause_reason(self, pause_file: Path) -> str:
        try:
            reason = pause_file.read_text(errors="replace")
        except OSError:
            return "pause file read failed"
        return reason[:_PAUSE_REASON_MAX_CHARS]

    def _observe_rate_limit(self) -> None:
        """各クライアントの GitHub API rate limit を取得し、audit.jsonl に記録する。"""
        for github in self._githubs:
            rate = github.get_rate_limit()
            if rate is None:
                continue
            remaining = rate.get("remaining")
            limit = rate.get("limit")
            reset = rate.get("reset")
            if remaining is None or limit is None or reset is None:
                continue

            audit_path = Path(self._queue_dir) / "audit.jsonl"
            write_rate_limit_audit(
                audit_path,
                remaining=remaining,
                limit=limit,
                reset=reset,
            )
            if remaining <= _RATE_LIMIT_THRESHOLD:
                logger.warning(
                    "GitHub API rate limit low: %d/%d remaining (resets at %d)",
                    remaining, limit, reset,
                )

    def _observe_correlation_burst(self) -> None:
        """audit.jsonl から correlation_id バーストを検出し warning を出力する。"""
        try:
            audit_path = Path(self._queue_dir) / "audit.jsonl"
            bursts = detect_correlation_bursts(
                audit_path,
                window_sec=_BURST_WINDOW_SEC,
                threshold=_BURST_THRESHOLD,
            )
            now = time.time()
            for burst in bursts:
                cid = burst["correlation_id"]
                if now - self._burst_warned.get(cid, 0) <= _BURST_COOLDOWN_SEC:
                    continue
                logger.warning(
                    "Correlation burst detected: %s (%d events in %ds, latest=%s)",
                    cid,
                    burst["count"],
                    _BURST_WINDOW_SEC,
                    burst["latest_timestamp"],
                )
                self._burst_warned[cid] = now
        except Exception:
            logger.debug("correlation burst observation failed", exc_info=True)

    # --- internal helpers ---

    def _poll_nonterminal_closed(
        self,
        github: GitHubIssuePort,
        workflow: WorkflowConfig,
        results: list[dict],
    ) -> None:
        """CLOSED かつ非終端ラベルの issue を検出し reopen / trigger action を実行する。"""
        config = workflow.nonterminal_closed
        if config is None:
            return

        terminal_labels = set(config.terminal_labels)
        action = config.action

        for trigger in workflow.triggers:
            try:
                issues = github.list_issues(trigger.label, state="closed")
            except Exception as exc:
                logger.warning(
                    "poll_once: nonterminal_closed scan for label=%r in workflow=%r failed "
                    "(%s: %s) — skipping this trigger and continuing with others",
                    trigger.label,
                    workflow.name,
                    type(exc).__name__,
                    exc,
                )
                continue

            for issue in issues:
                issue_number = issue["number"]
                issue_label_names = {lb["name"] for lb in issue.get("labels", [])}
                if issue_label_names & terminal_labels:
                    continue

                try:
                    comments = github.get_issue_comments(issue_number)
                except Exception as exc:
                    logger.warning(
                        "poll_once: get_issue_comments failed for issue #%d (%s: %s) — skipping",
                        issue_number,
                        type(exc).__name__,
                        exc,
                    )
                    continue

                if self._has_nonterminal_closed_marker(comments, action, issue_number):
                    continue

                if action == "reopen":
                    try:
                        github.reopen_issue(issue_number)
                        github.add_comment(
                            issue_number,
                            self._nonterminal_closed_comment(action, issue_number, "reopened"),
                        )
                    except Exception as exc:
                        logger.warning(
                            "poll_once: reopen failed for issue #%d (%s: %s) — skipping",
                            issue_number,
                            type(exc).__name__,
                            exc,
                        )
                    continue

                if action == "trigger":
                    trigger_label = config.trigger
                    if not trigger_label:
                        logger.warning(
                            "poll_once: nonterminal_closed trigger action missing trigger label "
                            "in workflow=%r — skipping issue #%d",
                            workflow.name,
                            issue_number,
                        )
                        continue

                    handler_trigger, handler_rank, handler = self._resolve_trigger_by_label(
                        workflow, trigger_label,
                    )
                    if handler is None or handler_trigger is None or handler_rank is None:
                        logger.warning(
                            "poll_once: nonterminal_closed trigger label=%r not found in workflow=%r "
                            "— skipping issue #%d",
                            trigger_label,
                            workflow.name,
                            issue_number,
                        )
                        continue

                    try:
                        github.add_comment(
                            issue_number,
                            self._nonterminal_closed_comment(
                                action,
                                issue_number,
                                f"triggering handler {handler_trigger.handler}",
                            ),
                        )
                    except Exception as exc:
                        logger.warning(
                            "poll_once: add_comment failed for issue #%d (%s: %s) — skipping",
                            issue_number,
                            type(exc).__name__,
                            exc,
                        )
                        continue

                    results.append(
                        {
                            "issue": issue_number,
                            "workflow": workflow.name,
                            "handler": handler_trigger.handler,
                            "_issue_data": issue,
                            "_workflow": workflow,
                            "_handler": handler,
                            "_trigger": handler_trigger,
                            "_trigger_rank": handler_rank,
                            "_github": github,
                        }
                    )

    @staticmethod
    def _nonterminal_closed_marker(action: str, issue_number: int) -> str:
        return f"<!-- ghdag:nonterminal_closed:{action}:{issue_number} -->"

    @classmethod
    def _has_nonterminal_closed_marker(
        cls,
        comments: list[dict],
        action: str,
        issue_number: int,
    ) -> bool:
        marker = cls._nonterminal_closed_marker(action, issue_number)
        return any(marker in (comment.get("body") or "") for comment in comments)

    @staticmethod
    def _nonterminal_closed_comment(action: str, issue_number: int, detail: str) -> str:
        return (
            f"⚠️ Nonterminal closed issue detected: {detail} (labels unchanged).\n"
            f"{WorkflowDispatcher._nonterminal_closed_marker(action, issue_number)}"
        )

    @staticmethod
    def _resolve_trigger_by_label(
        workflow: WorkflowConfig,
        trigger_label: str,
    ) -> tuple[TriggerConfig | None, int | None, HandlerConfig | None]:
        for rank, trigger in enumerate(workflow.triggers):
            if trigger.label != trigger_label:
                continue
            handler = workflow.handlers.get(trigger.handler)
            if handler is None:
                return trigger, rank, None
            return trigger, rank, handler
        return None, None, None

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
            m = _READY_LABEL_RE.match(trigger.label)
            if not m:
                continue
            running_label = f"{m.group(1)}{m.group(2)}running"
            if running_label in issue_label_names:
                if max_rank is None or rank > max_rank:
                    max_rank = rank

        return max_rank

    def _write_design_md(self, issue: dict, github: GitHubIssuePort) -> None:
        """Issue body + comments を queue/issue-{N}-design.md に書き出す。"""
        issue_number = issue["number"]
        comments = github.get_issue_comments(issue_number)

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
        self,
        issue: dict,
        workflow: WorkflowConfig,
        trigger: TriggerConfig,
        github: GitHubIssuePort,
    ) -> None:
        """冪等キー削除 + トリガーラベルと同プレフィックスのラベルをすべてクリア。"""
        issue_number = issue["number"]

        # 冪等キー削除
        self._pipeline.remove_idempotency_matching(workflow.name, issue_number)

        # ラベルプレフィックス: label_namespace 優先、未設定時は trigger.label から抽出
        if workflow.label_namespace:
            prefix = workflow.label_namespace + ":"
        elif ":" in trigger.label:
            prefix = trigger.label.rsplit(":", 1)[0] + ":"
        else:
            prefix = ""

        # 同プレフィックスのラベルをすべて除去
        if prefix:
            issue_label_names = [lb["name"] for lb in issue.get("labels", [])]
            for label in issue_label_names:
                if label.startswith(prefix):
                    github.remove_label(issue_number, label)

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
            raise ContextHookError(
                f"context_hook stdout is not valid JSON: {e}"
            ) from e

        return {str(k): str(v) for k, v in data.items()}
