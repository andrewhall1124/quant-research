"""EOD underlying prices for the S&P 500 universe."""

import argparse
import time
from datetime import date
from pathlib import Path

import polars as pl
from dotenv import load_dotenv

from data_access_layer import paths
from data_pipelines.common import fetch_many, load_universe_tickers, make_client

load_dotenv()


def fetch_underlying(symbol: str, start_date: date, end_date: date) -> pl.DataFrame:
    client = make_client()
    return client.stock_history_eod(symbol, start_date, end_date).with_columns(
        pl.lit(symbol).alias("symbol")
    )


def run(
    start_date: date,
    end_date: date,
    universe_path: str,
    output_path: str,
    workers: int,
    limit: int | None,
) -> None:
    symbols = load_universe_tickers(universe_path, "stock", limit)
    print(f"underlying: {len(symbols)} symbols, {start_date} .. {end_date}")

    started = time.perf_counter()
    frames = fetch_many(
        symbols, lambda symbol: fetch_underlying(symbol, start_date, end_date), workers
    )
    elapsed = time.perf_counter() - started

    prices_df = pl.concat(frames, how="vertical_relaxed")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    prices_df.write_parquet(output_path)

    print(
        f"\ndone in {elapsed:.1f}s | {prices_df.height:,} rows"
        f" | {elapsed / len(symbols):.2f}s per symbol"
        f" | {Path(output_path).stat().st_size / 1e6:.1f} MB"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, default=date(2025, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2025, 12, 31))
    parser.add_argument("--universe", default=str(paths.UNIVERSE))
    parser.add_argument("--output", default=str(paths.UNDERLYING))
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--history",
        action="store_true",
        help=(
            "write underlying_history.parquet instead: the pre-sample stock"
            " history, so a model burn-in does not consume formation dates in"
            " the option window. Defaults to the whole span the free stock tier"
            " serves before the main file starts, 2023-06-01 .. 2024-12-31."
        ),
    )
    args = parser.parse_args()

    start_date, end_date, output_path = args.start, args.end, args.output
    if args.history:
        # The free stock tier refuses anything before 2023-06-01, so this is
        # the whole of the available pre-sample, not an arbitrary window.
        if start_date == date(2025, 1, 1):
            start_date = date(2023, 6, 1)
        if end_date == date(2025, 12, 31):
            end_date = date(2024, 12, 31)
        if output_path == str(paths.UNDERLYING):
            output_path = str(paths.UNDERLYING_HISTORY)

    run(start_date, end_date, args.universe, output_path, args.workers, args.limit)
