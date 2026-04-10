"""
pipeline/config.py — パイプライン設定とモデル解決（Claude 前提）

移植元: tools/stash-developer/stash_developer/model_resolver.py
"""

from __future__ import annotations

import shlex
import sys
from dataclasses import dataclass


class ModelValidationError(Exception):
    """許可外モデル ID が指定された場合に送出。"""


@dataclass
class PipelineConfig:
    system_defaults: dict[str, str]
    allowed_models: set[str] | None = None
    validate_allowlist: bool = True


def resolve_models(config: PipelineConfig, overrides: dict[str, str]) -> dict[str, str]:
    """system_defaults に overrides をマージし、allowlist で検証。

    Args:
        config: パイプライン設定
        overrides: フェーズごとのモデル上書き。system_defaults に存在しないキーは無視
    Returns:
        phase → model の dict（system_defaults のキーすべてを含む）
    Raises:
        ModelValidationError: validate_allowlist=True かつ allowed_models に含まれないモデル
    """
    result = dict(config.system_defaults)
    for phase, model in overrides.items():
        if phase not in config.system_defaults:
            print(f"WARNING: 未知フェーズ {phase!r} は無視します", file=sys.stderr)
            continue
        result[phase] = model

    if config.validate_allowlist and config.allowed_models is not None:
        for phase, model in result.items():
            if model not in config.allowed_models:
                raise ModelValidationError(
                    f"許可リストにないモデル ID です: {model!r} (phase={phase}). "
                    f"許可リスト: {sorted(config.allowed_models)}"
                )

    return result


def build_agent_cmd(
    order_path: str,
    result_path: str,
    model: str,
    agent: str = "claude",
    prompt: str = "受け取った内容を実行して",
) -> str:
    """エージェント CLI コマンド文字列を構築。

    Returns:
        "cat queue/{order_path} | {agent} --model {model} -p {prompt}
         --dangerously-skip-permissions | tee -a queue/{result_path}"
        model, prompt は shlex.quote() でエスケープ
    """
    safe_model = shlex.quote(model)
    safe_prompt = shlex.quote(prompt)
    return (
        f"cat queue/{order_path}"
        f" | {agent} --model {safe_model}"
        f" -p {safe_prompt}"
        " --dangerously-skip-permissions"
        f" | tee -a queue/{result_path}"
    )
