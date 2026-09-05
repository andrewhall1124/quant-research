"""Build the ATM-straddle panel this study runs on.

One row per (symbol, date): the ~30-dte at-the-money straddle chosen that day,
its quote and greeks at the close, and the quote of *those same two contracts*
at the next close. That second half is what makes a one-day hold measurable
without re-selecting a contract, and it is why this is a cached artefact rather
than something recomputed inside `analysis.py` — it is a full pass over the
option-greeks store, ~4,600 symbol-years and 60 GB.

Run it directly to (re)build the cache:

    uv run python -m research.iv_zscore_reversion.panel
"""

from __future__ import annotations

import argparse
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import polars as pl

from data_access_layer import (
    MissingDataset,
    UntrustedSymbolYear,
    available_option_symbols,
    load_option_greeks,
)
from data_access_layer import paths

RESULTS_DIR = Path(__file__).resolve().parent / "results"
PANEL_PATH = RESULTS_DIR / "straddle_panel.parquet"

# Selection targets. The chain is read wider than the straddle is selected so
# that a contract picked at t is still in the frame at t+1 after a day of spot
# drift — otherwise a big move would silently delete exactly the observations
# that matter most.
TARGET_DTE = 30
SELECT_DTE_MIN, SELECT_DTE_MAX = 20, 45
READ_DTE_MIN, READ_DTE_MAX = 15, 55
SELECT_MONEYNESS = 0.05
READ_MONEYNESS = 0.30
# ~3% of contract-days fail to invert and come back pinned near 0.5 with a
# +/-100 error. The straddle's IV is the whole signal, so those are dropped.
MAX_IV_ERROR = 0.02

CHAIN_COLUMNS = [
    "date", "expiration", "strike", "right", "dte", "moneyness",
    "bid", "ask", "mid", "implied_vol", "iv_error",
    "delta", "vega", "gamma", "theta", "underlying_price", "volume",
]


def read_chain(symbol: str, years: list[int]) -> pl.DataFrame:
    """The near-dated, near-the-money slice of one symbol's whole history."""
    return load_option_greeks(
        symbol,
        years=years,
        min_dte=READ_DTE_MIN,
        max_dte=READ_DTE_MAX,
        max_moneyness=READ_MONEYNESS,
        max_iv_error=MAX_IV_ERROR,
        quoted_only=True,
        columns=CHAIN_COLUMNS,
        trusted_only=False,
    )


def pair_legs(chain_df: pl.DataFrame) -> pl.DataFrame:
    """Collapse the chain to one row per (date, expiration, strike) straddle.

    An inner join on the two rights is the point: a strike quoted on only one
    side is not a straddle, and dropping it here rather than later keeps the
    strike search from ever selecting a half-priced pair.
    """
    legs = {}
    for right in ("CALL", "PUT"):
        tag = right.lower()[0]  # "c" / "p"
        legs[right] = chain_df.filter(pl.col("right") == right).select(
            "date", "expiration", "strike", "dte", "underlying_price",
            *[
                pl.col(name).alias(f"{tag}_{name}")
                for name in ("bid", "ask", "mid", "implied_vol",
                             "delta", "vega", "gamma", "theta", "volume")
            ],
        )
    return legs["CALL"].join(
        legs["PUT"].drop("dte", "underlying_price"),
        on=["date", "expiration", "strike"],
        how="inner",
    )


def add_straddle_terms(pairs_df: pl.DataFrame) -> pl.DataFrame:
    """Straddle-level quote, greeks and spread from the two legs.

    `vega` is ThetaData's per-share sensitivity to a 1.00 move in vol, so a
    100-share contract moves `vega` dollars per *vol point* — the units the
    sizing in `analysis.py` works in, with no further scaling.
    """
    return pairs_df.with_columns(
        (pl.col("c_mid") + pl.col("p_mid")).alias("straddle_mid"),
        ((pl.col("c_ask") - pl.col("c_bid")) + (pl.col("p_ask") - pl.col("p_bid")))
        .alias("straddle_spread"),
        ((pl.col("c_implied_vol") + pl.col("p_implied_vol")) / 2).alias("straddle_iv"),
        (pl.col("c_vega") + pl.col("p_vega")).alias("straddle_vega"),
        (pl.col("c_delta") + pl.col("p_delta")).alias("straddle_delta"),
        (pl.col("c_gamma") + pl.col("p_gamma")).alias("straddle_gamma"),
        (pl.col("c_theta") + pl.col("p_theta")).alias("straddle_theta"),
        (pl.col("c_volume") + pl.col("p_volume")).alias("straddle_volume"),
    )


