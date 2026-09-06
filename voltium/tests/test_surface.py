import datetime as dt

import numpy as np
import polars as pl

from voltium.loaders import OPTION_SCHEMA
from voltium.providers.surface import SurfaceConfig, build_surface_panel
from voltium.utils.interpolation import interpolate_total_variance


def make_flat_chain(iv: float, dtes: tuple[int, ...], spot: float = 100.0) -> pl.LazyFrame:
    date_ = dt.date(2024, 1, 2)
    rows = []
    for dte in dtes:
        expiration = date_ + dt.timedelta(days=dte)
        for strike, delta_c in ((95.0, 0.62), (100.0, 0.52), (105.0, 0.41)):
            for right, delta in (("C", delta_c), ("P", delta_c - 1.0)):
                rows.append(
                    dict(
                        date=date_, symbol="XYZ", expiration=expiration, strike=strike, right=right,
                        bid=1.0, ask=1.1, mid=1.05, iv=iv, delta=delta, gamma=0.01, theta=-0.01,
                        vega=0.1, underlying=spot, oi=100, volume=10,
                    )
                )
    return pl.DataFrame(rows, schema=OPTION_SCHEMA).lazy()


def test_flat_surface_returns_input_iv_at_all_tenors():
    iv = 0.32
    panel_df = build_surface_panel(make_flat_chain(iv, (10, 45, 80, 120)), SurfaceConfig()).collect()
    assert panel_df.height == 1
    for tenor in (30, 60, 90):
        assert abs(panel_df[f"iv{tenor}"][0] - iv) < 1e-12


def test_tenor_outside_pillars_is_null_without_extrapolation():
    panel_df = build_surface_panel(make_flat_chain(0.2, (10, 45)), SurfaceConfig()).collect()
    assert abs(panel_df["iv30"][0] - 0.2) < 1e-12
    assert panel_df["iv90"][0] is None


def test_total_variance_interpolation_is_linear_in_variance():
    # two pillars: 20% at 30d, 40% at 90d; total variance linear in t
    dte = np.array([30.0, 90.0])
    iv = np.array([0.2, 0.4])
    w30 = 0.04 * 30 / 365
    w90 = 0.16 * 90 / 365
    w60 = w30 + (w90 - w30) * 0.5
    expected = np.sqrt(w60 / (60 / 365))
    assert abs(interpolate_total_variance(dte, iv, 60) - expected) < 1e-12
