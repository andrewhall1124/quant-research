"""Alpha: expected daily P&L per dollar of vega.

    alpha_i = -IC * sigma_idio_i * z_i

`z` is the richness score from `signals.py` (positive = implied rich), so the
sign is flipped: a rich name gets negative alpha and the optimizer shorts
its vega. `sigma_idio` is the daily idiosyncratic vol of the name's
per-unit-vega straddle P&L from the risk model, which puts the alpha in the
same units as the covariance the optimizer trades it off against. `IC` is a
config parameter, 0.04 by default, matching atium's `compute_alphas`.
"""

from __future__ import annotations

import datetime as dt
from abc import ABC
from dataclasses import dataclass

import polars as pl

from voltium.providers.base import Provider
from voltium.providers.signals import SignalProvider
from voltium.risk_model.base import RiskModelConstructor


class AlphaProvider(Provider, ABC):
    """`get(date) -> (date, symbol, alpha)`."""


@dataclass(frozen=True)
class AlphaConfig:
    ic: float = 0.04
    short_rich: bool = True


class ICScaledAlpha(AlphaProvider):
    def __init__(
        self,
        signal: SignalProvider,
        risk_model_constructor: RiskModelConstructor,
        config: AlphaConfig = AlphaConfig(),
    ) -> None:
        self.signal = signal
        self.risk_model_constructor = risk_model_constructor
        self.config = config

    def get(self, date_: dt.date) -> pl.DataFrame:
        signal_df = self.signal.get(date_)
        if signal_df.is_empty():
            return pl.DataFrame(schema={"date": pl.Date, "symbol": pl.Utf8, "alpha": pl.Float64})
        model = self.risk_model_constructor.get_risk_model(date_)
        idio_df = pl.DataFrame({"symbol": model.symbols, "idio_vol": model.idio_vol(model.symbols)})
        sign = -1.0 if self.config.short_rich else 1.0
        return (
            signal_df.join(idio_df, on="symbol", how="inner")
            .with_columns((sign * self.config.ic * pl.col("idio_vol") * pl.col("signal")).alias("alpha"))
            .select("date", "symbol", "alpha")
            .sort("symbol")
        )
