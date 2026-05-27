"""workflow/engine.py — LLM エンジン Adapter パターン"""

from __future__ import annotations

import warnings
from typing import Protocol

from ghdag.exceptions import GhdagError
from ghdag.llm.spec import ENGINE_SPECS, EngineSpec, render_exec_command


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


class _GenericAdapter:
    """ENGINE_SPECS から生成される汎用アダプター。4 Adapter クラスを統合。"""

    def __init__(self, spec: EngineSpec) -> None:
        self._spec = spec

    @property
    def name(self) -> str:
        return self._spec.name

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
            "engine": self._spec.name,
            "model": model if self._spec.model_flag else None,
            "command": render_exec_command(
                self._spec, order_path=order_path, prompt=prompt, model=model
            ),
            "depends": depends,
            "result_path": result_path,
            "retry": 0,
            "annotations": {},
        }


_CUSTOM_ADAPTERS: dict[str, EngineAdapter] = {}


class AdapterNotFoundError(GhdagError, ValueError):
    """Raised when an unregistered engine adapter is requested."""


def register_adapter(adapter: EngineAdapter) -> None:
    _CUSTOM_ADAPTERS[adapter.name] = adapter


def get_adapter(name: str) -> EngineAdapter:
    spec = ENGINE_SPECS.get(name)
    if spec is not None:
        return _GenericAdapter(spec)
    if name in _CUSTOM_ADAPTERS:
        return _CUSTOM_ADAPTERS[name]
    raise AdapterNotFoundError(f"Unknown engine: {name!r}. Available: {sorted(ENGINE_SPECS)}")


# ---------------------------------------------------------------------------
# Deprecated aliases — 0.24.0 で削除予定
# ---------------------------------------------------------------------------

def ClaudeAdapter() -> _GenericAdapter:
    warnings.warn(
        "ClaudeAdapter is deprecated and will be removed in 0.24.0. "
        "Use get_adapter('claude') instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return _GenericAdapter(ENGINE_SPECS["claude"])


def GeminiAdapter() -> _GenericAdapter:
    warnings.warn(
        "GeminiAdapter is deprecated and will be removed in 0.24.0. "
        "Use get_adapter('gemini') instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return _GenericAdapter(ENGINE_SPECS["gemini"])


def CursorAdapter() -> _GenericAdapter:
    warnings.warn(
        "CursorAdapter is deprecated and will be removed in 0.24.0. "
        "Use get_adapter('cursor') instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return _GenericAdapter(ENGINE_SPECS["cursor"])


def ShellAdapter() -> _GenericAdapter:
    warnings.warn(
        "ShellAdapter is deprecated and will be removed in 0.24.0. "
        "Use get_adapter('shell') instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return _GenericAdapter(ENGINE_SPECS["shell"])
