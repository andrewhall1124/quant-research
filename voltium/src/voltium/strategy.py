"""Strategy: what the book should look like on a rebalance date.

atium's `Strategy.generate_weights(date) -> (date, ticker, weight)` becomes
`generate_targets(date, chain_df, portfolio) -> (symbol, target_vega)`: the
unit is dollar vega, and the strategy sees the chain and the current book
because a vol target only makes sense for names that have a tradeable
straddle today.

`FixedTargetStrategy` is the hard-coded book used to validate the
backtester; `OptimizationStrategy` (risk model -> alpha -> optimizer) lives
in `optimizer/strategy.py` so this module does not import CVXPY.
"""

from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod

import polars as pl

from voltium.portfolio import Portfolio

TARGET_SCHEMA = {"symbol": pl.Utf8, "target_vega": pl.Float64}


class Strategy(ABC):
    @abstractmethod
    def generate_targets(self, date_: dt.date, chain_df: pl.DataFrame, portfolio: Portfolio) -> pl.DataFrame:
        """`(symbol, target_vega)` in dollars per vol point; long > 0."""


class FixedTargetStrategy(Strategy):
    def __init__(self, targets: dict[str, float]) -> None:
        self.targets = targets

    def generate_targets(self, date_: dt.date, chain_df: pl.DataFrame, portfolio: Portfolio) -> pl.DataFrame:
        return pl.DataFrame(
            {"symbol": list(self.targets), "target_vega": list(self.targets.values())},
            schema=TARGET_SCHEMA,
        )
