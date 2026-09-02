"""The cross-sectional score each name is ranked on.

A `Signal` takes the per-name-day position summary and returns it with a
`score` column added. Everything downstream — quantiles, sizing, P&L — is
signal-agnostic, so a new sort is a new class here and nothing else.

`VrpSignal` is the one this study is about: the variance risk premium, implied
vol minus a forecast of what will actually be realized over the life of the
contract.

Two choices in that definition are worth being explicit about.

**The IV is the traded contract's own.** Not a separately interpolated 30-day
measure. The premium being sorted on is the premium in the option being
bought, which removes a whole class of mismatch between signal and instrument.

**The forecast horizon matches the option tenor, not the holding period.** A
30-day straddle prices 30 calendar days ≈ 21 trading days of variance, so that
is what the forecast is compared against, and the sort keeps its meaning as the
holding-period grid varies. Matching the forecast to the holding period instead
would confound the two axes of the grid.

The forecast is the weak link and should be treated as such: `single_name_vol`
found GARCH and ARCH poorly calibrated on these names, and implied vol itself
the best available predictor of forward realized vol. `IV - E[RV]` is
therefore partly a measure of forecast error, which is why the forecast is a
parameter rather than a constant.
"""

import numpy as np
import polars as pl

from tools.vol_models import fit_and_forecast_horizons, trailing_realized_vol


class VrpSignal:
    """score = implied vol - forecast realized vol, in annualized vol points.

    Lowest score is the cheapest option relative to what the model expects to
    be realized, and is therefore the long side.
    """

    def __init__(self, forecast: str = "GARCH", horizon: int = 21, burn_in: int = 120):
        self.forecast = forecast
        self.horizon = horizon
        self.burn_in = burn_in
        self.name = f"vrp_{forecast.lower()}_{horizon}d"

    def attach(self, positions_df: pl.DataFrame, forecasts_df: pl.DataFrame) -> pl.DataFrame:
        """Join the forecast on and difference it against the traded IV.

        A name-day with no forecast (still inside its burn-in, or too short a
        history to fit) has no score and drops out of the sort that day rather
        than being scored against a fallback.
        """
        return (
            positions_df.join(
                forecasts_df.select("date", "symbol", pl.col(self.forecast).alias("forecast_rv")),
                on=["date", "symbol"],
                how="inner",
            )
            .with_columns((pl.col("atm_iv") - pl.col("forecast_rv")).alias("score"))
            .filter(pl.col("score").is_not_null(), pl.col("score").is_finite())
        )


class IvLevelSignal:
    """score = implied vol alone. The control.

    If a VRP sort does no better than sorting on raw IV, the forecast is adding
    nothing and the strategy is a low-vol/high-vol tilt wearing a costume. This
    exists so that comparison is always available.
    """

    def __init__(self):
        self.name = "iv_level"

    def attach(self, positions_df: pl.DataFrame, forecasts_df: pl.DataFrame) -> pl.DataFrame:
        return positions_df.with_columns(pl.col("atm_iv").alias("score"))


class IvZScoreSignal:
    """score = the traded IV against that name's own trailing IV history.

    Removes the persistent cross-sectional level — a high-vol name always looks
    rich in absolute terms — and sorts on the time-series deviation instead.
    Needs no return model at all, so it is the variant least exposed to
    forecast error, which is why `research/vrp_cross_section/` leads with it.

    Two properties worth being explicit about:

    * The window counts **observations, not calendar days**. It is applied
      after structure selection, so a name that could not be traded on some
      days contributes no row for them. That is the right behaviour — the
      history being compared against is the history of the thing being traded
      — but it means the lookback is not a fixed date range.
    * The window includes the current row, which is correct rather than
      lookahead: today's implied vol is known at today's close, which is when
      the position is formed.
    """

    def __init__(self, window: int = 60, min_periods: int = 40):
        self.window = window
        self.min_periods = min_periods
        self.name = f"iv_zscore_{window}d"

    def attach(self, positions_df: pl.DataFrame, forecasts_df: pl.DataFrame) -> pl.DataFrame:
        ordered = positions_df.sort("symbol", "date")
        return (
            ordered.with_columns(
                pl.col("atm_iv")
                .rolling_mean(self.window, min_samples=self.min_periods)
                .over("symbol")
                .alias("iv_mean"),
                pl.col("atm_iv")
                .rolling_std(self.window, min_samples=self.min_periods)
                .over("symbol")
                .alias("iv_std"),
            )
            .with_columns(
                ((pl.col("atm_iv") - pl.col("iv_mean")) / pl.col("iv_std")).alias("score")
            )
            .filter(pl.col("score").is_not_null(), pl.col("score").is_finite())
        )


def build_forecasts(
    returns_df: pl.DataFrame,
    horizon: int = 21,
    burn_in: int = 120,
    refit_every: int = 5,
    models: tuple[str, ...] = ("GARCH", "ARCH"),
) -> pl.DataFrame:
    """Out-of-sample RV forecasts per (symbol, date), using information through t.

    Reuses `tools.vol_models`, so the forecasts here are the same objects
    `research/single_name_vol/` scored — which is what makes that study's
    calibration findings directly applicable to this signal.

    `returns_df` must be split-adjusted log returns; see
    `tools.backtest.engine.build_returns`. Every model is fitted on an
    expanding window at each origin, so nothing here sees the future.
    """
    frames = []
    grouped = returns_df.sort("symbol", "date").group_by(["symbol"], maintain_order=True)
    for position, ((symbol,), symbol_df) in enumerate(grouped, start=1):
        returns = symbol_df["ret"].to_numpy()
        dates = symbol_df["date"].to_list()
        if len(returns) < burn_in + horizon:
            continue

        columns = {
            "date": dates,
            "symbol": [symbol] * len(dates),
            "RV": trailing_realized_vol(returns, horizon),
        }
        for model in models:
            columns[model] = fit_and_forecast_horizons(
                returns, [horizon], model, burn_in, refit_every
            )[horizon]
        frames.append(pl.DataFrame(columns))
        if position % 25 == 0:
            print(f"  forecasts {position} names", flush=True)

    if not frames:
        raise ValueError("no symbol had enough history to forecast")
    return (
        pl.concat(frames, how="vertical_relaxed")
        .filter(pl.all_horizontal(pl.col(m).is_not_null() for m in models))
        .sort("date", "symbol")
    )
