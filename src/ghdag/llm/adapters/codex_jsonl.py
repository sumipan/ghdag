"""Extract body text and TokenUsage from codex exec --json JSONL (stream capable)."""

from __future__ import annotations

from typing import Any

from ghdag.llm.adapters.codex import CodexAdapter


class CodexJsonlAdapter(CodexAdapter):
    """Stream adapter adding line-type classification to CodexAdapter JSONL extraction.

    ``--json`` output is always JSONL, so the body extraction logic is identical to CodexAdapter.
    Exposes a method to decide "is this the final agent_message" for line-by-line draining in the DAG.
    """

    def is_terminal_result_event(self, event: dict[str, Any]) -> bool:
        """Whether this is the final assistant message (item.completed + agent_message)."""
        if not isinstance(event, dict):
            return False
        if event.get("type") != "item.completed":
            return False
        item = event.get("item")
        return isinstance(item, dict) and item.get("type") == "agent_message"
