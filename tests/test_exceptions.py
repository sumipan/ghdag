"""Tests for GhdagError exception hierarchy."""

import pytest

from ghdag import GhdagError
from ghdag.dag.fanout import FanoutError
from ghdag.exceptions import GhdagError as GhdagErrorFromModule
from ghdag.files.append import AppendRecoverError
from ghdag.files.models import PathTraversalError
from ghdag.llm._config import ConfigLoadError
from ghdag.llm.capabilities import LLMParseError
from ghdag.llm.engines import EngineModelError
from ghdag.pipeline.config import ModelValidationError
from ghdag.pipeline.llm_pipeline import DependencyError
from ghdag.workflow.dispatcher import ContextHookError
from ghdag.workflow.engine import AdapterNotFoundError
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
