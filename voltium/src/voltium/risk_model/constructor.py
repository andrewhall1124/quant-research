"""Builds a `FactorRiskModel` for a rebalance date from the reference panels.

Mirrors atium's `FactorRiskModelConstructor.get_risk_model(date)`, except
that atium's providers hand it pre-estimated loadings and covariances; here
the estimation happens inside, on the trailing `spec.window` sessions of
per-unit-vega P&L, so that the same `FactorSpec` also drives the signal.
Models are memoised per date.
"""

from __future__ import annotations

import datetime as dt

import polars as pl

from voltium.risk_model.base import RiskModelConstructor
from voltium.risk_model.factor import FactorRiskModel, build_factor_returns, estimate_factor_risk_model
from voltium.risk_model.spec import FactorSpec


class FactorRiskModelConstructor(RiskModelConstructor):
    def __init__(
        self,
        returns_df: pl.DataFrame,
        sectors_df: pl.DataFrame,
        spec: FactorSpec = FactorSpec(),
        market_df: pl.DataFrame | None = None,
    ) -> None:
        """`returns_df`: (date, symbol, pnl_per_vega, dollar_vega) for the whole
        history; `market_df`: (date, pnl_per_vega) of the SPX reference unit."""
        self.returns_df = returns_df.select("date", "symbol", "pnl_per_vega", "dollar_vega")
        self.sectors_df = sectors_df.select("symbol", "sector")
        self.spec = spec
        self.market_df = market_df
        self.sessions = self.returns_df["date"].unique().sort().to_list()
        self.cache: dict[dt.date, FactorRiskModel] = {}

    def window_start(self, date_: dt.date) -> dt.date:
        eligible = [d for d in self.sessions if d <= date_]
        if len(eligible) < self.spec.min_observations:
            raise ValueError(f"only {len(eligible)} sessions of reference P&L up to {date_}")
        return eligible[-self.spec.window] if len(eligible) >= self.spec.window else eligible[0]

    def factor_returns(self, date_: dt.date) -> pl.DataFrame:
        start = self.window_start(date_)
        window = self.returns_df.filter(pl.col("date").is_between(start, date_))
        market = None
        if self.market_df is not None:
            market = self.market_df.filter(pl.col("date").is_between(start, date_))
        return build_factor_returns(window, self.sectors_df, self.spec, market)

    def get_risk_model(self, date_: dt.date) -> FactorRiskModel:
        if date_ in self.cache:
            return self.cache[date_]
        start = self.window_start(date_)
        window = self.returns_df.filter(pl.col("date").is_between(start, date_))
        model = estimate_factor_risk_model(window, self.factor_returns(date_), self.sectors_df, self.spec)
        self.cache[date_] = model
        return model
