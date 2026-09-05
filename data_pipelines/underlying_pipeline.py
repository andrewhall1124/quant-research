"""EOD underlying prices for the S&P 500 universe.

    uv run python -m data_pipelines.cli underlying
    uv run python -m data_pipelines.cli underlying --history
"""

import argparse
import time
from datetime import date
from pathlib import Path

import polars as pl
from dotenv import load_dotenv

from data_access_layer import paths
from data_pipelines.utils import fetch_many, make_client, universe_symbols, write_atomic

load_dotenv()

SAMPLE_START = date(2025, 1, 1)
SAMPLE_END = date(2025, 12, 31)
# The free stock tier refuses anything before 2023-06-01, so this is the whole
# of the available pre-sample, not an arbitrary window.
HISTORY_START = date(2023, 6, 1)
HISTORY_END = date(2024, 12, 31)


def fetch_underlying(symbol: str, start_date: date, end_date: date) -> pl.DataFrame:
    client = make_client()
    return client.stock_history_eod(symbol, start_date, end_date).with_columns(
        pl.lit(symbol).alias("symbol")
    )


def run(
    start_date: date,
    end_date: date,
    output_path: str,
    workers: int,
    limit: int | None,
    universe_year: int = paths.SAMPLE_YEAR,
) -> None:
    # Membership is taken from `universe_year`, not from the price window: the
    # history file exists to give the sample's names a burn-in, so it must pull
    # the same symbols the sample holds rather than 2023's constituents.
    symbols = universe_symbols(
        "stock", date(universe_year, 1, 1), date(universe_year, 12, 31), limit
    )
    print(f"underlying: {len(symbols)} symbols, {start_date} .. {end_date}")

    started = time.perf_counter()
    frames = fetch_many(
        symbols, lambda symbol: fetch_underlying(symbol, start_date, end_date), workers
    )
    elapsed = time.perf_counter() - started

    prices_df = pl.concat(frames, how="vertical_relaxed")
    write_atomic(prices_df, Path(output_path))

    print(
        f"\ndone in {elapsed:.1f}s | {prices_df.height:,} rows"
        f" | {elapsed / len(symbols):.2f}s per symbol"
        f" | {Path(output_path).stat().st_size / 1e6:.1f} MB"
    )


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--start", type=date.fromisoformat, default=SAMPLE_START)
    parser.add_argument("--end", type=date.fromisoformat, default=SAMPLE_END)
    parser.add_argument("--output", default=str(paths.UNDERLYING))
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--universe-year",
        type=int,
        default=paths.SAMPLE_YEAR,
        help="whose membership to pull, independent of the price window",
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help=(
            "write underlying_history.parquet instead: the pre-sample stock"
            " history, so a model burn-in does not consume formation dates in"
            " the option window. Defaults to the whole span the free stock tier"
            f" serves before the main file starts, {HISTORY_START} .. {HISTORY_END}."
        ),
    )


def main(args: argparse.Namespace) -> None:
    start_date, end_date, output_path = args.start, args.end, args.output
    if args.history:
        if start_date == SAMPLE_START:
            start_date = HISTORY_START
        if end_date == SAMPLE_END:
            end_date = HISTORY_END
        if output_path == str(paths.UNDERLYING):
            output_path = str(paths.UNDERLYING_HISTORY)

    run(
        start_date, end_date, output_path,
        args.workers, args.limit, args.universe_year,
    )
