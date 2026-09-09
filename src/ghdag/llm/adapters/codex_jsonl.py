"""codex exec --json の JSONL から本文・TokenUsage を抽出する（stream 対応）。"""

from __future__ import annotations

from typing import Any

from ghdag.llm.adapters.codex import CodexAdapter


class CodexJsonlAdapter(CodexAdapter):
    """CodexAdapter の JSONL 抽出に、行種別判定を加えた stream 用アダプター。

    ``--json`` 出力は常に JSONL のため、本文抽出ロジックは CodexAdapter と同一。
    DAG の行単位ドレインで「最終 agent_message か」を判定するメソッドを公開する。
    """

    def is_terminal_result_event(self, event: dict[str, Any]) -> bool:
        """最終 assistant メッセージ（item.completed + agent_message）か。"""
        if not isinstance(event, dict):
            return False
        if event.get("type") != "item.completed":
            return False
        item = event.get("item")
        return isinstance(item, dict) and item.get("type") == "agent_message"
