"""OrderBuilder Protocol."""

from __future__ import annotations

from typing import Protocol


class OrderBuilder(Protocol):
    def build_order(self, step_id: str, context: dict[str, str]) -> str:
        """ステップ ID とコンテキストから order 本文を生成。"""
        ...
