"""Realized volatility from OHLC, and a forecast of it.

`compute_realized_vol_panel` builds the Yang-Zhang estimator over trailing
windows of 1, 5 and 22 sessions and the forward 60-session target, per
symbol, lazily. `HARForecaster` fits the Corsi (2009) HAR regression of the
log forward RV on the three trailing log RVs, pooled across names, on an
expanding window refit monthly. The fit at a date only uses rows whose
forward window closed before that date, so nothing in a forecast depends on
a later row (see `tests/test_realized_vol.py`).

Yang-Zhang over a window of n sessions:

    sigma^2 = sigma_overnight^2 + k * sigma_close^2 + (1 - k) * sigma_rs^2
    k = 0.34 / (1.34 + (n + 1) / (n - 1))

where sigma_overnight is the variance of ln(open / prev close), sigma_close
the variance of ln(close / open), and sigma_rs the Rogers-Satchell mean.
For n = 1 the variances collapse to the single day's squared terms and k is
taken as 0, i.e. rv_1 = overnight^2 + Rogers-Satchell.
"""

from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np
import polars as pl

from voltium.loaders import DataPaths, scan_stocks
from voltium.providers.base import PanelProvider, Provider

SESSIONS_PER_YEAR = 252


@dataclass(frozen=True)
class RealizedVolConfig:
    windows: tuple[int, ...] = (1, 5, 22)
    forward_window: int = 60
    min_price: float = 1.0


def yang_zhang_k(n: int) -> float:
    return 0.34 / (1.34 + (n + 1) / (n - 1)) if n > 1 else 0.0


def yang_zhang_variance(n: int) -> pl.Expr:
    """Annualised YZ variance over the trailing `n` sessions (inclusive)."""
    overnight = pl.col("overnight")
    close_open = pl.col("close_open")
    rs = pl.col("rs")
    if n == 1:
        daily = overnight**2 + rs
    else:
        k = yang_zhang_k(n)
        daily = (
            overnight.rolling_var(n)
            + k * close_open.rolling_var(n)
            + (1.0 - k) * rs.rolling_mean(n)
        )
    return daily * SESSIONS_PER_YEAR


def compute_realized_vol_panel(
    stocks: pl.LazyFrame, config: RealizedVolConfig = RealizedVolConfig()
) -> pl.LazyFrame:
    """`(date, symbol, rv_1, rv_5, rv_22, rv_fwd)` as annualised **vols**.

    `rv_fwd` on date d is the YZ vol over sessions d+1 .. d+forward_window,
    null until that window has closed. Names with a close below `min_price`
    are dropped for the day.
    """
    by = "symbol"
    if "split_ratio" not in stocks.collect_schema().names():
        stocks = stocks.with_columns(pl.lit(1.0).alias("split_ratio"))
    frame = (
        stocks.filter(pl.col("close") >= config.min_price)
        .sort("symbol", "date")
        .with_columns(
            (pl.col("open") * pl.col("split_ratio") / pl.col("close").shift(1).over(by)).log().alias("overnight"),
            (pl.col("close") / pl.col("open")).log().alias("close_open"),
            (
                (pl.col("high") / pl.col("close")).log() * (pl.col("high") / pl.col("open")).log()
                + (pl.col("low") / pl.col("close")).log() * (pl.col("low") / pl.col("open")).log()
            ).alias("rs"),
        )
        .with_columns(
            [yang_zhang_variance(n).over(by).sqrt().alias(f"rv_{n}") for n in config.windows]
        )
    )
    n = config.forward_window
    forward = yang_zhang_variance(n).shift(-n).over(by).sqrt().alias("rv_fwd")
    return frame.with_columns(forward).select(
        "date", "symbol", *[f"rv_{w}" for w in config.windows], "rv_fwd"
    )


