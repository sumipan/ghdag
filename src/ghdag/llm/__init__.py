"""ghdag.llm — one-shot LLM invocation interface"""

from ghdag.core.capabilities import (
    DANGEROUS_FULL_ACCESS,
    JSON_ONLY,
    PERMISSIONS,
    TEXT_ONLY,
    WEB_RESEARCH,
    Permission,
)
from ghdag.core.container import container_prefix
from ghdag.core.engine_spec import ENGINE_SPECS, EngineSpec, InputMode, PromptFlag
from ghdag.core.exceptions import LLMParseError
from ghdag.core.ports.output import EngineError
from ghdag.llm import _config
from ghdag.llm._constants import DEFAULT_ENGINE_MODELS
from ghdag.llm.adapters.failure_classification import classify_common_failure
from ghdag.llm.engines import (
    ENGINE_DEFAULTS,
    EngineModelError,
    LLMResult,
    build_llm_cmd,
    call,
    get_engine_models,
    list_engines,
    list_models,
    supports_capability,
    validate_engine_model,
)
from ghdag.llm.session import SessionRecord, SessionStore

__all__ = [
    "_config",
    "DEFAULT_ENGINE_MODELS",
    "ENGINE_DEFAULTS",
    "ENGINE_SPECS",
    "EngineError",
    "EngineModelError",
    "PERMISSIONS",
    "classify_common_failure",
    "container_prefix",
    "supports_capability",
    "EngineSpec",
    "InputMode",
    "PromptFlag",
    "Permission",
    "LLMParseError",
    "LLMResult",
    "SessionRecord",
    "SessionStore",
    "TEXT_ONLY",
    "JSON_ONLY",
    "WEB_RESEARCH",
    "DANGEROUS_FULL_ACCESS",
    "build_llm_cmd",
    "call",
    "get_engine_models",
    "list_engines",
    "list_models",
    "validate_engine_model",
]
