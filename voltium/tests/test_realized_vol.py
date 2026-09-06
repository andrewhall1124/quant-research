import datetime as dt

import numpy as np
import polars as pl

from voltium.loaders import STOCK_SCHEMA
from voltium.providers.realized_vol import (
    HARConfig,
    HARForecaster,
    RealizedVolConfig,
    compute_realized_vol_panel,
)


def make_stock_panel(n_days: int = 500, n_symbols: int = 6, seed: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    start = dt.date(2022, 1, 3)
    dates = [start + dt.timedelta(days=i) for i in range(n_days)]
    rows = []
    for k in range(n_symbols):
        sigma = 0.15 + 0.05 * k
        close = 100.0
        for d in dates:
            open_ = close * np.exp(rng.normal(0, sigma / np.sqrt(252) / 3))
            close_new = open_ * np.exp(rng.normal(0, sigma / np.sqrt(252)))
            high = max(open_, close_new) * (1 + abs(rng.normal(0, 0.003)))
            low = min(open_, close_new) * (1 - abs(rng.normal(0, 0.003)))
            rows.append(dict(date=d, symbol=f"S{k}", open=open_, high=high, low=low, close=close_new, volume=1000))
            close = close_new
    return pl.DataFrame(rows, schema=STOCK_SCHEMA)


def test_yang_zhang_recovers_the_simulated_vol_roughly():
    stocks_df = make_stock_panel(n_days=1500, n_symbols=1)
    panel_df = compute_realized_vol_panel(stocks_df.lazy(), RealizedVolConfig()).collect()
    rv22 = panel_df["rv_22"].drop_nulls()
    assert 0.10 < rv22.median() < 0.22


def test_har_forecaster_has_no_lookahead():
    config = HARConfig(min_fit_rows=200, forward_window=60)
    stocks_df = make_stock_panel(n_days=500, n_symbols=6)
    panel_df = compute_realized_vol_panel(stocks_df.lazy(), RealizedVolConfig(forward_window=60)).collect()

    baseline = HARForecaster(config).forecast(panel_df)
    cutoff = dt.date(2023, 1, 1)
    # scramble everything after the cutoff: shuffle rows and rescale wildly
    future = panel_df.filter(pl.col("date") > cutoff)
    rng = np.random.default_rng(1)
    scrambled = future.with_columns(
        [pl.Series(c, rng.permutation(future[c].to_numpy()) * 7.0) for c in ("rv_1", "rv_5", "rv_22", "rv_fwd")]
    )
    perturbed_df = pl.concat([panel_df.filter(pl.col("date") <= cutoff), scrambled]).sort("date", "symbol")
    perturbed = HARForecaster(config).forecast(perturbed_df)

    # forecasts up to cutoff minus the forward window must be identical: those
    # fits only see targets that closed before the cutoff. Forecasts between
    # cutoff-60 and cutoff may legitimately change (their fit uses rv_fwd rows
    # dated after cutoff-60 whose forward window reaches past the cutoff? No:
    # rv_fwd at date d is only used once d + 60 <= fit date, so a row dated
    # before cutoff has rv_fwd computed from closes up to d + 60, which the
    # perturbation above never touches because rv_fwd itself was permuted only
    # for rows dated after the cutoff.)
    joined = baseline.join(perturbed, on=["date", "symbol"], suffix="_perturbed").filter(pl.col("date") <= cutoff)
    assert joined.height > 100
    assert (joined["rv_fcst"] - joined["rv_fcst_perturbed"]).abs().max() < 1e-12


def test_close_to_close_panel_matches_yang_zhang_scale():
    from voltium.providers.realized_vol import compute_close_to_close_panel

    stocks_df = make_stock_panel(n_days=800, n_symbols=2)
    yz = compute_realized_vol_panel(stocks_df.lazy()).collect()
    cc = compute_close_to_close_panel(stocks_df.lazy().select("date", "symbol", "close")).collect()
    assert cc.columns == yz.columns
    ratio = cc["rv_22"].drop_nulls().median() / yz["rv_22"].drop_nulls().median()
    assert 0.7 < ratio < 1.4
