"""workflow/loader.py — ディレクトリからの YAML 読み込み、バリデーション"""

from __future__ import annotations

from pathlib import Path

import yaml

from ghdag.workflow.schema import (
    HandlerConfig,
    OnTriggerConfig,
    StepConfig,
    TriggerConfig,
    WorkflowConfig,
)


class ValidationError(ValueError):
    """YAML バリデーションエラー。"""


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
        configs.append(_parse(data, workflow_dir=directory.resolve()))

    return configs


def _validate(data: dict, filename: str) -> None:
    """必須フィールドの存在チェック。"""
    if "name" not in data:
        raise ValidationError(f"'name' フィールドが必須です: {filename}")
    if "triggers" not in data or not data["triggers"]:
        raise ValidationError(f"'triggers' フィールドが必須で空でない必要があります: {filename}")
    if "handlers" not in data or data["handlers"] is None:
        raise ValidationError(f"'handlers' フィールドが必須です: {filename}")

    # triggers の各エントリを検証
    for i, trigger in enumerate(data["triggers"]):
        if not isinstance(trigger, dict):
            raise ValidationError(f"triggers[{i}] はマッピングである必要があります: {filename}")
        if "label" not in trigger:
            raise ValidationError(f"triggers[{i}] に 'label' が必須です: {filename}")
        if "handler" not in trigger:
            raise ValidationError(f"triggers[{i}] に 'handler' が必須です: {filename}")

    # handlers の各エントリを検証
    handlers = data["handlers"]
    if not isinstance(handlers, dict):
        raise ValidationError(f"'handlers' はマッピングである必要があります: {filename}")

    for handler_name, handler_data in handlers.items():
        if handler_data is None:
            continue
        if not isinstance(handler_data, dict):
            raise ValidationError(f"ハンドラー '{handler_name}' はマッピングである必要があります: {filename}")

        # reset ハンドラーは steps 不要
        handler_type = handler_data.get("type")
        if handler_type == "reset":
            continue

        steps = handler_data.get("steps")
        if steps is None:
            raise ValidationError(f"ハンドラー '{handler_name}' に 'steps' が必須です: {filename}")

        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                raise ValidationError(f"ハンドラー '{handler_name}' の step[{i}] はマッピングである必要があります: {filename}")
            if "template" not in step:
                raise ValidationError(f"ハンドラー '{handler_name}' の step[{i}] に 'template' が必須です: {filename}")
            if "model" not in step:
                raise ValidationError(f"ハンドラー '{handler_name}' の step[{i}] に 'model' が必須です: {filename}")


def _parse(data: dict, *, workflow_dir: Path | None = None) -> WorkflowConfig:
    """バリデーション済み dict を WorkflowConfig に変換。"""
    triggers = [
        TriggerConfig(label=t["label"], handler=t["handler"])
        for t in data["triggers"]
    ]

    handlers: dict[str, HandlerConfig] = {}
    for name, h in data["handlers"].items():
        if h is None:
            h = {}

        handler_type = h.get("type")

        # on_trigger パース
        on_trigger_data = h.get("on_trigger")
        on_trigger = None
        if on_trigger_data and isinstance(on_trigger_data, dict):
            on_trigger = OnTriggerConfig(
                issue_context=on_trigger_data.get("issue_context", False)
            )

        context_hook = h.get("context_hook")

        if handler_type == "reset":
            handlers[name] = HandlerConfig(
                steps=[], on_trigger=on_trigger, type="reset",
                context_hook=context_hook,
            )
            continue

        steps = []
        for s in h.get("steps", []):
            steps.append(
                StepConfig(
                    id=s.get("id"),
                    template=s["template"],
                    model=s["model"],
                    depends=s.get("depends", []),
                )
            )

        handlers[name] = HandlerConfig(
            steps=steps, on_trigger=on_trigger, type=handler_type,
            context_hook=context_hook,
        )

    # template_dir の解決: 相対パスはワークフローファイルのディレクトリ基準
    raw_template_dir = data.get("template_dir")
    resolved_template_dir: str | None = None
    if raw_template_dir is not None:
        td = Path(raw_template_dir)
        if not td.is_absolute() and workflow_dir is not None:
            td = workflow_dir / td
        resolved_template_dir = str(td)

    return WorkflowConfig(
        name=data["name"],
        triggers=triggers,
        handlers=handlers,
        polling_interval=data.get("polling_interval", 30),
        template_dir=resolved_template_dir,
    )
