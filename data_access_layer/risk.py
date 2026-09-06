"""The volatility risk model: reference straddle returns, factors, loadings.

Written by `data_pipelines.cli vol-risk-model`, estimated by voltium. The
"return" underneath everything is the daily P&L per dollar of vega of one
long delta-hedged ATM straddle per name, rolled at 20 DTE or on a 15% strike
drift — so a loading of 1 on the market factor means the name's hedged
straddle earns, per dollar of vega, what the SPX straddle earns per dollar
of its vega.
"""

from datetime import date

import polars as pl

from data_access_layer import paths
from data_access_layer.filters import deliver, in_window, require

PIPELINE = "data_pipelines.cli vol-risk-model"


def load_vol_reference_returns(
    symbol: str, start: date | None = None, end: date | None = None, lazy: bool = False
) -> pl.LazyFrame | pl.DataFrame:
    """One name's per-unit-vega straddle P&L: `pnl_per_vega`, `cost_per_vega`,
    `exit_cost_per_vega`, `dollar_vega`, `entry_vega`, `event`. `SPX` is the market."""
    path = require(paths.VOL_REFERENCE_RETURNS_DIR / f"{symbol.upper()}.parquet", PIPELINE)
    return deliver(in_window(pl.scan_parquet(path), start, end), lazy)


def load_vol_factor_returns(
    start: date | None = None, end: date | None = None, lazy: bool = False
) -> pl.LazyFrame | pl.DataFrame:
    """`(date, factor, ret)`: `market` plus one GICS sector factor each."""
    frame = pl.scan_parquet(require(paths.VOL_FACTOR_RETURNS, PIPELINE)).sort("date", "factor")
    return deliver(in_window(frame, start, end), lazy)


def load_vol_factor_loadings(
    start: date | None = None, end: date | None = None, lazy: bool = False
) -> pl.LazyFrame | pl.DataFrame:
    """`(date, symbol, factor, loading)`; a name loads on `market` and its own sector."""
    frame = pl.scan_parquet(require(paths.VOL_FACTOR_LOADINGS, PIPELINE)).sort("date", "symbol", "factor")
    return deliver(in_window(frame, start, end), lazy)


def load_vol_factor_covariances(
    start: date | None = None, end: date | None = None, lazy: bool = False
) -> pl.LazyFrame | pl.DataFrame:
    """`(date, factor_1, factor_2, covariance)`, daily, Ledoit-Wolf shrunk."""
    frame = pl.scan_parquet(require(paths.VOL_FACTOR_COVARIANCES, PIPELINE)).sort("date", "factor_1", "factor_2")
    return deliver(in_window(frame, start, end), lazy)


def load_vol_idio_vol(
    start: date | None = None, end: date | None = None, lazy: bool = False
) -> pl.LazyFrame | pl.DataFrame:
    """`(date, symbol, idio_vol)`, daily std of the residual per dollar of vega."""
    frame = pl.scan_parquet(require(paths.VOL_IDIO_VOL, PIPELINE)).sort("date", "symbol")
    return deliver(in_window(frame, start, end), lazy)
