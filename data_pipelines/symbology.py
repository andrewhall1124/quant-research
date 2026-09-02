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
`underlying_price`, struck at the same instant as the quote. Comparing that to
Yahoo's close for the same name — Yahoo serves a renamed company's whole
history under its modern ticker — separates "we pulled the right company" from
"we pulled whatever answered".

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
from data_pipelines.corporate_actions import build_actions, fetch_yahoo, split_factors

load_dotenv()

# A median disagreement wider than this is a different instrument, not a data
# quirk. `corporate_actions.py` uses the same tolerance against stock closes.
CLOSE_TOLERANCE = 0.02
MIN_OVERLAP_DAYS = 20


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

    pairs = list(
        zip(
            mapping_df["symbol"].to_list(),
            mapping_df["yahoo_symbol"].to_list(),
        )
    )
    actions_df = build_actions(quotes_df, pairs)

    # ThetaData spot is raw; Yahoo back-adjusts splits into its close. Undo the
    # splits that fall after each date so the two are on the same footing.
    factors_df = split_factors(actions_df, named.select("symbol", "date"))
    adjusted = (
        named.join(factors_df, on=["symbol", "date"], how="left")
        .with_columns(pl.col("split_factor").fill_null(1.0))
        .with_columns((pl.col("theta_spot") / pl.col("split_factor")).alias("theta_adjusted"))
    )

    yahoo_df = (
        quotes_df.join(mapping_df, on="yahoo_symbol", how="inner")
        .select("symbol", "date", "yahoo_close")
        .filter(pl.col("yahoo_close") > 0)
    )

    compared = (
        adjusted.join(yahoo_df, on=["symbol", "date"], how="inner")
        .with_columns(
            ((pl.col("theta_adjusted") - pl.col("yahoo_close")).abs() / pl.col("yahoo_close"))
            .alias("difference")
        )
        .group_by("symbol")
        .agg(
            pl.len().alias("overlap_days"),
            pl.col("difference").median().alias("median_difference"),
            pl.col("difference").quantile(0.99).alias("p99_difference"),
        )
    )

    return (
        named.group_by("symbol")
        .agg(pl.len().alias("theta_days"))
        .join(compared, on="symbol", how="left")
        .with_columns(
            pl.lit(year).alias("year"),
            pl.col("overlap_days").fill_null(0),
        )
        .with_columns(
            pl.when(pl.col("overlap_days") < MIN_OVERLAP_DAYS)
            .then(pl.lit("thin_overlap"))
            .when(pl.col("median_difference") <= CLOSE_TOLERANCE)
            .then(pl.lit("ok"))
            .otherwise(pl.lit("wrong_instrument"))
            .alias("status")
        )
        .select(
            "year", "symbol", "theta_days", "overlap_days",
            "median_difference", "p99_difference", "status",
        )
        .sort("status", "symbol")
    )


def run(years: list[int], universe_path: str, chunk_size: int) -> None:
    started = time.perf_counter()
    frames = []
    for year in years:
        print(f"=== {year} ===")
        frames.append(check_year(year, Path(universe_path), chunk_size))

    checks_df = pl.concat(frames, how="vertical_relaxed").sort("year", "status", "symbol")

    output_path = paths.SYMBOLOGY_CHECK
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    checks_df.write_parquet(temp_path)
    os.replace(temp_path, output_path)

    print(f"\ndone in {time.perf_counter() - started:.1f}s | wrote {output_path}")
    print(checks_df.group_by("year", "status").len().sort("year", "status"))
    suspect = checks_df.filter(pl.col("status") == "wrong_instrument")
    if suspect.height:
        print("\nthese symbol-years are a different company and must be dropped:")
        print(suspect)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, nargs="+", required=True)
    parser.add_argument("--universe", default=str(paths.UNIVERSE_HISTORY))
    parser.add_argument("--chunk-size", type=int, default=50)
    args = parser.parse_args()
    run(args.years, args.universe, args.chunk_size)


if __name__ == "__main__":
    main()
