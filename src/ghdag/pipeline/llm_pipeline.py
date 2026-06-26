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

from ghdag.exceptions import GhdagError
from ghdag.pipeline.audit import AuditContext
from ghdag.pipeline.order import OrderBuilder
from ghdag.pipeline.state import PipelineState


class DependencyError(GhdagError, ValueError):
    """Raised when step dependencies are invalid or circular."""

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
                raise DependencyError(f"Unknown dependency: {dep_id!r}")

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
        raise DependencyError("circular dependency detected among steps")


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

    def remove_idempotency_for_handler(
        self, workflow_name: str, handler_name: str, issue_number: int
    ) -> int:
        """handler 単位の冪等キー削除を PipelineState に委譲する。"""
        return self._state.remove_idempotency_for_handler(workflow_name, handler_name, issue_number)

    def submit(
        self,
        steps: list[StepConfig],
        base_context: dict[str, str],
        *,
        idempotency_key: str | None = None,
        audit_context: AuditContext,
        metadata: dict[str, str] | None = None,
    ) -> list[str]:
        """ステップ群を order/exec.jsonl ファイルに投入する。

        Args:
            steps: 実行する StepConfig のリスト
            base_context: 全ステップ共通のコンテキスト変数
            idempotency_key: 冪等性キー（省略時は記録しない）
            audit_context: enqueue audit に記録するコンテキスト
            metadata: 全ステップ共通のメタデータ（exec.jsonl の annotations に格納）

        Returns:
            書き込んだ JSON レコードを文字列化したリスト（DispatchResult 用）
        """
        _validate_depends(steps)

        ts = datetime.now(tz=ZoneInfo("Asia/Tokyo")).strftime("%Y%m%d%H%M%S")
        order_builder = self._resolve_order_builder(base_context.get("workflow_name"))
        step_uuid_map: dict[str, str] = {}
        step_engine_map: dict[str, str] = {}

        return self._submit_jsonl(
            steps, base_context, idempotency_key, ts, order_builder,
            step_uuid_map, step_engine_map, audit_context,
            metadata=metadata,
        )

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
            record = self._build_exec_record(
                step_uuid=step_uuid,
                depends=[step_uuid_map[d] for d in step.depends if d in step_uuid_map],
                order_filename=order_filename,
                result_filename=result_filename,
                engine=engine,
                model=step.model,
                permission=step.permission,
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

    def _build_exec_record(
        self,
        *,
        step_uuid: str,
        depends: list[str],
        order_filename: str,
        result_filename: str,
        engine: str,
        model: str,
        permission: str | None = None,
    ) -> dict:
        """exec.jsonl の 1 レコードを構築する（内部メソッド）。"""
        from ghdag.llm.capabilities import PRESETS
        from ghdag.llm.spec import ENGINE_SPECS, render_exec_command

        if permission is not None and permission not in PRESETS:
            raise ValueError(
                f"Unknown permission preset: {permission!r}. "
                f"Available: {sorted(PRESETS.keys())}"
            )

        safe_default_env: str | None = None
        safe_default_applied = False
        if permission is not None:
            capabilities = PRESETS[permission]
        else:
            safe_default_env = os.environ.get("GHDAG_SAFE_DEFAULT_PERMISSION")
            if safe_default_env:
                if safe_default_env not in PRESETS:
                    raise ValueError(
                        f"Unknown GHDAG_SAFE_DEFAULT_PERMISSION: {safe_default_env!r}. "
                        f"Available: {sorted(PRESETS.keys())}"
                    )
                capabilities = PRESETS[safe_default_env]
                safe_default_applied = True
            else:
                safe_default_env = "text_only"  # 安全デフォルト（hardcoded）
                capabilities = PRESETS["text_only"]
                safe_default_applied = True

        spec = ENGINE_SPECS[engine]
        annotations: dict[str, object] = {}
        if permission is None:
            if safe_default_applied:
                annotations["safe_default_applied"] = True
                annotations["safe_default_preset"] = safe_default_env
            elif spec.danger_flag:
                annotations["default_permission_applied"] = True
                annotations["injected_danger_flag"] = spec.danger_flag

        return {
            "uuid": step_uuid,
            "engine": spec.engine,
            "model": model if spec.model_flag else None,
            "command": render_exec_command(
                spec,
                order_path=f"{self._queue_dir}/{order_filename}",
                prompt="受け取った内容を実行して",
                model=model,
                capabilities=capabilities,
            ),
            "depends": depends,
            "result_path": f"{self._queue_dir}/{result_filename}",
            "retry": 0,
            "annotations": annotations,
        }
