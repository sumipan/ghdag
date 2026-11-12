"""Tests for GhdagError exception hierarchy."""

import pytest

from ghdag import GhdagError
from ghdag.core.command import AdapterNotFoundError
from ghdag.core.exceptions import (
    AuthError,
    CliUsageError,
    ConfigError,
    GitHubApiError,
    LLMParseError,
    NetworkError,
    PermissionDeniedError,
    RateLimitError,
)
from ghdag.core.exceptions import (
    GhdagError as GhdagErrorFromModule,
)
from ghdag.core.exceptions import LLMParseError as LLMParseErrorCanonical
from ghdag.core.models.files import PathTraversalError
from ghdag.dag.fanout import FanoutError
from ghdag.files.append import AppendRecoverError
from ghdag.llm._config import ConfigLoadError
from ghdag.llm.engines import EngineModelError
from ghdag.pipeline.config import ModelValidationError
from ghdag.pipeline.llm_pipeline import DependencyError
from ghdag.tool.exceptions import ToolRegistryError
from ghdag.workflow.dispatcher import ContextHookError
from ghdag.workflow.loader import ValidationError

_CUSTOM_EXCEPTIONS = [
    ValidationError,
    AdapterNotFoundError,
    ContextHookError,
    DependencyError,
    ModelValidationError,
    ConfigLoadError,
    LLMParseError,
    EngineModelError,
    FanoutError,
    PathTraversalError,
    AppendRecoverError,
]

_VALUE_ERROR_EXCEPTIONS = [
    ValidationError,
    AdapterNotFoundError,
    ContextHookError,
    DependencyError,
    ConfigLoadError,
    FanoutError,
    PathTraversalError,
    AppendRecoverError,
]


@pytest.mark.parametrize("exc_cls", _CUSTOM_EXCEPTIONS)
def test_custom_exception_is_ghdag_error_subclass(exc_cls):
    assert issubclass(exc_cls, GhdagError)


@pytest.mark.parametrize("exc_cls", _VALUE_ERROR_EXCEPTIONS)
def test_value_error_exceptions_remain_value_error_subclass(exc_cls):
    assert issubclass(exc_cls, ValueError)


def test_ghdag_error_exported_from_package():
    assert GhdagError is GhdagErrorFromModule


def _raise_instance(exc_cls):
    if exc_cls is LLMParseError:
        raise exc_cls("raw", "reason")
    raise exc_cls("test")


@pytest.mark.parametrize("exc_cls", _CUSTOM_EXCEPTIONS)
def test_except_ghdag_error_catches_custom_exception(exc_cls):
    with pytest.raises(GhdagError):
        _raise_instance(exc_cls)


def test_llm_parse_error_preserves_attributes():
    err = LLMParseError("raw output", "invalid json")
    assert err.raw == "raw output"
    assert err.reason == "invalid json"
    assert err.code == "LLM_OUTPUT_NOT_JSON"
    assert err.exit_code == 3
    assert LLMParseError is LLMParseErrorCanonical


# --- GitHub API exception hierarchy (issue #1289) ---


def test_github_api_error_status_code_explicit():
    assert GitHubApiError("msg", status_code=401).status_code == 401


def test_github_api_error_status_code_omitted():
    assert GitHubApiError("msg").status_code is None


def test_auth_error_inheritance():
    err = AuthError("x", 401)
    assert isinstance(err, GitHubApiError)
    assert isinstance(err, GhdagError)


@pytest.mark.parametrize(
    "exc_cls",
    [AuthError, RateLimitError, PermissionDeniedError, NetworkError],
)
def test_github_api_subclasses_inherit_from_github_api_error(exc_cls):
    assert issubclass(exc_cls, GitHubApiError)
    assert issubclass(exc_cls, GhdagError)


def test_ghdag_error_to_dict_includes_code_hint_and_exit_code():
    """Issue #6153 — machine-readable GhdagError.to_dict()."""
    err = GhdagError("boom", code="X_Y", hint="do z")
    assert err.to_dict() == {
        "code": "X_Y",
        "message": "boom",
        "hint": "do z",
        "exit_code": 1,
    }


def test_rate_limit_error_positional_args_preserve_fields():
    """Issue #6153 — GitHub rate limit code and positional compat."""
    err = RateLimitError("limited", 403, 1_700_000_000)
    assert err.status_code == 403
    assert err.reset_at == 1_700_000_000
    assert err.code == "GITHUB_RATE_LIMIT"
    assert err.exit_code == 4


def test_config_and_cli_usage_errors_exit_codes():
    """Issue #6153 — ConfigError / CliUsageError hierarchy."""
    assert ConfigError("bad").exit_code == 3
    assert CliUsageError("bad").exit_code == 2
    assert isinstance(ConfigError("bad"), GhdagError)
    assert isinstance(CliUsageError("bad"), GhdagError)


def test_tool_registry_error_code_and_exit_code():
    """Issue #6153 — ToolRegistryError machine-readable code."""
    err = ToolRegistryError("x")
    assert err.code == "TOOL_REGISTRY_INVALID"
    assert err.exit_code == 3
