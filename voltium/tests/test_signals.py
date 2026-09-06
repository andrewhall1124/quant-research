import datetime as dt

import numpy as np
import polars as pl

from voltium.providers.signals import CrossSectionalVRPSignal, SignalConfig
from voltium.risk_model.spec import FactorSpec


def test_residuals_are_uncorrelated_with_each_factor():
    rng = np.random.default_rng(0)
    n_names, n_days = 200, 12
    names = [f"S{i}" for i in range(n_names)]
    sectors = [f"sec{i % 5}" for i in range(n_names)]
    dates = [dt.date(2024, 1, 2) + dt.timedelta(days=i) for i in range(n_days)]
    rows_surface, rows_fcst, rows_feat = [], [], []
    for d in dates:
        spx_vrp = rng.normal(0.2, 0.05)
        for i, s in enumerate(names):
            beta = 0.5 + i / n_names
            log_size = rng.normal(20, 1)
            idio = rng.uniform(0.15, 0.5)
            sector_effect = 0.1 * (i % 5)
            vrp = 0.3 * beta * spx_vrp + sector_effect + 0.02 * log_size - 0.2 * idio + rng.normal(0, 0.1)
            rv = 0.25
            rows_surface.append({"date": d, "symbol": s, "iv60": rv * np.exp(vrp)})
            rows_fcst.append({"date": d, "symbol": s, "rv_fcst": rv})
            rows_feat.append({"date": d, "symbol": s, "beta": beta, "log_size": log_size, "idio_vol": idio})
    spx_df = pl.DataFrame({"date": dates, "spx_vrp": [0.2] * n_days})
    signal = CrossSectionalVRPSignal(
        pl.DataFrame(rows_surface), pl.DataFrame(rows_fcst), pl.DataFrame(rows_feat),
        pl.DataFrame({"symbol": names, "sector": sectors}), FactorSpec(), SignalConfig(min_names=20), spx_df,
    )
    for d in dates[:3]:
        for factor, corr in signal.factor_correlations(d).items():
            assert abs(corr) < 1e-8, (factor, corr)
    out = signal.get(dates[-1])
    assert out.height == n_names
    assert abs(out["signal"].mean()) < 1e-6
    assert abs(out["signal"].std() - 1.0) < 0.05


def test_time_series_zscore_is_relative_to_each_names_own_history():
    from voltium.providers.signals import TimeSeriesIVZScoreSignal, TimeSeriesZScoreConfig

    dates = [dt.date(2024, 1, 1) + dt.timedelta(days=i) for i in range(60)]
    rows = []
    for i, d in enumerate(dates):
        rows.append({"date": d, "symbol": "FLAT", "iv60": 0.30})
        rows.append({"date": d, "symbol": "RISING", "iv60": 0.20 + 0.001 * i})
    surface_df = pl.DataFrame(rows)
    signal = TimeSeriesIVZScoreSignal(surface_df, TimeSeriesZScoreConfig(window=20, min_periods=10))
    last = signal.get(dates[-1])
    # a flat series has zero std and is dropped; a rising one is above its trailing mean
    assert last.filter(pl.col("symbol") == "FLAT").is_empty()
    assert last.filter(pl.col("symbol") == "RISING")["signal"][0] > 1.0
