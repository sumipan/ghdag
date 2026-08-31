"""import 時副作用の固定 — ENGINE_MODELS 遅延化と Adapter 解決。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[2] / "src")


def test_import_llm_engines_does_not_call_load_engine_models() -> None:
    """import ghdag.llm.engines が load_engine_models（env / cwd YAML）を呼ばないこと。

    他テストの sys.modules を汚染しないよう subprocess で検証する。
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
    """import ghdag.workflow 直後に get_adapter('claude') が EngineAdapter を返すこと。"""
    import ghdag.workflow  # noqa: F401
    from ghdag.core.command import get_adapter

    adapter = get_adapter("claude")
    assert adapter.name == "claude"
    assert hasattr(adapter, "build_exec_record")
