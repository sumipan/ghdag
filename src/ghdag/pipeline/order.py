"""
pipeline/order.py — テンプレート展開

移植元: tools/stash-developer/stash_developer/order_builder.py（インタフェースのみ）
"""

from __future__ import annotations

import string
from pathlib import Path
from typing import Protocol


class OrderBuilder(Protocol):
    def build_order(self, step_id: str, context: dict[str, str]) -> str:
        """ステップ ID とコンテキストから order 本文を生成。"""
        ...


class TemplateOrderBuilder:
    """ファイルベースの string.Template 展開 OrderBuilder 実装。"""

    def __init__(self, template_dir: str | Path):
        """
        Args:
            template_dir: テンプレートファイルを格納するディレクトリパス
        """
        self._template_dir = Path(template_dir)

    def build_order(self, step_id: str, context: dict[str, str]) -> str:
        """template_dir/{step_id}.md を読み込み、string.Template で context を展開。

        Args:
            step_id: テンプレートファイル名（拡張子なし）
            context: テンプレート変数の展開に使う dict
        Returns:
            展開後の order 本文（文字列）
        Raises:
            FileNotFoundError: template_dir/{step_id}.md が存在しない
            KeyError: テンプレートに含まれる変数が context に不足
        """
        template_path = self._template_dir / f"{step_id}.md"
        if not template_path.exists():
            raise FileNotFoundError(
                f"テンプレートファイルが見つかりません: {template_path}"
            )
        tmpl = string.Template(template_path.read_text(encoding="utf-8"))
        return tmpl.safe_substitute(context)
