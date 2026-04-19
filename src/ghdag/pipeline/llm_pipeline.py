"""
pipeline/llm_pipeline.py — LLMPipelineAPI: order/result/exec.md 投入を一括で担う

dispatcher は submit() を呼ぶだけで、ファイル命名規則や
exec 行フォーマットを知る必要がない。
"""

from __future__ import annotations

import shlex
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from ghdag.llm.engines import build_llm_cmd
from ghdag.pipeline.order import OrderBuilder
from ghdag.pipeline.state import PipelineState

if TYPE_CHECKING:
    from ghdag.workflow.schema import StepConfig


@dataclass
class SubmittedStep:
    """submit() の戻り値に含まれる、投入済みステップの情報。"""
    step_id: str
    uuid: str
    order_filename: str
    result_filename: str
    exec_line: str


class LLMPipelineAPI:
    """order/result ファイル管理と exec.md 投入を一括で担う。

    dispatcher は submit() を呼ぶだけで、ファイル命名規則や
    exec 行フォーマットを知る必要がない。
    """

    def __init__(
        self,
        pipeline_state: PipelineState,
        order_builder: OrderBuilder,
        queue_dir: str = "queue",
    ):
        self._state = pipeline_state
        self._order_builder = order_builder
        self._queue_dir = queue_dir

    def check_idempotency(self, key: str) -> bool:
        """冪等性チェックを PipelineState に委譲する。"""
        return self._state.check_idempotency(key)

    def remove_idempotency_matching(self, workflow_name: str, issue_number: int) -> None:
        """冪等キー削除を PipelineState に委譲する。"""
        self._state.remove_idempotency_matching(workflow_name, issue_number)

    def submit(
        self,
        steps: list[StepConfig],
        base_context: dict[str, str],
        *,
        idempotency_key: str | None = None,
    ) -> list[str]:
        """ステップ群を order/exec.md に投入する。

        Args:
            steps: 実行する StepConfig のリスト
            base_context: 全ステップ共通のコンテキスト変数
                          （issue_number, workflow_name 等）
            idempotency_key: exec.md に記録する冪等性キー（省略時は記録しない）

        Returns:
            exec.md に追記された行のリスト（DispatchResult 用）
        """
        ts = datetime.now(tz=ZoneInfo("Asia/Tokyo")).strftime("%Y%m%d%H%M%S")
        exec_lines: list[str] = []

        if idempotency_key:
            exec_lines.append(f"# idempotency: {idempotency_key}")

        step_uuid_map: dict[str, str] = {}    # step_id -> uuid
        step_engine_map: dict[str, str] = {}  # step_id -> engine

        for step in steps:
            step_uuid = str(uuid.uuid4())
            engine = step.agent
            step_id = step.id if step.id else step_uuid
            step_uuid_map[step_id] = step_uuid
            step_engine_map[step_id] = engine

            result_filename = f"{ts}-{engine}-result-{step_uuid}.md"

            # context: base_context + step-specific + dep result filenames
            context = dict(base_context)
            context.update({
                "ts": ts,
                "order_uuid": step_uuid,
                "result_uuid": step_uuid,
                "result_filename": result_filename,
            })

            for dep_id in step.depends:
                if dep_id in step_uuid_map:
                    dep_uuid = step_uuid_map[dep_id]
                    dep_engine = step_engine_map[dep_id]
                    context[f"{dep_id}_result_filename"] = (
                        f"{ts}-{dep_engine}-result-{dep_uuid}.md"
                    )

            order_content = self._order_builder.build_order(step.template, context)
            order_filename = self._state.write_order_file(
                ts, step_uuid, order_content, self._queue_dir, engine=engine
            )

            exec_line = self._build_exec_line(
                step_uuid=step_uuid,
                depends=[step_uuid_map[d] for d in step.depends if d in step_uuid_map],
                order_filename=order_filename,
                result_filename=result_filename,
                engine=engine,
                model=step.model,
            )
            exec_lines.append(exec_line)

        self._state.append_exec(exec_lines)
        return exec_lines

    def _build_exec_line(
        self,
        *,
        step_uuid: str,
        depends: list[str],
        order_filename: str,
        result_filename: str,
        engine: str,
        model: str,
    ) -> str:
        """exec.md の 1 行を構築する（内部メソッド）。"""
        dep_str = f"[depends:{','.join(depends)}]" if depends else ""
        cmd_parts = build_llm_cmd(
            engine=engine,
            model=model,
            prompt="受け取った内容を実行して",
            dangerously_skip_permissions=True,
        )
        cmd_str = " ".join(shlex.quote(p) for p in cmd_parts)
        return (
            f"{step_uuid}{dep_str}: cat {self._queue_dir}/{order_filename}"
            f" | {cmd_str}"
            f" | tee -a {self._queue_dir}/{result_filename}"
        )
