"""Risk model -> alphas -> optimizer, on a rebalance date.

atium's `OptimizationStrategy` in the dollar-vega vocabulary. Eligibility
(chain ∩ universe, plus held names so they can be unwound) is the
`ScoreStrategy` base's; this class supplies the sizing step: hand the alphas,
the current book, the day's risk model and `gap_freq` to the optimizer.
"""

from __future__ import annotations

import datetime as dt

import polars as pl

from voltium.optimizer.base import Optimizer
from voltium.providers.alphas import AlphaProvider
from voltium.providers.base import PanelProvider
from voltium.providers.universe import UniverseProvider
from voltium.risk_model.base import RiskModelConstructor
from voltium.strategy import ScoreStrategy


class OptimizationStrategy(ScoreStrategy):
    def __init__(
        self,
        alpha_provider: AlphaProvider,
        risk_model_constructor: RiskModelConstructor,
        optimizer: Optimizer,
        universe: UniverseProvider | None = None,
        gap_freq: PanelProvider | None = None,
    ) -> None:
        super().__init__(alpha_provider, "alpha", universe)
        self.risk_model_constructor = risk_model_constructor
        self.optimizer = optimizer
        self.gap_freq = gap_freq

    def size(self, date_: dt.date, scores_df: pl.DataFrame, previous_df: pl.DataFrame) -> pl.DataFrame:
        gap_df = self.gap_freq.get(date_).select("symbol", "gap_freq") if self.gap_freq is not None else None
        risk_model = self.risk_model_constructor.get_risk_model(date_)
        return self.optimizer.optimize(date_, scores_df, risk_model, previous_df, gap_df)
