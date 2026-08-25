"""EOD option chains for the S&P 500 universe, or for explicit roots.

One parquet file per symbol under `output_dir` — a full year of every listed
expiration across 500 names is far too large to hold in memory at once.
"""

import argparse
import time
from datetime import date
from pathlib import Path

import polars as pl
from dotenv import load_dotenv

from common import fetch_many, load_universe_tickers, make_client

load_dotenv()


def fetch_chain(
    symbol: str, start_date: date, end_date: date, output_dir: Path
) -> tuple[str, int, int]:
    output_path = output_dir / f"{symbol}.parquet"
    if output_path.exists():
        return symbol, 0, 0  # resumable: already pulled

    client = make_client()
    chain_df = client.option_history_eod(start_date, end_date, symbol, "*")
    chain_df.write_parquet(output_path)
    return symbol, chain_df.height, output_path.stat().st_size


def run(
    start_date: date,
    end_date: date,
    universe_path: str,
    output_dir: str,
    workers: int,
    limit: int | None,
    symbols: list[str] | None = None,
) -> None:
    """Pull chains for `symbols`, or for the whole universe when it is None.

    An explicit symbol list bypasses the universe file entirely, which is how
    index roots (SPX, SPXW, XSP) get pulled — they are not constituents.
    """
    if symbols is None:
        symbols = load_universe_tickers(universe_path, "option", limit)
    elif limit:
        symbols = symbols[:limit]
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    print(f"options: {len(symbols)} symbols, {start_date} .. {end_date}")

    started = time.perf_counter()
    results = fetch_many(
        symbols,
        lambda symbol: fetch_chain(symbol, start_date, end_date, output_path),
        workers,
    )
    elapsed = time.perf_counter() - started

    rows = sum(row_count for _, row_count, _ in results)
    size = sum(byte_count for _, _, byte_count in results)
    print(
        f"\ndone in {elapsed:.1f}s | {rows:,} rows"
        f" | {elapsed / len(symbols):.2f}s per symbol"
        f" | {size / 1e9:.2f} GB on disk"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, default=date(2025, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2025, 12, 31))
    parser.add_argument("--universe", default="data/universe.parquet")
    parser.add_argument("--output-dir", default="data/options_2025")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--symbols",
        default=None,
        help="comma-separated roots to pull instead of the universe, e.g. SPX,SPXW,XSP",
    )
    args = parser.parse_args()

    symbols = (
        [symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip()]
        if args.symbols
        else None
    )

    run(
        args.start,
        args.end,
        args.universe,
        args.output_dir,
        args.workers,
        args.limit,
        symbols,
    )