def select_straddles(straddles_df: pl.DataFrame) -> pl.DataFrame:
    """One straddle per date: nearest expiry to 30 dte, nearest strike to spot.

    Expiry first, then strike, and never the other way round — picking the
    globally closest-to-spot strike would hop between expirations day to day
    and turn a term-structure move into signal.
    """
    with_target = (
        straddles_df.filter(
            pl.col("dte").is_between(SELECT_DTE_MIN, SELECT_DTE_MAX)
        )
        .with_columns((pl.col("dte") - TARGET_DTE).abs().alias("dte_gap"))
    )
    if with_target.is_empty():
        return with_target
    chosen_expiry = (
        with_target.sort("date", "dte_gap", "dte")
        .group_by("date")
        .first()
        .select("date", pl.col("expiration").alias("target_expiration"))
    )
    return (
        with_target.join(chosen_expiry, on="date")
        .filter(pl.col("expiration") == pl.col("target_expiration"))
        .with_columns(
            (pl.col("strike") / pl.col("underlying_price") - 1)
            .abs().alias("strike_gap")
        )
        .filter(pl.col("strike_gap") <= SELECT_MONEYNESS)
        .sort("date", "strike_gap", "strike")
        .group_by("date")
        .first()
        .drop("target_expiration", "dte_gap")
        .sort("date")
    )


def attach_next_day(selected_df: pl.DataFrame, straddles_df: pl.DataFrame) -> pl.DataFrame:
    """Join each selected straddle to its own quote at the next session.

    The next session is the next date *this symbol's chain* prints, not a
    calendar day, so a market holiday shortens nothing and a missing symbol-day
    is skipped rather than silently spanning two sessions.
    """
    sessions = selected_df.select("date").unique().sort("date")
    next_session = sessions.with_columns(
        pl.col("date").shift(-1).alias("next_date")
    )
    tomorrow = straddles_df.select(
        pl.col("date").alias("next_date"),
        "expiration",
        "strike",
        pl.col("straddle_mid").alias("next_straddle_mid"),
        pl.col("straddle_spread").alias("next_straddle_spread"),
        pl.col("straddle_iv").alias("next_straddle_iv"),
        pl.col("straddle_vega").alias("next_straddle_vega"),
        pl.col("straddle_delta").alias("next_straddle_delta"),
        pl.col("dte").alias("next_dte"),
        pl.col("underlying_price").alias("next_underlying_price"),
    )
    return (
        selected_df.join(next_session, on="date", how="left")
        .join(tomorrow, on=["next_date", "expiration", "strike"], how="left")
        .sort("date")
    )


def build_symbol(symbol: str, years: list[int]) -> pl.DataFrame | None:
    """The whole per-symbol pipeline, or None if the symbol has nothing usable."""
    try:
        chain_df = read_chain(symbol, years)
    except (MissingDataset, UntrustedSymbolYear):
        return None
    if chain_df.is_empty():
        return None
    straddles_df = add_straddle_terms(pair_legs(chain_df))
    if straddles_df.is_empty():
        return None
    selected_df = select_straddles(straddles_df)
    if selected_df.is_empty():
        return None
    panel_df = attach_next_day(selected_df, straddles_df)
    return panel_df.with_columns(pl.lit(symbol).alias("symbol")).select(
        "symbol", "date", "next_date", "expiration", "strike", "dte", "next_dte",
        "underlying_price", "next_underlying_price",
        "straddle_mid", "next_straddle_mid",
        "straddle_spread", "next_straddle_spread",
        "straddle_iv", "next_straddle_iv",
        "straddle_vega", "next_straddle_vega",
        "straddle_delta", "next_straddle_delta",
        "straddle_gamma", "straddle_theta", "straddle_volume",
        "c_mid", "p_mid", "c_implied_vol", "p_implied_vol",
        "c_delta", "p_delta", "c_vega", "p_vega",
    )


def build_panel(years: list[int], workers: int = 8) -> pl.DataFrame:
    """Every symbol on disk, in parallel. Reads are IO-bound, so threads."""
    symbols = sorted(
        {symbol for year in years for symbol in available_option_symbols(year=year)}
    )
    print(f"{len(symbols)} symbols across {years[0]}-{years[-1]}")
    frames, done, started = [], 0, time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(build_symbol, symbol, years): symbol for symbol in symbols}
        for future in as_completed(futures):
            done += 1
            frame = future.result()
            if frame is not None:
                frames.append(frame)
            if done % 50 == 0:
                print(f"  {done}/{len(symbols)}  {time.time() - started:.0f}s")
    panel_df = pl.concat(frames, how="vertical_relaxed").sort("symbol", "date")
    print(f"panel: {panel_df.height:,} symbol-days in {time.time() - started:.0f}s")
    return panel_df


def load_panel() -> pl.DataFrame:
    """The cached panel, with the command to rebuild it if it is not there."""
    if not PANEL_PATH.exists():
        raise MissingDataset(
            f"no straddle panel at {PANEL_PATH}. Create it with:\n"
            "  uv run python -m research.iv_zscore_reversion.panel"
        )
    return pl.read_parquet(PANEL_PATH)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--years", type=int, nargs="*", default=None)
    args = parser.parse_args()
    years = args.years or paths.available_years("option_greeks")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    panel_df = build_panel(years, workers=args.workers)
    panel_df.write_parquet(PANEL_PATH)
    print(f"wrote {PANEL_PATH}")


if __name__ == "__main__":
    main()
