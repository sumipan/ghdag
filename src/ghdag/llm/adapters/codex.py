"""codex --json の JSONL stdout から本文テキストと TokenUsage を抽出するアダプター。"""

from __future__ import annotations

import json

from ghdag.core.models.metrics import TokenUsage


class CodexAdapter:
    """JSONL 形式の codex stdout を処理し、result テキストと使用量を取り出す。

    codex --json は複数行 JSONL を出力する。本文は item.completed + agent_message の
    item.text に、使用量は turn.completed の usage に格納される。

    LLMResult.stdout は生 JSONL のままであり、テキストが必要な呼び出し側は
    extract_result_text() を通すこと（claude エンジンとの非対称に注意）。
    """

    def extract_result_text(self, stdout: bytes, stderr: bytes) -> bytes:
        texts: list[str] = []
        for line in stdout.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                obj.get("type") == "item.completed"
                and isinstance(obj.get("item"), dict)
                and obj["item"].get("type") == "agent_message"
            ):
                text = obj["item"].get("text", "")
                if isinstance(text, str):
                    texts.append(text)
        return "\n".join(texts).encode("utf-8")

    def extract_token_usage(self, stdout: bytes, stderr: bytes) -> TokenUsage | None:
        for line in stdout.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") == "turn.completed":
                usage = obj.get("usage")
                if not isinstance(usage, dict):
                    return None
                input_tokens = usage.get("input_tokens", 0) or 0
                output_tokens = usage.get("output_tokens", 0) or 0
                cache_read = usage.get("cached_input_tokens", 0) or 0
                cache_write = usage.get("cache_write_input_tokens", 0) or 0
                return TokenUsage(
                    token_count=input_tokens + output_tokens,
                    cache_read_tokens=cache_read,
                    cache_creation_tokens=cache_write,
                    cost_usd=None,
                )
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
            session_id = obj.get("session_id") if isinstance(obj, dict) else None
            if isinstance(session_id, str) and session_id:
                return session_id
        return None
