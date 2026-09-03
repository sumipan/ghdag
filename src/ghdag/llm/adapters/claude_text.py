"""テキスト形式の claude stdout 用フォールバックアダプター（後方互換）。

stdout をそのまま返し、stderr から token_count を抽出する既存挙動を温存する。
"""

from __future__ import annotations

from ghdag.core.models.metrics import FailureClass, TokenUsage
from ghdag.core.parsers import parse_token_count
from ghdag.core.ports.output import EngineError
from ghdag.llm.adapters.failure_classification import classify_common_failure


class ClaudeTextAdapter:
    def extract_result_text(self, stdout: bytes, stderr: bytes) -> bytes:
        return stdout

    def extract_token_usage(self, stdout: bytes, stderr: bytes) -> TokenUsage | None:
        stderr_text = stderr.decode("utf-8", errors="replace")
        token_count = parse_token_count("claude", stderr_text)
        if token_count is None:
            return None
        return TokenUsage(token_count=token_count)

    def extract_session_id(self, stdout: bytes, stderr: bytes) -> str | None:
        return None

    def extract_error(self, stdout: bytes, stderr: bytes) -> EngineError | None:
        return None

    def classify_failure(
        self,
        returncode: int,
        stdout: bytes,
        stderr: bytes,
    ) -> FailureClass | None:
        return classify_common_failure("claude", stdout, stderr)
