"""Synthetic canonical chains for the backtester tests."""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl

from voltium.loaders import OPTION_SCHEMA
from voltium.utils import bs


def third_friday(year: int, month: int) -> dt.date:
    first = dt.date(year, month, 1)
    offset = (4 - first.weekday()) % 7
    return first + dt.timedelta(days=offset + 14)


def make_chain(
    dates: list[dt.date],
    spots: dict[dt.date, float],
    ivs: dict[dt.date, float],
    symbol: str = "XYZ",
    expirations: list[dt.date] | None = None,
    strikes: np.ndarray | None = None,
    spread: float = 0.10,
    rate: float = 0.0,
    theta_override: dict[dt.date, tuple[float, float]] | None = None,
) -> pl.DataFrame:
    """Black-Scholes chain with vendor-style greeks (vega per vol point)."""
    if expirations is None:
        expirations = [third_friday(2024, m) for m in range(2, 8)]
    if strikes is None:
        strikes = np.arange(80.0, 121.0, 5.0)
    rows = []
    for date_ in dates:
        spot, iv = spots[date_], ivs[date_]
        for expiration in expirations:
            dte = (expiration - date_).days
            if dte <= 0:
                continue
            tau = dte / 365.0
            for strike in strikes:
                for right in ("C", "P"):
                    is_call = np.array([right == "C"])
                    args = (np.array([spot]), np.array([strike]), np.array([tau]), np.array([rate]), np.array([iv]))
                    mid = float(bs.price(*args, is_call)[0])
                    rows.append(
                        dict(
                            date=date_, symbol=symbol, expiration=expiration, strike=float(strike), right=right,
                            bid=max(mid - spread / 2, 0.01), ask=mid + spread / 2, mid=mid,
                            iv=iv, delta=float(bs.delta(*args, is_call)[0]), gamma=float(bs.gamma(*args)[0]),
                            theta=float(bs.theta(*args, is_call)[0]), vega=float(bs.vega(*args)[0]) / 100.0,
                            underlying=spot, oi=1000, volume=100,
                        )
                    )
    return pl.DataFrame(rows, schema=OPTION_SCHEMA)
