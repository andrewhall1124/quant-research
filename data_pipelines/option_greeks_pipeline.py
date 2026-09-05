"""EOD option chains with greeks, IV and the underlying price, per symbol.

`option_history_greeks_eod` returns OHLC and NBBO columns *plus* 1st-3rd order
greeks, `implied_vol`, `iv_error` and — the part that removes a join and a
symbology hop — `underlying_price` struck at the same instant as the option
quote.

Expensive, because the server requires `expiration=*` to be requested a day at
a time:

    "When expiration=*, you must request data a day-at-a-time"

so a symbol-year is ~250 requests rather than one. Measured at ~0.31 s of
fixed cost per request, a 500-name year is ~11 hours serial and ~3 hours at
Standard's 4 concurrent requests.

Resumable at symbol granularity: a symbol's file is written only once its whole
window is fetched, and existing files are skipped, so an interrupted run loses
at most one symbol (~80 s) and can just be re-run.

    uv run python -m data_pipelines.cli option-greeks --year 2024
"""

import argparse
import time
from datetime import date
from pathlib import Path

import polars as pl
from dotenv import load_dotenv

from data_access_layer import paths
from data_pipelines.utils import fetch_many, make_client, trading_sessions, universe_symbols

load_dotenv()


def fetch_symbol_greeks(
    symbol: str, sessions: list[date], output_dir: Path
) -> tuple[str, int, int]:
    """Every contract-day for one symbol, one request per session."""
    output_path = output_dir / f"{symbol}.parquet"
    if output_path.exists():
        return symbol, 0, 0  # resumable: already pulled

    client = make_client()
    days = []
    for session in sessions:
        try:
            day_df = client.option_history_greeks_eod(symbol, "*", session, session)
        except Exception as error:
            # A symbol with no listed chain on a given day is a NOT_FOUND, not a
            # failure of the pull; anything else should surface.
            if "NoDataFound" in type(error).__name__ or "NOT_FOUND" in str(error):
                continue
            raise
        if day_df.height:
            days.append(day_df)

    if not days:
        return symbol, 0, 0
    chain_df = pl.concat(days, how="vertical_relaxed")
    chain_df.write_parquet(output_path)
    return symbol, chain_df.height, output_path.stat().st_size


def run(
    start_date: date,
    end_date: date,
    output_dir: str,
    workers: int,
    limit: int | None,
    symbols: list[str] | None = None,
) -> None:
    if symbols is None:
        # The window picks the members: a backfill year pulls that year's
        # constituents, not today's.
        symbols = universe_symbols("option", start_date, end_date, limit)
    elif limit:
        symbols = symbols[:limit]

    sessions = trading_sessions(start_date, end_date)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    remaining = [
        symbol for symbol in symbols if not (output_path / f"{symbol}.parquet").exists()
    ]
    requests = len(remaining) * len(sessions)
    print(
        f"option_greeks: {len(symbols)} symbols ({len(remaining)} still to pull),"
        f" {len(sessions)} sessions, {start_date} .. {end_date}"
    )
    print(f"  {requests:,} requests at {workers} workers, ~{requests * 0.31 / workers / 3600:.1f} hr")

    started = time.perf_counter()
    results = fetch_many(
        remaining,
        lambda symbol: fetch_symbol_greeks(symbol, sessions, output_path),
        workers,
    )
    elapsed = time.perf_counter() - started

    rows = sum(row_count for _, row_count, _ in results)
    size = sum(byte_count for _, _, byte_count in results)
    print(
        f"\ndone in {elapsed / 3600:.2f} hr | {rows:,} rows"
        f" | {size / 1e9:.2f} GB written"
    )


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--start", type=date.fromisoformat, default=date(2025, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2025, 12, 31))
    parser.add_argument(
        "--year",
        type=int,
        default=None,
        help=(
            "backfill one calendar year: sets --start, --end and the"
            " year-stamped output directory in one flag"
        ),
    )
    parser.add_argument("--output-dir", default=str(paths.OPTION_GREEKS_DIR))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--symbols", default=None, help="comma-separated roots")


def main(args: argparse.Namespace) -> None:
    start_date, end_date = args.start, args.end
    output_dir = args.output_dir
    if args.year is not None:
        start_date = date(args.year, 1, 1)
        end_date = date(args.year, 12, 31)
        # An explicit --output-dir still wins: that is how index roots are
        # pulled into their own directory rather than the constituent one.
        if args.output_dir == str(paths.OPTION_GREEKS_DIR):
            output_dir = str(paths.option_dir("option_greeks", args.year))

    symbols = (
        [symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip()]
        if args.symbols
        else None
    )
    run(start_date, end_date, output_dir, args.workers, args.limit, symbols)
