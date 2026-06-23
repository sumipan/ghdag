"""claude --output-format json の stdout から本文テキストと TokenUsage を抽出するアダプター。"""

from __future__ import annotations

import json

from ghdag.metrics.models import TokenUsage
from ghdag.metrics.parsers import parse_token_usage_json


class ClaudeJsonAdapter:
    """JSON 形式の claude stdout を処理し、result テキストと使用量を取り出す。

    JSON parse に失敗した場合はフォールバックとして raw stdout を返し、
    TokenUsage は None を返す。result_path の中身が壊れない安全弁として機能する。
    """

    def extract_result_text(self, stdout: bytes, stderr: bytes) -> bytes:
        if not stdout:
            return stdout
        try:
            data = json.loads(stdout.decode("utf-8"))
            return (data.get("result") or "").encode("utf-8")
        except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
            return stdout

    def extract_token_usage(self, stdout: bytes, stderr: bytes) -> TokenUsage | None:
        if not stdout:
            return None
        try:
            data = json.loads(stdout.decode("utf-8"))
            return parse_token_usage_json(data)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
