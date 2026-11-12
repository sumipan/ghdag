"""ghdag.llm — one-shot LLM invocation interface"""

from ghdag.core.capabilities import (
    DANGEROUS_FULL_ACCESS,
    PERMISSIONS,
    TEXT_ONLY,
    Permission,
)
from ghdag.core.container import container_prefix
from ghdag.core.engine_spec import ENGINE_SPECS
from ghdag.core.ports.output import EngineError
from ghdag.llm.adapters.failure_classification import classify_common_failure
from ghdag.llm.engines import (
    ENGINE_DEFAULTS,
    EngineModelError,
    LLMResult,
    call,
    get_engine_models,
    list_engines,
    supports_capability,
    validate_engine_model,
)
from ghdag.llm.session import SessionRecord

from . import _config as _config

__all__ = [
    "ENGINE_DEFAULTS",
    "ENGINE_SPECS",
    "EngineError",
    "EngineModelError",
    "PERMISSIONS",
    "classify_common_failure",
    "container_prefix",
    "supports_capability",
    "Permission",
    "LLMResult",
    "SessionRecord",
    "TEXT_ONLY",
    "DANGEROUS_FULL_ACCESS",
    "call",
    "get_engine_models",
    "list_engines",
    "validate_engine_model",
]
