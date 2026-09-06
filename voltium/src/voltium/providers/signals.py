"""Cross-sectional variance-risk-premium signal.

    vrp = ln(iv60) - ln(rv_fcst)

is regressed cross-sectionally every day on the factors named by the shared
`FactorSpec` plus the controls in `SignalConfig`:

    market   beta_i * spx_vrp   (or beta_i * the vega-weighted mean vrp when
                                 no SPX surface is given)
    sector   the median vrp of the name's GICS sector
    controls log_size, idio_vol  (stock-return features)

The residual is averaged over `smoothing_days` sessions per name and
z-scored across names. A positive score means implied is rich relative to
the forecast after controlling for the factors: the book sells it.

Sign convention downstream: `alphas.py` flips the sign so that a positive
alpha is a *long* vega position; the signal itself is left as "richness".
"""

from __future__ import annotations

import datetime as dt
from abc import ABC
from dataclasses import dataclass

import numpy as np
import polars as pl

from voltium.providers.base import PanelProvider, Provider
from voltium.risk_model.spec import FactorSpec


@dataclass(frozen=True)
class SignalConfig:
    tenor: int = 60
    smoothing_days: int = 5
    controls: tuple[str, ...] = ("log_size", "idio_vol")
    winsor: float = 3.0
    min_names: int = 50


class SignalProvider(Provider, ABC):
    """`get(date) -> (date, symbol, signal)`; a cross-sectional z-score."""


def compute_vrp(surface_df: pl.DataFrame, forecast_df: pl.DataFrame, tenor: int) -> pl.DataFrame:
    """`(date, symbol, vrp)` from a surface panel and a forecast panel."""
    iv_col = f"iv{tenor}"
    return (
        surface_df.select("date", "symbol", iv_col)
        .join(forecast_df.select("date", "symbol", "rv_fcst"), on=["date", "symbol"], how="inner")
        .filter(pl.col(iv_col) > 0, pl.col("rv_fcst") > 0)
        .with_columns((pl.col(iv_col).log() - pl.col("rv_fcst").log()).alias("vrp"))
        .select("date", "symbol", "vrp")
    )


def residualize_daily(frame_df: pl.DataFrame, regressors: list[str], min_names: int) -> pl.DataFrame:
    """OLS of `vrp` on `regressors` with intercept, per date; returns residuals."""
    pieces = []
    for (day,), group in frame_df.sort("date").group_by("date", maintain_order=True):
        clean = group.drop_nulls(["vrp", *regressors])
        if clean.height < min_names:
            continue
        x = np.column_stack([np.ones(clean.height), clean.select(regressors).to_numpy()])
        y = clean["vrp"].to_numpy()
        beta, *_ = np.linalg.lstsq(x, y, rcond=None)
        pieces.append(clean.select("date", "symbol").with_columns(pl.Series("residual", y - x @ beta)))
    if not pieces:
        return pl.DataFrame(schema={"date": pl.Date, "symbol": pl.Utf8, "residual": pl.Float64})
    return pl.concat(pieces)


class CrossSectionalVRPSignal(SignalProvider, PanelProvider):
    def __init__(
        self,
        surface_df: pl.DataFrame,
        forecast_df: pl.DataFrame,
        features_df: pl.DataFrame,
        sectors_df: pl.DataFrame,
        spec: FactorSpec = FactorSpec(),
        config: SignalConfig = SignalConfig(),
        spx_vrp_df: pl.DataFrame | None = None,
    ) -> None:
        """`spx_vrp_df` is `(date, spx_vrp)`; None falls back to the universe mean.

        `features_df` needs `beta` plus every name in `config.controls`.
        """
        self.spec = spec
        self.config = config
        self.used_spx = spx_vrp_df is not None and spec.market_source == "spx"

        vrp_df = compute_vrp(surface_df, forecast_df, config.tenor)
        frame = vrp_df.join(features_df.select("date", "symbol", "beta", *config.controls), on=["date", "symbol"], how="left")
        frame = frame.join(sectors_df.select("symbol", "sector"), on="symbol", how="left")

        if self.used_spx:
            frame = frame.join(spx_vrp_df.select("date", pl.col("spx_vrp").alias("market_vrp")), on="date", how="left")
        else:
            frame = frame.with_columns(pl.col("vrp").mean().over("date").alias("market_vrp"))
        frame = frame.with_columns((pl.col("beta") * pl.col("market_vrp")).alias("market"))

        regressors = ["market"]
        if spec.use_sectors:
            frame = frame.with_columns(pl.col("vrp").median().over("date", "sector").alias("sector_median"))
            regressors.append("sector_median")
        regressors.extend(config.controls)
        self.regressors = regressors
        self.residuals_df = residualize_daily(frame, regressors, config.min_names)

        smoothed = (
            self.residuals_df.sort("symbol", "date")
            .with_columns(pl.col("residual").rolling_mean(config.smoothing_days, min_samples=1).over("symbol").alias("smooth"))
        )
        z = ((pl.col("smooth") - pl.col("smooth").mean()) / pl.col("smooth").std()).over("date")
        panel_df = (
            smoothed.with_columns(z.alias("signal"))
            .with_columns(pl.col("signal").clip(-config.winsor, config.winsor))
            .select("date", "symbol", "signal")
            .sort("date", "symbol")
        )
        self.frame_df = frame
        PanelProvider.__init__(self, panel_df)

    def factor_correlations(self, date_: dt.date) -> dict[str, float]:
        """Cross-sectional correlation of the residual with each regressor on a date."""
        merged = self.residuals_df.filter(pl.col("date") == date_).join(
            self.frame_df.filter(pl.col("date") == date_), on=["date", "symbol"]
        )
        return {r: float(merged.select(pl.corr("residual", r)).item()) for r in self.regressors}


@dataclass(frozen=True)
class TimeSeriesZScoreConfig:
    tenor: int = 60
    window: int = 250
    min_periods: int = 120
    winsor: float = 3.0
    use_log: bool = True


class TimeSeriesIVZScoreSignal(SignalProvider, PanelProvider):
    """Each name's IV against its own history: `(iv - mean) / std` over a trailing window.

    No cross-sectional regression, no forecast: the score is purely how far
    today's constant-maturity ATM IV (log IV by default) sits from its
    trailing `window`-session mean, in trailing standard deviations. Positive
    means rich relative to the name's own past, and `alphas.py` flips the
    sign so the book sells it. It is the classic mean-reversion-in-IV
    signal and a useful control for the residualised premium.
    """

    def __init__(self, surface_df: pl.DataFrame, config: TimeSeriesZScoreConfig = TimeSeriesZScoreConfig()) -> None:
        self.config = config
        iv_col = f"iv{config.tenor}"
        level = pl.col(iv_col).log() if config.use_log else pl.col(iv_col)
        panel_df = (
            surface_df.select("date", "symbol", iv_col)
            .filter(pl.col(iv_col) > 0)
            .sort("symbol", "date")
            .with_columns(level.alias("level"))
            .with_columns(
                pl.col("level").rolling_mean(config.window, min_samples=config.min_periods).over("symbol").alias("mean"),
                pl.col("level").rolling_std(config.window, min_samples=config.min_periods).over("symbol").alias("std"),
            )
            .filter(pl.col("std") > 0)
            .with_columns(((pl.col("level") - pl.col("mean")) / pl.col("std")).clip(-config.winsor, config.winsor).alias("signal"))
            .select("date", "symbol", "signal")
            .sort("date", "symbol")
        )
        PanelProvider.__init__(self, panel_df)
