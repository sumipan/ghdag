"""ghdag.llm.capabilities — LLM 呼び出しの能力制約値オブジェクトとプリセット"""

from __future__ import annotations

from dataclasses import dataclass

from ghdag.exceptions import GhdagError


class LLMParseError(GhdagError):
    """Raised when a response violates the output_format contract."""
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
    stream: bool = False  # True 時 --output-format stream-json（output_format を上書き）
    sandbox: str = "off"  # "off" | "readonly"


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

# 観測系 Bash を許可しつつ編集をサンドボックスで封じる（TEXT_ONLY のツール剥奪代替）。
# disallowed_tools は claude 向け二重防壁。codex / cursor では各エンジンの
# _IGNORED_CAPABILITIES で noop になるが、プリセット定義はエンジン非依存に保つ。
READONLY_OBSERVE = LLMCapabilities(
    sandbox="readonly",
    disallowed_tools=("Edit", "Write", "NotebookEdit"),
)

PRESETS: dict[str, LLMCapabilities] = {
    "text_only": TEXT_ONLY,
    "json_only": JSON_ONLY,
    "web_research": WEB_RESEARCH,
    "dangerous_full_access": DANGEROUS_FULL_ACCESS,
    "readonly_observe": READONLY_OBSERVE,
}
