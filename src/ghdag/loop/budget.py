"""loop/budget.py — LoopBudget: 四次元予算管理"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class BudgetExceededError(Exception):
    """予算超過例外。超過した全次元名を dimensions に保持する。"""

    def __init__(self, dimensions: list[str], limits: dict[str, float], actuals: dict[str, float]) -> None:
        self.dimensions = dimensions
        self._limits = limits
        self._actuals = actuals
        parts = [
            f"{dim}(limit={limits[dim]}, actual={actuals[dim]})"
            for dim in dimensions
        ]
        super().__init__(f"BudgetExceeded: {', '.join(parts)}")


@dataclass(frozen=True)
class LoopBudget:
    """四次元予算（wall_clock / token / cost / steps）の不変データクラス。

    None フィールドは無制限（check/remaining でスキップ）。
    """

    wall_clock: float | None = None
    token: int | None = None
    cost: float | None = None
    steps: int | None = None

    def check(
        self,
        *,
        elapsed: float = 0.0,
        tokens: int = 0,
        cost: float = 0.0,
        step: int = 0,
    ) -> None:
        """全次元を評価し、1つ以上超過していれば BudgetExceededError を raise する。

        全次元を評価してから raise（早期 return しない）。
        step は 1-indexed の消費予約: check(step=N) は N 回目に入る直前の確認。
        """
        exceeded: list[str] = []
        limits: dict[str, float] = {}
        actuals: dict[str, float] = {}

        if self.wall_clock is not None and elapsed >= self.wall_clock:
            exceeded.append("wall_clock")
            limits["wall_clock"] = float(self.wall_clock)
            actuals["wall_clock"] = float(elapsed)

        if self.token is not None and tokens >= self.token:
            exceeded.append("token")
            limits["token"] = float(self.token)
            actuals["token"] = float(tokens)

        if self.cost is not None and cost >= self.cost:
            exceeded.append("cost")
            limits["cost"] = float(self.cost)
            actuals["cost"] = float(cost)

        if self.steps is not None and step >= self.steps:
            exceeded.append("steps")
            limits["steps"] = float(self.steps)
            actuals["steps"] = float(step)

        if exceeded:
            raise BudgetExceededError(exceeded, limits, actuals)

    def remaining(
        self,
        *,
        elapsed: float = 0.0,
        tokens: int = 0,
        cost: float = 0.0,
        step: int = 0,
    ) -> dict[str, float]:
        """各次元の残余を返す。None 次元は結果に含めない。"""
        result: dict[str, float] = {}

        if self.wall_clock is not None:
            result["wall_clock"] = float(self.wall_clock) - elapsed

        if self.token is not None:
            result["token"] = float(self.token) - tokens

        if self.cost is not None:
            result["cost"] = float(self.cost) - cost

        if self.steps is not None:
            result["steps"] = float(self.steps) - step

        return result


def from_skill_meta(meta: dict[str, Any]) -> LoopBudget:
    """SkillMeta 互換 dict から LoopBudget を構築する。

    未知のキーは無視する（前方互換性）。値が None のキーも無制限扱い。
    """
    steps = meta.get("max_iterations")
    wall_clock = meta.get("wall_clock_limit")
    token = meta.get("token_limit")
    cost = meta.get("cost_limit")

    return LoopBudget(
        steps=steps if isinstance(steps, int) else None,
        wall_clock=float(wall_clock) if wall_clock is not None else None,
        token=int(token) if token is not None else None,
        cost=float(cost) if cost is not None else None,
    )
