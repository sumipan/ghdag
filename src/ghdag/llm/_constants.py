"""ghdag.llm._constants — エンジン・モデルのデフォルト値"""

from __future__ import annotations

# フォールバック用デフォルト値（YAML 設定ファイルが存在しない場合に使用）
DEFAULT_ENGINE_MODELS: dict[str, list[str]] = {
    "claude": [
        "claude-opus-4-6",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
    ],
    "gemini": [
        "gemini-2.5-pro",
        "gemini-2.5-flash",
    ],
}
