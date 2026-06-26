"""workflow/engine.py — LLM エンジン Adapter パターン"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from ghdag.exceptions import GhdagError
from ghdag.llm.spec import ENGINE_SPECS, EngineSpec, render_exec_command

if TYPE_CHECKING:
    from ghdag.llm.capabilities import LLMCapabilities


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
        result_path: str | None,
        prompt: str,
        model: str | None,
        depends: list[str],
        capabilities: "LLMCapabilities | None" = None,
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
        return self._spec.engine

    def build_exec_record(
        self,
        *,
        uuid: str,
        order_path: str,
        result_path: str | None,
        prompt: str,
        model: str | None,
        depends: list[str],
        capabilities: "LLMCapabilities | None" = None,
    ) -> dict:
        return {
            "uuid": uuid,
            "engine": self._spec.engine,
            "model": model if self._spec.model_flag else None,
            "command": render_exec_command(
                self._spec, order_path=order_path, prompt=prompt, model=model,
                capabilities=capabilities,
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
