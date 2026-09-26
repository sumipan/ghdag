"""ghdag.core.capabilities — LLM 呼び出しの能力制約値オブジェクトとプリセット"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LLMCapabilities:
    """LLM 呼び出しの能力制約を束ねる値オブジェクト。"""
    permission_mode: str = "default"
    output_format: str = "text"  # "text" | "json"
    allowed_tools: tuple[str, ...] = ()  # 空 = 指定なし（CLI に渡さない）
    disallowed_tools: tuple[str, ...] = ()  # 空 = 指定なし（CLI に渡さない）
    stream: bool = False  # True 時 stream 出力（claude/cursor: stream-json、codex: --json）
    sandbox: str = "off"  # "off" | "readonly" | "container"
    resume: bool = False  # True 時セッション再開フローを許可
    # Container sandbox (sandbox="container"); see ghdag.core.container.container_prefix.
    container_image: str = ""  # required when sandbox="container"
    container_readonly: bool = False  # --read-only root FS and read-only worktree mount
    container_mounts: tuple[str, ...] = ()  # extra "host:container[:ro]" mounts; "~" expanded
    container_env: tuple[str, ...] = ()  # env var names passed through (values stay off argv)
    docker_bin: str = "docker"  # docker executable; a path also prepends its dir to PATH


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
# Write は禁止しない: claude の plan モード（sandbox=readonly）ではハーネスが計画
# ファイル（~/.claude/plans/*.md）を Write させ、計画ファイル以外への Write は
# plan モード自体が拒否する（sumipan/nexus#3188）。
READONLY_OBSERVE = LLMCapabilities(
    sandbox="readonly",
    disallowed_tools=("Edit", "NotebookEdit"),
)

PRESETS: dict[str, LLMCapabilities] = {
    "text_only": TEXT_ONLY,
    "json_only": JSON_ONLY,
    "web_research": WEB_RESEARCH,
    "dangerous_full_access": DANGEROUS_FULL_ACCESS,
    "readonly_observe": READONLY_OBSERVE,
}
