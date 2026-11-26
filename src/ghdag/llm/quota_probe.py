"""One-shot LLM CLI probe for quota usage windows (does not touch quota state)."""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass

from ghdag.core.capabilities import TEXT_ONLY, Permission
from ghdag.core.command import build_llm_cmd
from ghdag.core.exceptions import GhdagError
from ghdag.core.models.metrics import QuotaWindow
from ghdag.llm.adapters import get_output_adapter
from ghdag.llm.engines import validate_engine_model

PROBE_PROMPT = "Reply with OK only."

PROBE_DEFAULT_MODELS: dict[str, str] = {
    "claude": "claude-haiku-4-5-20251001",
}


class QuotaProbeUnsupportedError(GhdagError):
    """Raised when quota probe is requested for an unsupported engine."""

    code = "QUOTA_PROBE_UNSUPPORTED_ENGINE"
    exit_code = 2


@dataclass(frozen=True)
class QuotaProbeResult:
    engine: str
    model: str
    returncode: int
    windows: tuple[QuotaWindow, ...]
    stderr: str


def probe_quota_windows(
    engine: str,
    *,
    model: str | None = None,
    timeout: int = 120,
) -> QuotaProbeResult:
    if engine not in PROBE_DEFAULT_MODELS:
        raise QuotaProbeUnsupportedError(
            f"quota probe does not support engine {engine!r}",
            hint=f"supported engines: {sorted(PROBE_DEFAULT_MODELS)}",
        )

    resolved_model = model if model is not None else PROBE_DEFAULT_MODELS[engine]
    resolved_model = validate_engine_model(engine, resolved_model)

    permission = Permission(
        stream=True,
        disallowed_tools=TEXT_ONLY.disallowed_tools,
    )
    argv = build_llm_cmd(
        engine,
        resolved_model,
        PROBE_PROMPT,
        permission=permission,
        isolation=True,
    )
    argv += ["--tools", "", "--no-session-persistence", "--strict-mcp-config"]

    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            completed = subprocess.run(
                argv,
                input=PROBE_PROMPT,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=temp_dir,
            )
        except FileNotFoundError as exc:
            target = exc.filename or argv[0]
            return QuotaProbeResult(
                engine=engine,
                model=resolved_model,
                returncode=127,
                windows=(),
                stderr=f"failed to launch {target}: {exc}",
            )
        except subprocess.TimeoutExpired:
            return QuotaProbeResult(
                engine=engine,
                model=resolved_model,
                returncode=124,
                windows=(),
                stderr=f"probe timed out after {timeout} seconds",
            )

    adapter = get_output_adapter(engine)
    windows = adapter.extract_quota_windows(
        completed.stdout.encode("utf-8"),
        completed.stderr.encode("utf-8"),
    )
    return QuotaProbeResult(
        engine=engine,
        model=resolved_model,
        returncode=completed.returncode,
        windows=windows,
        stderr=completed.stderr,
    )


__all__ = [
    "PROBE_DEFAULT_MODELS",
    "PROBE_PROMPT",
    "QuotaProbeResult",
    "QuotaProbeUnsupportedError",
    "probe_quota_windows",
]
