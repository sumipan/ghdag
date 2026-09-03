"""ghdag.llm — ワンショット LLM 呼び出しインタフェース"""

from ghdag.llm import _config
from ghdag.llm._constants import DEFAULT_ENGINE_MODELS
from ghdag.llm.capabilities import (
    DANGEROUS_FULL_ACCESS,
    JSON_ONLY,
    TEXT_ONLY,
    WEB_RESEARCH,
    LLMCapabilities,
    LLMParseError,
)
from ghdag.llm.engines import (
    ENGINE_DEFAULTS,
    EngineModelError,
    LLMResult,
    TextResult,
    build_llm_cmd,
    call,
    call_text,
    get_engine_models,
    list_engines,
    list_models,
    validate_engine_model,
)
from ghdag.llm.managed import ManagedResult, call_managed
from ghdag.llm.session import SessionRecord, SessionStore
from ghdag.llm.spec import ENGINE_SPECS, EngineSpec

__all__ = [
    "_config",
    "DEFAULT_ENGINE_MODELS",
    "ENGINE_DEFAULTS",
    "ENGINE_SPECS",
    "EngineModelError",
    "EngineSpec",
    "LLMCapabilities",
    "LLMParseError",
    "LLMResult",
    "ManagedResult",
    "TextResult",
    "SessionRecord",
    "SessionStore",
    "TEXT_ONLY",
    "JSON_ONLY",
    "WEB_RESEARCH",
    "DANGEROUS_FULL_ACCESS",
    "build_llm_cmd",
    "call",
    "call_managed",
    "call_text",
    "get_engine_models",
    "list_engines",
    "list_models",
    "validate_engine_model",
]
