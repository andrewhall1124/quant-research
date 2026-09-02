"""A 30-day ATM implied vol built from the SPXW chain, to check VIX.

The study uses VIX as its implied-vol measure because it is free, EOD, and
covers the whole sample. This module rebuilds a comparable number the hard way
from option mids, so the report can show the two agree before leaning on VIX.

Method, per day:
  1. keep the two expirations bracketing 30 calendar days,
  2. imply the forward from put-call parity at the strike where call and put
     mids are closest (no dividend-yield assumption needed),
  3. invert Black-76 for the call and the put at the strike nearest that
     forward, and average,
  4. interpolate the two expirations in total variance to exactly 30 days.

Step 2 is what makes this trustworthy on an index: assuming a dividend yield
instead would put a few vol points of error straight into the level.
"""

from datetime import date

import numpy as np
import polars as pl
from scipy.optimize import brentq
from scipy.stats import norm

import data_access_layer as dal

TARGET_DAYS = 30


def black76_price(forward: float, strike: float, ttm: float, vol: float, is_call: bool) -> float:
    if vol <= 0 or ttm <= 0:
        return max(0.0, (forward - strike) if is_call else (strike - forward))
    d1 = (np.log(forward / strike) + 0.5 * vol**2 * ttm) / (vol * np.sqrt(ttm))
    d2 = d1 - vol * np.sqrt(ttm)
    if is_call:
        return forward * norm.cdf(d1) - strike * norm.cdf(d2)
    return strike * norm.cdf(-d2) - forward * norm.cdf(-d1)


def implied_vol(price: float, forward: float, strike: float, ttm: float, is_call: bool) -> float:
    """Invert Black-76. Returns NaN when the quote sits outside no-arbitrage bounds."""
    intrinsic = max(0.0, (forward - strike) if is_call else (strike - forward))
    if not np.isfinite(price) or price <= intrinsic or ttm <= 0:
        return np.nan
    try:
        return brentq(
            lambda vol: black76_price(forward, strike, ttm, vol, is_call) - price,
            1e-4,
            5.0,
            xtol=1e-6,
        )
    except ValueError:
        return np.nan


def atm_vol_for_expiry(expiry_df: pl.DataFrame, discount_rate: float) -> tuple[float, float]:
    """(time to expiry in years, ATM implied vol) for one date-expiration slice."""
    ttm = expiry_df["dte"][0] / 365.0
    quoted_df = expiry_df.pivot(on="right", index="strike", values="mid")
    # Thin single-name expirations can quote one side only, leaving no pair to
    # take parity from. Nothing to invert, so say so.
    if not {"CALL", "PUT"}.issubset(quoted_df.columns):
        return ttm, np.nan
    paired_df = (
        quoted_df.drop_nulls(["CALL", "PUT"])
        .with_columns((pl.col("CALL") - pl.col("PUT")).abs().alias("gap"))
        .sort("gap")
    )
    if paired_df.height == 0:
        return ttm, np.nan

    # Put-call parity at the tightest strike gives the forward.
    parity = paired_df.row(0, named=True)
    forward = parity["strike"] + np.exp(discount_rate * ttm) * (parity["CALL"] - parity["PUT"])

    atm = (
        paired_df.with_columns((pl.col("strike") - forward).abs().alias("distance"))
        .sort("distance")
        .row(0, named=True)
    )
    call_vol = implied_vol(atm["CALL"] * np.exp(discount_rate * ttm), forward, atm["strike"], ttm, True)
    put_vol = implied_vol(atm["PUT"] * np.exp(discount_rate * ttm), forward, atm["strike"], ttm, False)
    return ttm, float(np.nanmean([call_vol, put_vol]))


def build_atm_iv_series(
    start: date | None = None, end: date | None = None, root: str = "SPXW"
) -> pl.DataFrame:
    """Daily 30-day ATM implied vol interpolated from the index chain."""
    chain_df = dal.load_option_greeks(
        root,
        index=True,
        start=start,
        end=end,
        min_dte=7,
        max_dte=60,
        max_moneyness=0.03,
    ).filter(pl.col("bid") > 0)

    rate_df = dal.load_yields("13w", start, end).select("date", pl.col("yield").alias("rate"))
    rates = dict(zip(rate_df["date"].to_list(), rate_df["rate"].to_list()))

    rows = []
    for (quote_date,), day_df in chain_df.group_by(["date"], maintain_order=True):
        discount_rate = rates.get(quote_date, 0.04)
        available = sorted(day_df["dte"].unique().to_list())
        below = [dte for dte in available if dte <= TARGET_DAYS]
        above = [dte for dte in available if dte > TARGET_DAYS]
        if not below or not above:
            continue

        near_ttm, near_vol = atm_vol_for_expiry(
            day_df.filter(pl.col("dte") == below[-1]), discount_rate
        )
        far_ttm, far_vol = atm_vol_for_expiry(
            day_df.filter(pl.col("dte") == above[0]), discount_rate
        )
        if not (np.isfinite(near_vol) and np.isfinite(far_vol)):
            continue

        # Interpolate in total variance, the way VIX does, then re-annualize.
        target_ttm = TARGET_DAYS / 365.0
        weight = (far_ttm - target_ttm) / (far_ttm - near_ttm)
        total_variance = weight * near_vol**2 * near_ttm + (1 - weight) * far_vol**2 * far_ttm
        rows.append({"date": quote_date, "atm_iv": float(np.sqrt(total_variance / target_ttm))})

    return pl.DataFrame(rows).sort("date")
