"""Pin import-time side effects — ENGINE_MODELS lazy load and Adapter resolution."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[2] / "src")


def test_import_llm_engines_does_not_call_load_engine_models() -> None:
    """import ghdag.llm.engines must not call load_engine_models (env / cwd YAML).

    Verified in a subprocess so other tests' sys.modules are not polluted.
    """
    code = r"""
from unittest.mock import patch
import ghdag.llm._config as config_mod
with patch.object(config_mod, "load_engine_models") as mock_load:
    import ghdag.llm.engines  # noqa: F401
    assert mock_load.call_count == 0, mock_load.call_count
print("OK")
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = _SRC
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "OK" in result.stdout


def test_import_workflow_resolves_get_adapter() -> None:
    """get_adapter('claude') returns an EngineAdapter right after importing ghdag.workflow."""
    import ghdag.workflow  # noqa: F401
    from ghdag.core.command import get_adapter

    adapter = get_adapter("claude")
    assert adapter.name == "claude"
    assert hasattr(adapter, "build_exec_record")
