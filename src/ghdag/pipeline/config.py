"""
pipeline/config.py — pipeline config and model resolution (assumes Claude)

Ported from: tools/stash-developer/stash_developer/model_resolver.py
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from ghdag.exceptions import GhdagError


class ModelValidationError(GhdagError):
    """Raised when an unauthorized model ID is specified."""


@dataclass
class PipelineConfig:
    system_defaults: dict[str, str]
    allowed_models: set[str] | None = None
    validate_allowlist: bool = True


def resolve_models(config: PipelineConfig, overrides: dict[str, str]) -> dict[str, str]:
    """Merge overrides into system_defaults and validate against the allowlist.

    Args:
        config: pipeline config
        overrides: per-phase model overrides. Keys absent from system_defaults are ignored
    Returns:
        dict of phase → model (contains all keys of system_defaults)
    Raises:
        ModelValidationError: validate_allowlist=True and a model is not in allowed_models
    """
    result = dict(config.system_defaults)
    for phase, model in overrides.items():
        if phase not in config.system_defaults:
            print(f"WARNING: unknown phase {phase!r} ignored", file=sys.stderr)
            continue
        result[phase] = model

    if config.validate_allowlist and config.allowed_models is not None:
        for phase, model in result.items():
            if model not in config.allowed_models:
                raise ModelValidationError(
                    f"Model ID not in allowlist: {model!r} (phase={phase}). "
                    f"Allowed: {sorted(config.allowed_models)}"
                )

    return result
