"""ghdag.llm._constants — エンジン・モデルのデフォルト値"""

from __future__ import annotations

# フォールバック用デフォルト値（YAML 設定ファイルが存在しない場合に使用）
DEFAULT_ENGINE_MODELS: dict[str, list[str]] = {
    "claude": [
        "claude-opus-4-7",
        "claude-opus-4-6",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
    ],
    "gemini": [
        "gemini-2.5-pro",
        "gemini-2.5-flash",
    ],
    # cursor agent CLI (https://cursor.com)。`agent --model <id> -p <prompt>` で呼び出す。
    # 利用可能モデルは `agent --list-models` で確認可能。代表的なものだけホワイトリスト化。
    "cursor": [
        "auto",
        "composer-2",
        "composer-2-fast",
        "gpt-5.2",
        "gpt-5.3-codex",
        "gpt-5.3-codex-fast",
        "gpt-5.3-codex-high",
        "gpt-5.3-codex-high-fast",
        "gpt-5.4-medium-fast",
    ],
}
