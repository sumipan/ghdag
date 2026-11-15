"""Tests for GhdagError exception hierarchy."""

import pytest

from ghdag import GhdagError
from ghdag.core.command import AdapterNotFoundError
from ghdag.core.exceptions import (
    AuthError,
    GitHubApiError,
    LLMParseError,
    NetworkError,
    PermissionDeniedError,
    RateLimitError,
)
from ghdag.core.exceptions import (
    GhdagError as GhdagErrorFromModule,
)
from ghdag.core.models.files import PathTraversalError
from ghdag.dag.fanout import FanoutError
from ghdag.files.append import AppendRecoverError
from ghdag.llm._config import ConfigLoadError
from ghdag.llm.engines import EngineModelError
from ghdag.pipeline.config import ModelValidationError
from ghdag.pipeline.llm_pipeline import DependencyError
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
