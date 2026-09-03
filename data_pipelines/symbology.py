"""Does a backfilled option root actually belong to the name we asked for?

ThetaData answers an unknown symbol with *some* instrument rather than an
error. Across a single year that is the BNY problem and `corporate_actions.py`
already catches it by comparing ThetaData's stock close to Yahoo's. Across a
backfill it becomes worse in two ways:

- The universe carries today's ticker at every historical date, because
  Wikipedia's constituent table only knows the current symbol. So a backfill
  asks for META in 2016, ELV in 2019, RTX in 2018 and PARA in 2021 — none of
  which existed then.
- Most of those fail safely: the modern root returns NoDataFound and the pull
  writes no file. But not all. `FI` on 2019-06-03 returns 40 contract-days at
  an underlying price of $5.92; Fiserv was FISV that day at $82.68. A backfill
  that trusted the universe would file another company's chain under Fiserv.

The stock endpoint cannot arbitrate, because the free stock tier refuses
anything before 2023-06-01. The option rows can: every greeks row carries
`underlying_price`, struck at the same instant as the quote. Yahoo serves a
renamed company's whole history under its modern ticker, so the two can be
compared — but **on returns, not on price levels**.

Comparing levels does not work, and the first version of this check that tried
it flagged APH and MNST for 2024 as different companies when both were right.
Yahoo back-adjusts splits into its closes and the two vendors disagree about
which splits apply to which window: APH's stored 2024 spot is exactly 2.000x
Yahoo's on all 252 days, MNST's flag traced to a split dated 2026-08-11, and
the repo's own stock-based `ticker_check` calls APH "ok" to 2.2e-8 over the
same 2025 dates where the raw ratio is a clean 2.0. Any level comparison is
really a test of split bookkeeping, in which a genuine rename hides.

Log returns are immune to all of it. A split is a single outlier day, and a
constant price ratio differences away to nothing. Measured on 2024:

    same company      corr 1.0000, median |return difference| 0.0000
    APH vs KO         corr -0.14,  0.0099
    MNST vs XOM       corr  0.06,  0.0111
    KO vs JNJ         corr  0.43,  0.0052   (two staples: the hardest case)

so the two populations do not come close to touching.

Costs no ThetaData requests at all: it reads the greeks already on disk.

    uv run python -m data_pipelines.symbology --years 2021 2022 2023 2024
"""

import argparse
import os
import time
from datetime import date
from pathlib import Path

import polars as pl
from dotenv import load_dotenv

from data_access_layer import paths
from data_pipelines.common import normalize_ticker
from data_pipelines.corporate_actions import fetch_yahoo

load_dotenv()

# The **median** return difference is the discriminator, not the correlation.
# Same company: 0.0000 (2e-8 in practice, which is float noise). Different
# company: 0.005 to 0.011. Five orders of magnitude of daylight, and a median
# cannot be moved by the one-day events below.
#
# Correlation is kept as a diagnostic but must not decide anything. A single
# unadjusted corporate action drags it far down while the company is right:
# FTV 2025 scores 0.70 and HON 0.97 on one spinoff day each, with 248 of 249
# days agreeing to 7e-8.
MAX_MEDIAN_RETURN_DIFFERENCE = 0.001
CLEARLY_WRONG_RETURN_DIFFERENCE = 0.005
MIN_OVERLAP_DAYS = 20

# A day where the two vendors disagree by more than this is a corporate action
# one of them has adjusted for and the other has not — a spinoff, or a split
# outside the window. Counted and reported, never used to condemn a symbol.
CORPORATE_ACTION_RETURN_GAP = 0.05

# A split lands as one ~0.69 log-return day in whichever series has not been
# back-adjusted. Dropping the tails keeps it from moving the correlation; the
# median would survive it anyway.
MAX_ABSOLUTE_LOG_RETURN = 0.3


def read_stored_spots(year: int) -> pl.DataFrame:
    """One (symbol, date, spot) row per stored symbol-day.

    The greeks file spells the option root in its  column, which is the
    dot-stripped spelling (BRKB), not the universe one.
    """
    directory = paths.option_dir("option_greeks", year)
    files = sorted(directory.glob("*.parquet"))
    if not files:
        raise FileNotFoundError(
            f"{directory} holds no chains. Pull the year first with:\n"
            f"  uv run python -m data_pipelines.option_greeks --year {year}"
        )
    return (
        pl.scan_parquet(files)
        .select(
            pl.col("symbol").alias("option_symbol"),
            pl.col("underlying_timestamp").dt.date().alias("date"),
            pl.col("underlying_price"),
        )
        .filter(pl.col("underlying_price") > 0)
        .group_by("option_symbol", "date")
        .agg(pl.col("underlying_price").median().alias("theta_spot"))
        .collect()
    )


def build_symbol_map(universe_path: Path) -> pl.DataFrame:
    """Option-root spelling back to the universe and Yahoo spellings.

    The option feed strips the dot (BRKB, BFB), so the mapping cannot be
    recovered from the root alone and is rebuilt from the universe instead.
    """
    tickers = pl.read_parquet(universe_path)["ticker"].unique().to_list()
    return pl.DataFrame(
        {
            "option_symbol": [normalize_ticker(t, "option") for t in tickers],
            "symbol": tickers,
            "yahoo_symbol": [normalize_ticker(t, "yahoo") for t in tickers],
        }
    ).unique("option_symbol")


