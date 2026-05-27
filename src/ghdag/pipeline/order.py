"""
pipeline/order.py — テンプレート展開

移植元: tools/stash-developer/stash_developer/order_builder.py（インタフェースのみ）
"""

from __future__ import annotations

import string
from pathlib import Path
from typing import Protocol


class TemplateVariableError(ValueError, KeyError):
    """テンプレート変数不足エラー。ValueError と KeyError 両方の subclass。"""

    def __str__(self) -> str:
        return self.args[0] if self.args else ""


class OrderBuilder(Protocol):
    def build_order(self, step_id: str, context: dict[str, str]) -> str:
        """ステップ ID とコンテキストから order 本文を生成。"""
        ...


def _check_missing_vars(
    tmpl: string.Template,
    context: dict[str, str],
    source_label: str,
) -> None:
    required = set(tmpl.get_identifiers())
    provided = set(context.keys())
    missing = sorted(required - provided)
    if missing:
        raise TemplateVariableError(
            f"テンプレート展開エラー ({source_label}): "
            f"未定義変数: {missing}, "
            f"利用可能なキー: {sorted(provided)}"
        )


class InlineOrderBuilder:
    """テンプレート文字列を直接受け取り string.Template で展開する OrderBuilder。

    ファイル I/O を行わないため、scheduler 等の動的プロンプト生成に使う。
    """

    def build_order(self, step_id: str, context: dict[str, str]) -> str:
        """step_id をテンプレート本文として string.Template で展開する。

        Args:
            step_id: テンプレート本文（TemplateOrderBuilder ではファイル名だが、
                     InlineOrderBuilder ではテンプレート文字列そのもの）。
                     LLMPipelineAPI.submit が StepConfig.template をそのまま渡す。
            context: テンプレート変数の展開に使う dict
        Returns:
            展開後の order 本文
        Raises:
            ValueError: テンプレートに含まれる変数が context に不足、または不正な $ 構文
        """
        tmpl = string.Template(step_id)
        _check_missing_vars(tmpl, context, "inline")
        try:
            return tmpl.substitute(context)
        except ValueError as e:
            raise ValueError(
                f"テンプレート展開エラー (inline): {e}"
            ) from e


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
            ValueError: テンプレートに含まれる変数が context に不足、または不正な $ 構文
        """
        template_path = self._template_dir / f"{step_id}.md"
        if not template_path.exists():
            raise FileNotFoundError(
                f"テンプレートファイルが見つかりません: {template_path}"
            )
        tmpl = string.Template(template_path.read_text(encoding="utf-8"))
        _check_missing_vars(tmpl, context, str(template_path))
        try:
            return tmpl.substitute(context)
        except ValueError as e:
            raise ValueError(
                f"テンプレート展開エラー ({template_path}): {e}"
            ) from e
