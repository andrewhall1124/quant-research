"""Implied risk-free rates from option combinations, compared to SOFR.

Two estimators, for different reasons:

**Box spreads (primary).** For strikes K1 < K2 sharing an expiration,
put-call parity gives

    (C1 - P1) - (C2 - P2) = (K2 - K1) * exp(-r*T)

The spot level cancels, and with it the entire dividend stream. So a box needs
no dividend assumption and no underlying price -- only four option quotes. It
is a synthetic zero-coupon bond, and its yield is the market's borrowing rate.
Run on SPX/SPXW, which are European and cash-settled, so there is no early
exercise to contaminate it.

**Put-call parity (secondary).** For a single strike,

    C - P = S - K * exp(-r*T)   =>   r = -ln((S - (C - P)) / K) / T

This one *does* depend on spot and ignores dividends, so on dividend-paying
equities it is biased downward. That bias is the point: comparing it to the box
rate on the same names measures the market-implied dividend stream we have no
data for.

Quotes are bid/ask midpoints from the EOD snapshot.
"""

import sys
from pathlib import Path

import polars as pl

DAYS_PER_YEAR = 365.0


def load_index_chains(path: str = "data/index_options_2025") -> pl.DataFrame:
    files = sorted(Path(path).glob("*.parquet"))
    if not files:
        raise SystemExit(f"no chains in {path}; run research/pull_index_options.py")
    return (
        pl.concat([pl.read_parquet(f) for f in files], how="vertical_relaxed")
        .with_columns(
            pl.col("created").dt.date().alias("date"),
            pl.col("expiration").str.strptime(pl.Date, "%Y-%m-%d").alias("exp"),
            ((pl.col("bid") + pl.col("ask")) / 2).alias("mid"),
        )
        .filter((pl.col("bid") > 0) & (pl.col("ask") > pl.col("bid")))
        .with_columns(
            ((pl.col("exp") - pl.col("date")).dt.total_days() / DAYS_PER_YEAR).alias("T")
        )
    )


def build_synthetics(chains_df: pl.DataFrame) -> pl.DataFrame:
    """Collapse each (date, exp, strike) into one synthetic forward C - P."""
    calls = chains_df.filter(pl.col("right") == "CALL").select(
        "symbol", "date", "exp", "strike", "T",
        pl.col("mid").alias("call"),
        ((pl.col("ask") - pl.col("bid")) / 2).alias("call_half_spread"),
    )
    puts = chains_df.filter(pl.col("right") == "PUT").select(
        "symbol", "date", "exp", "strike",
        pl.col("mid").alias("put"),
        ((pl.col("ask") - pl.col("bid")) / 2).alias("put_half_spread"),
    )
    return (
        calls.join(puts, on=["symbol", "date", "exp", "strike"], how="inner")
        .with_columns(
            (pl.col("call") - pl.col("put")).alias("cmp"),
            (pl.col("call_half_spread") + pl.col("put_half_spread")).alias("leg_spread"),
        )
    )


def box_rates(
    synth_df: pl.DataFrame,
    spot_df: pl.DataFrame,
    moneyness_band: float = 0.15,
    min_width_frac: float = 0.05,
) -> pl.DataFrame:
    """Pair each expiration's strikes into boxes and solve for the implied rate.

    Strikes are first restricted to a band around spot. That restriction is not
    cosmetic: pairing the most extreme strikes available puts deep-ITM legs in
    the box, where quotes are wide and stale, and where an American option
    carries early-exercise value that breaks parity outright. Within the band,
    the k-th lowest strike is paired with the k-th highest, making boxes as wide
    as the band allows -- width matters because per-leg quote noise is roughly
    fixed, so a wider box has a better signal-to-noise ratio on the discount.
    """
    banded = (
        synth_df.join(spot_df, on=["symbol", "date"], how="inner")
        .filter((pl.col("strike") / pl.col("spot") - 1).abs() <= moneyness_band)
    )

    ranked = banded.with_columns(
        pl.col("strike").rank("ordinal").over("symbol", "date", "exp").alias("lo_rank"),
        pl.col("strike").rank("ordinal", descending=True).over("symbol", "date", "exp").alias("hi_rank"),
        pl.len().over("symbol", "date", "exp").alias("n_strikes"),
    ).filter(pl.col("n_strikes") >= 6)

    lo = ranked.filter(pl.col("lo_rank") <= 5).select(
        "symbol", "date", "exp", "T", "spot",
        pl.col("lo_rank").alias("pair"),
        pl.col("strike").alias("k1"),
        pl.col("cmp").alias("cmp1"),
        pl.col("leg_spread").alias("spread1"),
    )
    hi = ranked.filter(pl.col("hi_rank") <= 5).select(
        "symbol", "date", "exp",
        pl.col("hi_rank").alias("pair"),
        pl.col("strike").alias("k2"),
        pl.col("cmp").alias("cmp2"),
        pl.col("leg_spread").alias("spread2"),
    )

    boxes = (
        lo.join(hi, on=["symbol", "date", "exp", "pair"], how="inner")
        .filter(pl.col("k2") > pl.col("k1") * (1 + min_width_frac))
        .with_columns(
            (pl.col("k2") - pl.col("k1")).alias("width"),
            (pl.col("cmp1") - pl.col("cmp2")).alias("cost"),
            (pl.col("spread1") + pl.col("spread2")).alias("total_spread"),
        )
        .filter((pl.col("cost") > 0) & (pl.col("T") > 7 / DAYS_PER_YEAR))
        .with_columns(
            ((pl.col("width") / pl.col("cost")).log() / pl.col("T")).alias("implied_rate"),
            # Quote noise maps to a rate band of roughly spread/(width*T).
            (pl.col("total_spread") / (pl.col("width") * pl.col("T"))).alias("rate_precision"),
        )
        .filter(pl.col("rate_precision") < 0.01)
        .filter(pl.col("implied_rate").is_between(-0.02, 0.15))
    )
    return boxes


def parity_rates(synth_df: pl.DataFrame, spot_df: pl.DataFrame) -> pl.DataFrame:
    """Single-strike put-call parity rate; dividend-blind by construction."""
    joined = synth_df.join(spot_df, on=["symbol", "date"], how="inner").filter(
        (pl.col("T") > 7 / DAYS_PER_YEAR)
        & ((pl.col("strike") / pl.col("spot") - 1).abs() < 0.05)
    )
    return joined.with_columns(
        (-((pl.col("spot") - pl.col("cmp")) / pl.col("strike")).log() / pl.col("T")).alias(
            "implied_rate"
        )
    ).filter(pl.col("implied_rate").is_between(-0.05, 0.20))
