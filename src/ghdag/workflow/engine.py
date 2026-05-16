"""workflow/engine.py — LLM エンジン Adapter パターン"""

from __future__ import annotations

from typing import Protocol


class EngineAdapter(Protocol):
    """エンジンごとの exec 行組み立てを担う"""

    @property
    def name(self) -> str:
        """エンジン名（"claude", "gemini"）"""
        ...

    def build_exec_line(
        self,
        *,
        uuid: str,
        order_path: str,
        result_path: str,
        prompt: str,
        model: str | None,
        depends: list[str],
    ) -> str:
        """exec.md に書き込む1行を組み立てる。

        Args:
            uuid: ジョブ識別子
            order_path: order ファイルパス（例: queue/ts-claude-order-uuid.md）
            result_path: result ファイルパス
            prompt: `-p` に渡すプロンプト文字列
            model: モデル ID（None の場合はエンジンのデフォルト）
            depends: 依存する UUID のリスト

        Returns:
            exec.md に追記する行（例: "uuid[depends:a,b]: cat ... | claude -p ... | tee ..."）
        """
        ...

    def build_exec_record(
        self,
        *,
        uuid: str,
        order_path: str,
        result_path: str,
        prompt: str,
        model: str | None,
        depends: list[str],
    ) -> dict:
        """exec.jsonl に書き込む 1 レコード（dict）を組み立てる。
        command フィールドに tee パイプを含めない。
        """
        ...


class ClaudeAdapter:
    name = "claude"

    def build_exec_line(
        self,
        *,
        uuid: str,
        order_path: str,
        result_path: str,
        prompt: str,
        model: str | None,
        depends: list[str],
    ) -> str:
        deps = f"[depends:{','.join(depends)}]" if depends else ""
        model_flag = f" --model '{model}'" if model else ""
        return (
            f"{uuid}{deps}: cat {order_path}"
            f" | claude -p '{prompt}'{model_flag}"
            f" --dangerously-skip-permissions"
            f" | tee -a {result_path}"
        )

    def build_exec_record(
        self,
        *,
        uuid: str,
        order_path: str,
        result_path: str,
        prompt: str,
        model: str | None,
        depends: list[str],
    ) -> dict:
        model_flag = f" --model '{model}'" if model else ""
        return {
            "uuid": uuid,
            "command": (
                f"cat {order_path}"
                f" | claude -p '{prompt}'{model_flag}"
                f" --dangerously-skip-permissions"
            ),
            "depends": depends,
            "result_path": result_path,
            "retry": 0,
            "annotations": {},
        }


class GeminiAdapter:
    name = "gemini"

    def build_exec_line(
        self,
        *,
        uuid: str,
        order_path: str,
        result_path: str,
        prompt: str,
        model: str | None,
        depends: list[str],
    ) -> str:
        deps = f"[depends:{','.join(depends)}]" if depends else ""
        model_flag = f" -m {model}" if model else ""
        return (
            f"{uuid}{deps}: cat {order_path}"
            f" | gemini -p '{prompt}'{model_flag}"
            f" --approval-mode yolo"
            f" | tee -a {result_path}"
        )

    def build_exec_record(
        self,
        *,
        uuid: str,
        order_path: str,
        result_path: str,
        prompt: str,
        model: str | None,
        depends: list[str],
    ) -> dict:
        model_flag = f" -m {model}" if model else ""
        return {
            "uuid": uuid,
            "command": (
                f"cat {order_path}"
                f" | gemini -p '{prompt}'{model_flag}"
                f" --approval-mode yolo"
            ),
            "depends": depends,
            "result_path": result_path,
            "retry": 0,
            "annotations": {},
        }


_ADAPTERS: dict[str, EngineAdapter] = {}


def register_adapter(adapter: EngineAdapter) -> None:
    _ADAPTERS[adapter.name] = adapter


def get_adapter(name: str) -> EngineAdapter:
    if name not in _ADAPTERS:
        raise ValueError(f"Unknown engine: {name!r}. Available: {list(_ADAPTERS)}")
    return _ADAPTERS[name]


class CursorAdapter:
    name = "cursor"

    def build_exec_line(
        self,
        *,
        uuid: str,
        order_path: str,
        result_path: str,
        prompt: str,
        model: str | None,
        depends: list[str],
    ) -> str:
        deps = f"[depends:{','.join(depends)}]" if depends else ""
        model_flag = f" --model '{model}'" if model else ""
        return (
            f"{uuid}{deps}: agent{model_flag} -p --force"
            f" < {order_path}"
            f" | tee -a {result_path}"
        )

    def build_exec_record(
        self,
        *,
        uuid: str,
        order_path: str,
        result_path: str,
        prompt: str,
        model: str | None,
        depends: list[str],
    ) -> dict:
        model_flag = f" --model '{model}'" if model else ""
        return {
            "uuid": uuid,
            "command": f"agent{model_flag} -p --force < {order_path}",
            "depends": depends,
            "result_path": result_path,
            "retry": 0,
            "annotations": {},
        }


class ShellAdapter:
    """bash スクリプトを order_path から直接実行するアダプター。

    order ファイルは LLM プロンプトではなく実行可能な bash スクリプト本体として扱う。
    `prompt` / `model` パラメーターは無視する（model は常に "bash" 固定）。
    """

    name = "shell"

    def build_exec_line(
        self,
        *,
        uuid: str,
        order_path: str,
        result_path: str,
        prompt: str,
        model: str | None,
        depends: list[str],
    ) -> str:
        deps = f"[depends:{','.join(depends)}]" if depends else ""
        return (
            f"{uuid}{deps}: bash -o pipefail {order_path}"
            f" | tee -a {result_path}"
        )

    def build_exec_record(
        self,
        *,
        uuid: str,
        order_path: str,
        result_path: str,
        prompt: str,
        model: str | None,
        depends: list[str],
    ) -> dict:
        return {
            "uuid": uuid,
            "command": f"bash -o pipefail {order_path}",
            "depends": depends,
            "result_path": result_path,
            "retry": 0,
            "annotations": {},
        }


# 起動時に登録
register_adapter(ClaudeAdapter())
register_adapter(GeminiAdapter())
register_adapter(CursorAdapter())
register_adapter(ShellAdapter())
