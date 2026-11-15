"""tests/llm/test_call_text.py — unified ghdag.llm.call / LLMResult (Issue sumipan/nexus#6529)."""
from __future__ import annotations

import inspect

import pytest

from ghdag.core.command import render_exec_command
from ghdag.core.container import container_prefix
from ghdag.llm import call
from ghdag.llm.engines import build_llm_cmd


def test_public_signatures_use_permission() -> None:
    """Issue sumipan/nexus#6501 — public LLM entrypoints take permission, not capabilities."""
    targets = (call, build_llm_cmd, render_exec_command, container_prefix)
    for fn in targets:
        params = inspect.signature(fn).parameters
        assert 'permission' in params, f'{fn.__name__} missing permission parameter'
        assert 'capabilities' not in params, f'{fn.__name__} still exposes capabilities'

def test_public_api_exports_only_call_and_llm_result() -> None:
    """Issue sumipan/nexus#6529 — one-shot API surface is call + LLMResult only."""
    import ghdag.llm as llm_pkg

    assert "call" in llm_pkg.__all__
    assert "LLMResult" in llm_pkg.__all__
    for removed in ("call_text", "call_managed", "TextResult", "ManagedResult"):
        assert removed not in llm_pkg.__all__
    with pytest.raises(ImportError):
        from ghdag.llm import call_text  # noqa: F401
    with pytest.raises(ImportError):
        from ghdag.llm import call_managed  # noqa: F401
    with pytest.raises(ImportError):
        from ghdag.llm import TextResult  # noqa: F401
    with pytest.raises(ImportError):
        from ghdag.llm import ManagedResult  # noqa: F401
