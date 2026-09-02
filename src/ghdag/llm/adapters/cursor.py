"""cursor agent CLI 用アダプター（usage 未対応のため no-op）。"""

from __future__ import annotations

import json

from ghdag.core.models.metrics import TokenUsage
from ghdag.core.ports.output import EngineError


class CursorAdapter:
    def extract_result_text(self, stdout: bytes, stderr: bytes) -> bytes:
        return stdout

    def extract_token_usage(self, stdout: bytes, stderr: bytes) -> TokenUsage | None:
        return None

    def extract_session_id(self, stdout: bytes, stderr: bytes) -> str | None:
        for line in stdout.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            chat_id = obj.get("chat_id") if isinstance(obj, dict) else None
            if isinstance(chat_id, str) and chat_id:
                return chat_id
        return None

    def extract_error(self, stdout: bytes, stderr: bytes) -> EngineError | None:
        return None