def compute_close_to_close_panel(
    closes: pl.LazyFrame, config: RealizedVolConfig = RealizedVolConfig(), exclude_df: pl.DataFrame | None = None
) -> pl.LazyFrame:
    """The same panel from closes only: `(date, symbol, close[, split_ratio])`.

    Realized variance over `n` sessions is the mean squared split-adjusted
    log return, annualised; `rv_1` is the single day's squared return. This
    is the estimator that reaches the whole option history (see
    `loaders.scan_spot_from_chains`); Yang-Zhang needs OHLC and the stock
    file only starts mid-2023. Same columns, so the HAR is indifferent.

    `exclude_df` `(date, symbol)` names sessions whose squared return is left
    out of every window (earnings sessions, for a diffusive realized vol);
    the mean is then over the remaining sessions in the window, and `rv_1`
    is null on an excluded session.
    """
    by = "symbol"
    if "split_ratio" not in closes.collect_schema().names():
        closes = closes.with_columns(pl.lit(1.0).alias("split_ratio"))
    frame = (
        closes.filter(pl.col("close") >= config.min_price)
        .sort("symbol", "date")
        .with_columns(
            ((pl.col("close") * pl.col("split_ratio") / pl.col("close").shift(1).over(by)).log() ** 2).alias("squared")
        )
    )
    if exclude_df is not None:
        excluded = exclude_df.select("date", "symbol").unique().with_columns(pl.lit(True).alias("excluded")).lazy()
        frame = frame.join(excluded, on=["date", "symbol"], how="left").with_columns(
            pl.when(pl.col("excluded").fill_null(False)).then(None).otherwise(pl.col("squared")).alias("squared")
        )
    frame = frame.with_columns(pl.col("squared").is_not_null().cast(pl.Float64).alias("counted"))

    def variance(n: int) -> pl.Expr:
        if n == 1:
            return pl.col("squared") * SESSIONS_PER_YEAR
        daily = pl.col("squared").fill_null(0.0).rolling_sum(n) / pl.col("counted").rolling_sum(n)
        return daily * SESSIONS_PER_YEAR

    n = config.forward_window
    return (
        frame.with_columns([variance(w).over(by).sqrt().alias(f"rv_{w}") for w in config.windows])
        .with_columns(variance(n).shift(-n).over(by).sqrt().alias("rv_fwd"))
        .select("date", "symbol", *[f"rv_{w}" for w in config.windows], "rv_fwd")
    )


class RealizedVolProvider(Provider):
    """`get(date) -> (date, symbol, rv_1, rv_5, rv_22, rv_fwd)`."""


class YangZhangRealizedVol(RealizedVolProvider, PanelProvider):
    def __init__(self, panel_df: pl.DataFrame, config: RealizedVolConfig = RealizedVolConfig()) -> None:
        self.config = config
        PanelProvider.__init__(self, panel_df)

    @classmethod
    def from_store(
        cls,
        paths: DataPaths,
        symbols: list[str] | None,
        start: dt.date | None,
        end: dt.date | None,
        config: RealizedVolConfig = RealizedVolConfig(),
    ) -> "YangZhangRealizedVol":
        stocks = scan_stocks(paths, start, end, with_splits=True)
        if symbols is not None:
            stocks = stocks.filter(pl.col("symbol").is_in(symbols))
        return cls(compute_realized_vol_panel(stocks, config).collect(), config)


class RealizedVolForecaster(ABC):
    """Turns a realized-vol panel into `(date, symbol, rv_fcst)`."""

    @abstractmethod
    def forecast(self, panel_df: pl.DataFrame) -> pl.DataFrame: ...


@dataclass(frozen=True)
class HARConfig:
    windows: tuple[int, ...] = (1, 5, 22)
    forward_window: int = 60
    min_fit_rows: int = 1000
    refit: str = "monthly"


