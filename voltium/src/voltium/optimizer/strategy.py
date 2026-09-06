"""Risk model -> alphas -> optimizer, on a rebalance date.

atium's `OptimizationStrategy` in the dollar-vega vocabulary. The universe
handed to the optimizer is the intersection of the risk model's names, the
names with a chain today, and (optionally) the point-in-time index members;
names held but no longer eligible are kept so the optimizer can unwind
them rather than the trade generator dumping them.
"""

from __future__ import annotations

import datetime as dt

import polars as pl

from voltium.optimizer.base import Optimizer
from voltium.portfolio import Portfolio
from voltium.providers.alphas import AlphaProvider
from voltium.providers.base import PanelProvider
from voltium.providers.universe import UniverseProvider
from voltium.risk_model.base import RiskModelConstructor
from voltium.strategy import TARGET_SCHEMA, Strategy


class OptimizationStrategy(Strategy):
    def __init__(
        self,
        alpha_provider: AlphaProvider,
        risk_model_constructor: RiskModelConstructor,
        optimizer: Optimizer,
        universe: UniverseProvider | None = None,
        gap_freq: PanelProvider | None = None,
    ) -> None:
        self.alpha_provider = alpha_provider
        self.risk_model_constructor = risk_model_constructor
        self.optimizer = optimizer
        self.universe = universe
        self.gap_freq = gap_freq

    def generate_targets(self, date_: dt.date, chain_df: pl.DataFrame, portfolio: Portfolio) -> pl.DataFrame:
        alphas_df = self.alpha_provider.get(date_)
        eligible = set(chain_df["symbol"].unique().to_list())
        if self.universe is not None:
            eligible &= set(self.universe.get(date_)["symbol"].to_list())
        held = set(portfolio.symbols())
        alphas_df = alphas_df.filter(pl.col("symbol").is_in(sorted(eligible | held)))

        previous_df = pl.DataFrame(
            {"symbol": portfolio.symbols(), "dollar_vega": [portfolio.dollar_vega(s) for s in portfolio.symbols()]},
            schema={"symbol": pl.Utf8, "dollar_vega": pl.Float64},
        )
        gap_df = self.gap_freq.get(date_).select("symbol", "gap_freq") if self.gap_freq is not None else None
        risk_model = self.risk_model_constructor.get_risk_model(date_)
        targets_df = self.optimizer.optimize(date_, alphas_df, risk_model, previous_df, gap_df)
        return targets_df.select(list(TARGET_SCHEMA))