def check_year(year: int, universe_path: Path, chunk_size: int) -> pl.DataFrame:
    spots_df = read_stored_spots(year)
    mapping_df = build_symbol_map(universe_path)
    named = spots_df.join(mapping_df, on="option_symbol", how="left")

    unmapped = named.filter(pl.col("symbol").is_null())["option_symbol"].unique().to_list()
    if unmapped:
        print(f"  {len(unmapped)} roots absent from the universe: {sorted(unmapped)[:8]}")
    named = named.drop_nulls("symbol")

    wanted = sorted(named["yahoo_symbol"].unique().to_list())
    print(f"  {len(wanted)} symbols, {named.height:,} symbol-days; asking Yahoo")
    quotes_df = fetch_yahoo(wanted, date(year, 1, 1), chunk_size)

    yahoo_df = (
        quotes_df.join(mapping_df, on="yahoo_symbol", how="inner")
        .select("symbol", "date", "yahoo_close")
        .filter(pl.col("yahoo_close") > 0)
        .filter(pl.col("date") <= date(year, 12, 31))
    )

    returns = (
        named.join(yahoo_df, on=["symbol", "date"], how="inner")
        .sort("symbol", "date")
        .with_columns(
            pl.col("theta_spot").log().diff().over("symbol").alias("theta_return"),
            pl.col("yahoo_close").log().diff().over("symbol").alias("yahoo_return"),
        )
        .drop_nulls(["theta_return", "yahoo_return"])
        .filter(
            (pl.col("theta_return").abs() < MAX_ABSOLUTE_LOG_RETURN)
            & (pl.col("yahoo_return").abs() < MAX_ABSOLUTE_LOG_RETURN)
        )
    )

    returns = returns.with_columns(
        (pl.col("theta_return") - pl.col("yahoo_return")).abs().alias("return_gap")
    )
    compared = returns.group_by("symbol").agg(
        pl.len().alias("overlap_days"),
        pl.corr("theta_return", "yahoo_return").alias("return_correlation"),
        pl.col("return_gap").median().alias("median_return_difference"),
        (pl.col("return_gap") > CORPORATE_ACTION_RETURN_GAP).sum()
        .alias("action_gap_days"),
    )

    return (
        named.group_by("symbol")
        .agg(pl.len().alias("theta_days"))
        .join(compared, on="symbol", how="left")
        .with_columns(pl.lit(year).alias("year"), pl.col("overlap_days").fill_null(0))
        .with_columns(
            pl.when(pl.col("overlap_days") < MIN_OVERLAP_DAYS)
            .then(pl.lit("thin_overlap"))
            .when(pl.col("median_return_difference") <= MAX_MEDIAN_RETURN_DIFFERENCE)
            .then(pl.lit("ok"))
            .when(pl.col("median_return_difference") >= CLEARLY_WRONG_RETURN_DIFFERENCE)
            .then(pl.lit("wrong_instrument"))
            .otherwise(pl.lit("suspect"))
            .alias("status")
        )
        .select(
            "year", "symbol", "theta_days", "overlap_days",
            "return_correlation", "median_return_difference",
            "action_gap_days", "status",
        )
        .sort("status", "symbol")
    )


def run(years: list[int], universe_path: str, chunk_size: int) -> None:
    started = time.perf_counter()
    frames = []
    for year in years:
        print(f"=== {year} ===")
        frames.append(check_year(year, Path(universe_path), chunk_size))

    checks_df = pl.concat(frames, how="vertical_relaxed")

    output_path = paths.SYMBOLOGY_CHECK
    # Accumulate. Each run is given one year, so writing only what it computed
    # would drop every year checked before it — and the table is the record of
    # which symbol-years are safe to use.
    if output_path.exists():
        kept = pl.read_parquet(output_path).filter(~pl.col("year").is_in(years))
        if kept.height and kept.columns == checks_df.columns:
            checks_df = pl.concat([kept, checks_df], how="vertical_relaxed")
        elif kept.height:
            print(
                f"  discarding {kept.height} rows from an older schema"
                f" ({sorted(set(kept['year'].to_list()))}); re-run those years"
            )
    checks_df = checks_df.sort("year", "status", "symbol")
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    checks_df.write_parquet(temp_path)
    os.replace(temp_path, output_path)

    print(f"\ndone in {time.perf_counter() - started:.1f}s | wrote {output_path}")
    print(checks_df.group_by("year", "status").len().sort("year", "status"))
    wrong = checks_df.filter(pl.col("status") == "wrong_instrument")
    if wrong.height:
        print("\nthese symbol-years are a different company and must be dropped:")
        print(wrong)
    suspect = checks_df.filter(pl.col("status") == "suspect")
    if suspect.height:
        print("\nthese landed between the two populations and need a human:")
        print(suspect)
    actions = checks_df.filter(pl.col("action_gap_days") > 0)
    if actions.height:
        print(
            f"\n{actions.height} symbol-years carry a day the two vendors disagree"
            " about by >5% — an unadjusted corporate action, most often a spinoff."
            " The symbol is fine; the return on that day is not:"
        )
        print(actions.select("year", "symbol", "action_gap_days", "median_return_difference"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, nargs="+", required=True)
    parser.add_argument("--universe", default=str(paths.UNIVERSE_HISTORY))
    parser.add_argument("--chunk-size", type=int, default=50)
    args = parser.parse_args()
    run(args.years, args.universe, args.chunk_size)


if __name__ == "__main__":
    main()