class HARForecaster(RealizedVolForecaster):
    """Pooled log-HAR, expanding window, refit on the first session of each month.

    On every refit date t the training set is every row with date <= t
    shifted back by `forward_window` sessions, so its `rv_fwd` is fully
    observed by t. Between refits the last coefficients are applied to each
    day's trailing RVs. Forecasts are of vol, with the lognormal correction
    exp(sigma_e^2 / 2) applied to the variance.
    """

    def __init__(self, config: HARConfig = HARConfig()) -> None:
        self.config = config
        self.coefficients_df: pl.DataFrame | None = None

    def forecast(self, panel_df: pl.DataFrame, apply_df: pl.DataFrame | None = None) -> pl.DataFrame:
        """Forecasts for every row of `panel_df`, plus for `apply_df` rows.

        `apply_df` rows (same columns) are scored with the pooled coefficients
        but never enter the fit — that is how the SPX forecast is produced
        from the single-name regression.
        """
        cfg = self.config
        features = [f"rv_{w}" for w in cfg.windows]
        panel_df = panel_df.with_columns(pl.lit(True).alias("in_fit"))
        if apply_df is not None:
            panel_df = pl.concat([panel_df, apply_df.select(panel_df.columns[:-1]).with_columns(pl.lit(False).alias("in_fit"))])
        clean = (
            panel_df.filter(
                pl.all_horizontal([pl.col(f) > 0 for f in features]),
                pl.all_horizontal([pl.col(f).is_finite() for f in features]),
            )
            .with_columns([pl.col(f).log().alias(f"x_{f}") for f in features])
            .with_columns(
                pl.when(pl.col("rv_fwd") > 0).then((pl.col("rv_fwd") ** 2).log()).otherwise(None).alias("y")
            )
            .sort("date", "symbol")
        )
        sessions = clean["date"].unique().sort().to_list()
        position = {d: i for i, d in enumerate(sessions)}
        refit_dates = select_refit_dates(sessions, cfg.refit)

        x_cols = [f"x_{f}" for f in features]
        beta: np.ndarray | None = None
        residual_var = 0.0
        coefficient_rows: list[dict] = []
        pieces: list[pl.DataFrame] = []
        next_refit = 0
        for day in sessions:
            while next_refit < len(refit_dates) and refit_dates[next_refit] <= day:
                fit_day = refit_dates[next_refit]
                next_refit += 1
                cutoff_index = position[fit_day] - cfg.forward_window
                if cutoff_index < 0:
                    continue
                cutoff = sessions[cutoff_index]
                train = clean.filter(pl.col("date") <= cutoff, pl.col("y").is_not_null(), pl.col("in_fit"))
                if train.height < cfg.min_fit_rows:
                    continue
                design = np.column_stack([np.ones(train.height), train.select(x_cols).to_numpy()])
                target = train["y"].to_numpy()
                beta, *_ = np.linalg.lstsq(design, target, rcond=None)
                residual_var = float(np.var(target - design @ beta))
                coefficient_rows.append(
                    {"date": fit_day, "rows": train.height, "const": beta[0], "residual_var": residual_var}
                    | {f"b_{f}": b for f, b in zip(features, beta[1:])}
                )
            if beta is None:
                continue
            today = clean.filter(pl.col("date") == day)
            design = np.column_stack([np.ones(today.height), today.select(x_cols).to_numpy()])
            variance = np.exp(design @ beta + residual_var / 2.0)
            pieces.append(today.select("date", "symbol").with_columns(pl.Series("rv_fcst", np.sqrt(variance))))

        self.coefficients_df = pl.DataFrame(coefficient_rows) if coefficient_rows else None
        if not pieces:
            return pl.DataFrame(schema={"date": pl.Date, "symbol": pl.Utf8, "rv_fcst": pl.Float64})
        return pl.concat(pieces)


def select_refit_dates(sessions: list[dt.date], refit: str) -> list[dt.date]:
    if refit == "daily":
        return list(sessions)
    if refit == "monthly":
        out, last = [], None
        for d in sessions:
            key = (d.year, d.month)
            if key != last:
                out.append(d)
                last = key
        return out
    raise ValueError(f"unknown refit schedule {refit!r}")


class ForecastProvider(PanelProvider):
    """`get(date) -> (date, symbol, rv_fcst)`; a forecaster applied to a panel."""

    def __init__(self, realized_vol: RealizedVolProvider, forecaster: RealizedVolForecaster) -> None:
        if not isinstance(realized_vol, PanelProvider):
            raise TypeError("ForecastProvider needs a panel-backed RealizedVolProvider")
        self.forecaster = forecaster
        PanelProvider.__init__(self, forecaster.forecast(realized_vol.panel_df))
