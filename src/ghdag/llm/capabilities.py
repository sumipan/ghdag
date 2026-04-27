"""ghdag.llm.capabilities — LLM 呼び出しの能力制約値オブジェクトとプリセット"""

from __future__ import annotations

from dataclasses import dataclass


class LLMParseError(Exception):
    """output_format 契約に違反したレスポンスに対して送出。"""
    def __init__(self, raw: str, reason: str):
        self.raw = raw
        self.reason = reason
        super().__init__(f"LLM output parse failed: {reason}")


@dataclass(frozen=True)
class LLMCapabilities:
    """LLM 呼び出しの能力制約を束ねる値オブジェクト。"""
    permission_mode: str = "default"
    output_format: str = "text"  # "text" | "json"
    allowed_tools: tuple[str, ...] = ()  # 空 = 指定なし（CLI に渡さない）
    disallowed_tools: tuple[str, ...] = ()  # 空 = 指定なし（CLI に渡さない）


TEXT_ONLY = LLMCapabilities(
    permission_mode="default",
    output_format="text",
    disallowed_tools=("Bash", "Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch"),
)

JSON_ONLY = LLMCapabilities(
    permission_mode="default",
    output_format="json",
    disallowed_tools=("Bash", "Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch"),
)

WEB_RESEARCH = LLMCapabilities(
    permission_mode="default",
    output_format="text",
    allowed_tools=("WebFetch", "WebSearch", "Read", "Grep", "Glob"),
    disallowed_tools=("Bash", "Edit", "Write", "NotebookEdit"),
)

DANGEROUS_FULL_ACCESS = LLMCapabilities(
    permission_mode="bypassPermissions",
    output_format="text",
)
