"""workflow/loader.py — ディレクトリからの YAML 読み込み、バリデーション"""

from __future__ import annotations

from pathlib import Path

import yaml

from ghdag.workflow.schema import PhaseHandler, TriggerConfig, WorkflowConfig


def load_workflows(directory: str | Path) -> list[WorkflowConfig]:
    """指定ディレクトリ配下の *.yml / *.yaml を読み込み、WorkflowConfig のリストを返す。

    Args:
        directory: ワークフロー YAML の配置ディレクトリ
    Returns:
        WorkflowConfig のリスト
    Raises:
        FileNotFoundError: ディレクトリが存在しない
        ValueError: YAML パースエラーまたは必須フィールド不足
    """
    directory = Path(directory)
    if not directory.exists():
        raise FileNotFoundError(f"ディレクトリが存在しません: {directory}")

    paths = sorted(directory.glob("*.yml")) + sorted(directory.glob("*.yaml"))
    configs: list[WorkflowConfig] = []

    for path in paths:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            raise ValueError(f"YAML パースエラー ({path.name}): {e}") from e

        if not isinstance(data, dict):
            raise ValueError(f"YAML ルートはマッピングである必要があります: {path.name}")

        _validate(data, path.name)

        triggers = [TriggerConfig(label=t["label"]) for t in data["triggers"]]
        handlers = [
            PhaseHandler(
                name=h["name"],
                template=h["template"],
                agent=h.get("agent", "claude"),
                model=h.get("model"),
            )
            for h in data["handlers"]
        ]
        configs.append(
            WorkflowConfig(
                name=data["name"],
                triggers=triggers,
                handlers=handlers,
                polling_interval=data.get("polling_interval", 30),
            )
        )

    return configs


def _validate(data: dict, filename: str) -> None:
    """必須フィールドの存在チェック。"""
    if "name" not in data:
        raise ValueError(f"'name' フィールドが必須です: {filename}")
    if "triggers" not in data or not data["triggers"]:
        raise ValueError(f"'triggers' フィールドが必須で空でない必要があります: {filename}")
    if "handlers" not in data or data["handlers"] is None:
        raise ValueError(f"'handlers' フィールドが必須です: {filename}")
