"""workflow/engine.py — LLM エンジン Adapter パターン"""

from __future__ import annotations

from typing import Protocol

from ghdag.llm.spec import ENGINE_SPECS, render_exec_command


class EngineAdapter(Protocol):
    """エンジンごとの exec レコード組み立てを担う"""

    @property
    def name(self) -> str:
        """エンジン名（"claude", "gemini"）"""
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
    _spec = ENGINE_SPECS["claude"]

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
            "engine": self.name,
            "model": model,
            "command": render_exec_command(self._spec, order_path=order_path, prompt=prompt, model=model),
            "depends": depends,
            "result_path": result_path,
            "retry": 0,
            "annotations": {},
        }


class GeminiAdapter:
    name = "gemini"
    _spec = ENGINE_SPECS["gemini"]

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
            "engine": self.name,
            "model": model,
            "command": render_exec_command(self._spec, order_path=order_path, prompt=prompt, model=model),
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
    _spec = ENGINE_SPECS["cursor"]

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
            "engine": self.name,
            "model": model,
            "command": render_exec_command(self._spec, order_path=order_path, prompt=prompt, model=model),
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
    _spec = ENGINE_SPECS["shell"]

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
            "engine": self.name,
            "model": None,
            "command": render_exec_command(self._spec, order_path=order_path, prompt=prompt, model=model),
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
