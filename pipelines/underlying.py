"""EOD underlying prices for the S&P 500 universe."""

import argparse
import time
from datetime import date
from pathlib import Path

import polars as pl
from dotenv import load_dotenv

from common import fetch_many, load_universe_tickers, make_client

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
    symbols = load_universe_tickers(universe_path, limit)
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
    parser.add_argument("--universe", default="data/universe.parquet")
    parser.add_argument("--output", default="data/underlying_2025.parquet")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    run(args.start, args.end, args.universe, args.output, args.workers, args.limit)
