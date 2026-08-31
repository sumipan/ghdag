"""cursor agent CLI 用アダプター（usage 未対応のため no-op）。"""

from __future__ import annotations

from ghdag.core.models.metrics import TokenUsage


class CursorAdapter:
    def extract_result_text(self, stdout: bytes, stderr: bytes) -> bytes:
        return stdout

    def extract_token_usage(self, stdout: bytes, stderr: bytes) -> TokenUsage | None:
        return None
