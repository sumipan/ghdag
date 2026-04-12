"""ghdag.shr.config — ShrConfig dataclass と JSON 読み書き。"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path


CONFIG_PATH: Path = Path.home() / ".ghdag" / "shr.json"
RUNNER_DIR: Path = Path.home() / ".ghdag" / "runner"


@dataclass
class ShrConfig:
    repo: str
    labels: list[str]
    runner_dir: str
    process_name: str


def load_config() -> ShrConfig:
    """CONFIG_PATH から ShrConfig を読み込む。ファイルがなければ FileNotFoundError。"""
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"shr.json が見つかりません: {CONFIG_PATH}")
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return ShrConfig(**data)


def save_config(config: ShrConfig) -> None:
    """CONFIG_PATH に ShrConfig を書き込む。親ディレクトリがなければ作成する。"""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(asdict(config), indent=2, ensure_ascii=False), encoding="utf-8")
