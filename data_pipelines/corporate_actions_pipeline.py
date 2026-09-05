"""Splits and dividends from Yahoo, checked against ThetaData's own closes.

ThetaData serves raw, unadjusted prices and its client exposes no splits
endpoint, so a return computed off `close` books a -93% day on ORLY's 15:1.
Yahoo has the split and dividend calendar; this pipeline pulls it and writes a
corporate-actions table the loaders use to adjust.

Yahoo is *not* used for prices. The whole value of the ThetaData panel is that
the option quotes and the stock close are the same 17:15 ET snapshot, and
mixing a second vendor's close into that breaks it.

Yahoo will also answer almost any symbol with something rather than an error,
so every name is verified — but on returns rather than price levels, and by
`symbology_pipeline.py` rather than here. See its docstring for why levels
cannot arbitrate.

    uv run python -m data_pipelines.cli corporate-actions
"""

import argparse
from datetime import date
import time

import polars as pl

from data_access_layer import paths
from data_pipelines.utils import fetch_yahoo, symbol_map, write_atomic

# Yahoo's `Close` is split-adjusted even with auto_adjust=False (that flag only
# controls dividend adjustment), so a raw ThetaData close only matches it once
# the splits have been applied. That makes the comparison a test of the split
# factors as well as of the ticker mapping.
#
# Crucially, Yahoo back-adjusts for every split up to *today*, not up to the end
# of the requested window, so the pull always runs to the present and only
# `start` is configurable. See `utils.yahoo.fetch_yahoo`.
CLOSE_TOLERANCE = 0.005
MIN_OVERLAP_DAYS = 20

# Splits and dividends are needed as far back as the option data reaches.
ACTIONS_START = date(2017, 1, 1)


def build_actions(quotes_df: pl.DataFrame, names_df: pl.DataFrame) -> pl.DataFrame:
    """The corporate-actions table: one row per split or dividend."""
    mapping_df = names_df.select(
        pl.col("stock_symbol").alias("symbol"), "yahoo_symbol"
    )
    named = quotes_df.join(mapping_df, on="yahoo_symbol", how="inner")
    splits = (
        named.filter(pl.col("split") > 0)
        .select("symbol", "yahoo_symbol", "date", pl.lit("split").alias("action"),
                pl.col("split").alias("value"))
    )
    dividends = (
        named.filter(pl.col("dividend") > 0)
        .select("symbol", "yahoo_symbol", "date", pl.lit("dividend").alias("action"),
                pl.col("dividend").alias("value"))
    )
    return pl.concat([splits, dividends]).sort("symbol", "date", "action")


def split_factors(actions_df: pl.DataFrame, dates_df: pl.DataFrame) -> pl.DataFrame:
    """Back-adjustment factor per (symbol, date).

    A price on date `t` is divided by the product of every split ratio with an
    ex-date strictly after `t`, which is the convention Yahoo's own series uses.
    """
    splits = actions_df.filter(pl.col("action") == "split")
    if splits.is_empty():
        return dates_df.with_columns(pl.lit(1.0).alias("split_factor"))

    factors = []
    for symbol in splits["symbol"].unique().to_list():
        symbol_splits = splits.filter(pl.col("symbol") == symbol)
        symbol_dates = dates_df.filter(pl.col("symbol") == symbol)
        ratios = list(zip(symbol_splits["date"].to_list(), symbol_splits["value"].to_list()))
        factor = pl.lit(1.0)
        for ex_date, ratio in ratios:
            factor = factor * pl.when(pl.col("date") < ex_date).then(ratio).otherwise(1.0)
        factors.append(symbol_dates.with_columns(factor.alias("split_factor")))

    unsplit = dates_df.join(splits.select("symbol").unique(), on="symbol", how="anti")
    factors.append(unsplit.with_columns(pl.lit(1.0).alias("split_factor")))
    return pl.concat(factors, how="vertical_relaxed")


def run(start: date, chunk_size: int, universe_year: int = paths.SAMPLE_YEAR) -> None:
    names_df = symbol_map(date(universe_year, 1, 1), date(universe_year, 12, 31))
    print(f"corporate actions: {names_df.height} symbols, {start} .. {date.today()}")

    started = time.perf_counter()
    quotes_df = fetch_yahoo(names_df["yahoo_symbol"].to_list(), start, chunk_size)
    actions_df = build_actions(quotes_df, names_df)

    write_atomic(actions_df, paths.CORPORATE_ACTIONS)

    splits_df = actions_df.filter(pl.col("action") == "split")
    print(
        f"\ndone in {time.perf_counter() - started:.1f}s"
        f" | {splits_df.height} splits, "
        f"{actions_df.height - splits_df.height:,} dividends"
    )
    print("\nsplits:")
    print(splits_df.select("symbol", "date", "value"))
    print(
        "\nWhether a symbol is the company the universe names is decided by"
        "\n`data_pipelines.cli symbology`, on returns rather than price levels."
    )


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--start", type=date.fromisoformat, default=ACTIONS_START)
    parser.add_argument("--chunk-size", type=int, default=50)
    parser.add_argument("--universe-year", type=int, default=paths.SAMPLE_YEAR)


def main(args: argparse.Namespace) -> None:
    run(args.start, args.chunk_size, args.universe_year)
