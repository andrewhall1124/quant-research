"""Check the inverted implied vol against the vendor's own.

The study's IV measure is inverted from mids, because there is no VIX per name
and the free tier carries no implied vol. `data_pipelines.option_greeks` now
pulls ThetaData's own `implied_vol` for a growing subset of the universe, which
turns the study's largest unmeasured assumption into a measured one: is a
Black-76 inversion of an American option's mid close enough to the vendor's
number to carry a horse race?

The comparison is like for like. Both series take the same two expirations
bracketing 30 days, the same near-the-money strikes, and interpolate in total
variance to exactly 30 days. Only the per-contract volatility differs: the
study inverts Black-76 off the parity forward, the vendor runs its own model
off `underlying_price`.

Runs over whatever `data_store/option_greeks/` currently holds, so it grows as
that pull does.
"""

from datetime import date

import numpy as np
import polars as pl

import data_access_layer as dal
from data_access_layer import paths
from research.single_name_vol.panel import TARGET_DAYS

# Contract-days whose inversion failed come back pinned at 0.5 with an error of
# +/-100; anything past a vol point of error is not a quote we should compare to.
MAX_IV_ERROR = 0.01
MAX_MONEYNESS = 0.05


def vendor_atm_vol(expiry_df: pl.DataFrame) -> float:
    """Vendor implied vol at the strike nearest the money, calls and puts averaged."""
    nearest = expiry_df.select(pl.col("moneyness").abs().min()).item()
    atm_df = expiry_df.filter(pl.col("moneyness").abs() == nearest)
    return float(atm_df["implied_vol"].mean())


def build_vendor_iv(symbol: str, start: date | None = None, end: date | None = None) -> pl.DataFrame:
    """Daily 30-day ATM implied vol from the vendor's own per-contract IV."""
    chain_df = dal.load_option_greeks(
        symbol,
        start,
        end,
        min_dte=3,
        max_dte=90,
        max_moneyness=MAX_MONEYNESS,
        max_iv_error=MAX_IV_ERROR,
    ).filter(pl.col("implied_vol") > 0)
    if chain_df.height == 0:
        return pl.DataFrame(schema={"date": pl.Date, "vendor_iv": pl.Float64})

    rows = []
    for (quote_date,), day_df in chain_df.group_by(["date"], maintain_order=True):
        available = sorted(day_df["dte"].unique().to_list())
        below = [dte for dte in available if dte <= TARGET_DAYS]
        above = [dte for dte in available if dte > TARGET_DAYS]
        if not below or not above:
            continue

        near_ttm, far_ttm = below[-1] / 365.0, above[0] / 365.0
        near_vol = vendor_atm_vol(day_df.filter(pl.col("dte") == below[-1]))
        far_vol = vendor_atm_vol(day_df.filter(pl.col("dte") == above[0]))
        if not (np.isfinite(near_vol) and np.isfinite(far_vol)):
            continue

        target_ttm = TARGET_DAYS / 365.0
        weight = (far_ttm - target_ttm) / (far_ttm - near_ttm)
        total_variance = weight * near_vol**2 * near_ttm + (1 - weight) * far_vol**2 * far_ttm
        rows.append({"date": quote_date, "vendor_iv": float(np.sqrt(total_variance / target_ttm))})

    if not rows:
        return pl.DataFrame(schema={"date": pl.Date, "vendor_iv": pl.Float64})
    return pl.DataFrame(rows).sort("date")


def compare_measures(panel_df: pl.DataFrame) -> pl.DataFrame:
    """Join the study's IV to the vendor's, for every name that has both."""
    study_df = (
        panel_df.filter(pl.col("horizon") == panel_df["horizon"].min())
        .select("symbol", "date", pl.col("IV").alias("study_iv"))
        .unique(["symbol", "date"])
    )
    available = set(paths.available_option_symbols()) & set(study_df["symbol"].unique())

    frames = []
    for symbol in sorted(available):
        vendor_df = build_vendor_iv(symbol)
        if vendor_df.height == 0:
            continue
        frames.append(
            study_df.filter(pl.col("symbol") == symbol).join(vendor_df, on="date", how="inner")
        )
    if not frames:
        return pl.DataFrame(schema={"symbol": pl.Utf8, "date": pl.Date})
    return pl.concat(frames).with_columns(
        ((pl.col("study_iv") - pl.col("vendor_iv")) * 100).alias("difference_points")
    )


def summarize(comparison_df: pl.DataFrame) -> pl.DataFrame:
    """One row per name: how far apart the two measures are, and how they co-move."""
    return (
        comparison_df.group_by("symbol")
        .agg(
            pl.len().alias("days"),
            pl.corr("study_iv", "vendor_iv").alias("corr"),
            pl.col("difference_points").mean().alias("mean_difference_points"),
            pl.col("difference_points").abs().median().alias("median_abs_difference_points"),
        )
        .sort("symbol")
    )
