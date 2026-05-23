"""
pipeline/llm_pipeline.py — LLMPipelineAPI: order/result/exec.md 投入を一括で担う

dispatcher は submit() を呼ぶだけで、ファイル命名規則や
exec 行フォーマットを知る必要がない。
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from ghdag.pipeline.audit import AuditContext
from ghdag.pipeline.order import OrderBuilder
from ghdag.pipeline.state import PipelineState

if TYPE_CHECKING:
    from ghdag.workflow.schema import StepConfig


def _validate_depends(steps: "list[StepConfig]") -> None:
    """depends の事前検証: 未定義参照と循環参照を検出する。

    Raises:
        ValueError: 未定義の dep_id が存在する場合、または循環参照がある場合
    """
    step_ids = {s.id for s in steps if s.id is not None}

    for step in steps:
        for dep_id in step.depends:
            if dep_id not in step_ids:
                raise ValueError(f"Unknown dependency: {dep_id!r}")

    # トポロジカルソートで循環参照を検出
    in_degree: dict[str, int] = {s.id: 0 for s in steps if s.id is not None}
    adjacency: dict[str, list[str]] = {s.id: [] for s in steps if s.id is not None}

    for step in steps:
        if step.id is None:
            continue
        for dep_id in step.depends:
            adjacency[dep_id].append(step.id)
            in_degree[step.id] += 1

    queue = [sid for sid, deg in in_degree.items() if deg == 0]
    visited = 0
    while queue:
        node = queue.pop(0)
        visited += 1
        for neighbor in adjacency[node]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if visited < len(in_degree):
        raise ValueError("circular dependency detected among steps")


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
        *,
        order_builders: dict[str, OrderBuilder] | None = None,
    ):
        """
        Args:
            pipeline_state: PipelineState インスタンス
            order_builder: デフォルト OrderBuilder。
                base_context["workflow_name"] が ``order_builders`` に含まれない、
                もしくは ``order_builders`` が None のときに使われるフォールバック。
            queue_dir: order/result ファイルを書き出すディレクトリ
            order_builders: workflow_name → OrderBuilder のマップ。
                複数ワークフローを横断する dispatcher（``ghdag watch``）は
                ワークフローごとに異なる ``template_dir`` を持つことがあるため、
                ここに per-workflow な OrderBuilder を渡してテンプレート解決を
                ワークフロー単位に切り替えられるようにする。
                単一ワークフロー前提の呼び出し（``ghdag trigger``）では
                ``None`` のままで問題ない。
        """
        self._state = pipeline_state
        self._order_builder = order_builder
        self._queue_dir = queue_dir
        self._order_builders: dict[str, OrderBuilder] = dict(order_builders or {})

    def check_idempotency(self, key: str) -> bool:
        """冪等性チェックを PipelineState に委譲する。"""
        return self._state.check_idempotency(key)

    def remove_idempotency_matching(self, workflow_name: str, issue_number: int) -> None:
        """冪等キー削除を PipelineState に委譲する。"""
        self._state.remove_idempotency_matching(workflow_name, issue_number)

    @property
    def _jsonl_mode(self) -> bool:
        return self._state._is_jsonl_mode

    def submit(
        self,
        steps: list[StepConfig],
        base_context: dict[str, str],
        *,
        idempotency_key: str | None = None,
        audit_context: AuditContext,
        metadata: dict[str, str] | None = None,
    ) -> list[str]:
        """ステップ群を order/exec ファイルに投入する。

        exec ファイルの拡張子が .jsonl の場合は JSON レコード形式で書き込む。
        それ以外はテキスト形式（uuid: command）で書き込む。

        Args:
            steps: 実行する StepConfig のリスト
            base_context: 全ステップ共通のコンテキスト変数
            idempotency_key: 冪等性キー（省略時は記録しない）
            audit_context: enqueue audit に記録するコンテキスト（省略時はデフォルト AuditContext）
            metadata: 全ステップ共通のメタデータ（JSONL モードで exec.jsonl の annotations に格納）。
                テキストモードでは無視される。省略時は既存動作と同一（後方互換）。

        Returns:
            書き込んだエントリを文字列化したリスト（DispatchResult 用）
        """
        _validate_depends(steps)

        ts = datetime.now(tz=ZoneInfo("Asia/Tokyo")).strftime("%Y%m%d%H%M%S")
        order_builder = self._resolve_order_builder(base_context.get("workflow_name"))
        step_uuid_map: dict[str, str] = {}
        step_engine_map: dict[str, str] = {}

        if self._jsonl_mode:
            return self._submit_jsonl(
                steps, base_context, idempotency_key, ts, order_builder,
                step_uuid_map, step_engine_map, audit_context,
                metadata=metadata,
            )
        return self._submit_text(
            steps, base_context, idempotency_key, ts, order_builder,
            step_uuid_map, step_engine_map, audit_context,
            # metadata は渡さない — テキストモードではサポート対象外
        )

    def _submit_text(
        self,
        steps: "list[StepConfig]",
        base_context: dict[str, str],
        idempotency_key: str | None,
        ts: str,
        order_builder: OrderBuilder,
        step_uuid_map: dict[str, str],
        step_engine_map: dict[str, str],
        audit_context: AuditContext,
    ) -> list[str]:
        """テキスト形式（exec.md）への書き込み。"""
        exec_lines: list[str] = []

        if idempotency_key:
            exec_lines.append(f"# idempotency: {idempotency_key}")

        for step in steps:
            step_uuid = str(uuid.uuid4())
            engine = step.engine
            step_id = step.id if step.id else step_uuid
            step_uuid_map[step_id] = step_uuid
            step_engine_map[step_id] = engine

            result_filename = f"{ts}-{engine}-result-{step_uuid}.md"
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
                    dep_result_filename = f"{ts}-{dep_engine}-result-{dep_uuid}.md"
                    context[f"{dep_id}_result_filename"] = dep_result_filename

                    dep_result_path = os.path.join(self._queue_dir, dep_result_filename)
                    if os.path.isfile(dep_result_path):
                        with open(dep_result_path, encoding="utf-8") as f:
                            context[f"{dep_id}_result_content"] = f.read()
                    else:
                        context[f"{dep_id}_result_content"] = ""

            order_content = order_builder.build_order(step.template, context)
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

        self._state.append_exec(exec_lines, audit_context=audit_context)
        return exec_lines

    def _submit_jsonl(
        self,
        steps: "list[StepConfig]",
        base_context: dict[str, str],
        idempotency_key: str | None,
        ts: str,
        order_builder: OrderBuilder,
        step_uuid_map: dict[str, str],
        step_engine_map: dict[str, str],
        audit_context: AuditContext,
        *,
        metadata: dict[str, str] | None = None,
    ) -> list[str]:
        """JSONL 形式（exec.jsonl）への書き込み。"""
        import json as _json

        records: list[dict] = []

        for step in steps:
            step_uuid = str(uuid.uuid4())
            engine = step.engine
            step_id = step.id if step.id else step_uuid
            step_uuid_map[step_id] = step_uuid
            step_engine_map[step_id] = engine

            result_filename = f"{ts}-{engine}-result-{step_uuid}.md"
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

            order_content = order_builder.build_order(step.template, context)
            order_filename = self._state.write_order_file(
                ts, step_uuid, order_content, self._queue_dir, engine=engine
            )
            record = self._build_exec_record(
                step_uuid=step_uuid,
                depends=[step_uuid_map[d] for d in step.depends if d in step_uuid_map],
                order_filename=order_filename,
                result_filename=result_filename,
                engine=engine,
                model=step.model,
            )
            if metadata:
                record.setdefault("annotations", {}).update(metadata)
            if idempotency_key:
                record["idempotency_key"] = idempotency_key
            records.append(record)

        self._state.append_exec_records(records, audit_context=audit_context)
        return [_json.dumps(r, ensure_ascii=False) for r in records]

    def _resolve_order_builder(self, workflow_name: str | None) -> OrderBuilder:
        """workflow_name から OrderBuilder を解決する。

        ``order_builders`` に該当エントリがあればそれを返し、なければ
        ``order_builder`` (デフォルト) を返す。
        """
        if workflow_name and workflow_name in self._order_builders:
            return self._order_builders[workflow_name]
        return self._order_builder

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
        from ghdag.workflow.engine import get_adapter

        adapter = get_adapter(engine)
        return adapter.build_exec_line(
            uuid=step_uuid,
            order_path=f"{self._queue_dir}/{order_filename}",
            result_path=f"{self._queue_dir}/{result_filename}",
            prompt="受け取った内容を実行して",
            model=model,
            depends=depends,
        )

    def _build_exec_record(
        self,
        *,
        step_uuid: str,
        depends: list[str],
        order_filename: str,
        result_filename: str,
        engine: str,
        model: str,
    ) -> dict:
        """exec.jsonl の 1 レコードを構築する（内部メソッド）。"""
        from ghdag.workflow.engine import get_adapter

        adapter = get_adapter(engine)
        return adapter.build_exec_record(
            uuid=step_uuid,
            order_path=f"{self._queue_dir}/{order_filename}",
            result_path=f"{self._queue_dir}/{result_filename}",
            prompt="受け取った内容を実行して",
            model=model,
            depends=depends,
        )
