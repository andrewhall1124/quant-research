"""EOD open interest for every listed contract, per symbol.

The greeks pull carries quotes, greeks and IV but *not* open interest: that
lives on its own endpoint, `/v3/option/history/open_interest`, which is badged
Value/Standard/Pro. It is the liquidity screen an option strategy actually
wants — volume says what traded today, open interest says how much position is
standing there.

Cheap, unlike `option_greeks.py`. That endpoint forces `expiration=*` to be
requested a day at a time; this one accepts a whole date range in one request,
so a symbol-year is a single call rather than ~250. A 500-name year runs in
minutes, not hours.

Still subject to the 365-day cap per request, so longer windows are stitched
with `reference.date_chunks`.

Resumable at symbol granularity: a symbol's file is written only once its whole
window is fetched, and existing files are skipped, so an interrupted run can
just be re-run.

    uv run python -m data_pipelines.open_interest --start 2025-01-01 --end 2025-12-31
"""

import argparse
import time
from datetime import date, timedelta
from pathlib import Path

import polars as pl
from dotenv import load_dotenv

from data_access_layer import paths
from data_pipelines.common import fetch_many, load_universe_tickers, make_client
from data_pipelines.reference import date_chunks

load_dotenv()


def is_server_fault(error: Exception) -> bool:
    """A fault in ThetaData's own request handling, not a missing-data answer.

    Seen as `INTERNAL: java.lang.ArrayIndexOutOfBoundsException`, raised for any
    window containing a session the server cannot serialise. One such session —
    2020-05-01 — cost 15 symbols their whole 2020 open interest, because the
    pull asks for a year at a time and the year contains the bad day.
    """
    return "INTERNAL" in str(error) or "ArrayIndexOutOfBounds" in str(error)


def is_missing_data(error: Exception) -> bool:
    """A symbol with no listed chain in the window: not a failure of the pull."""
    return "NoDataFound" in type(error).__name__ or "NOT_FOUND" in str(error)


def fetch_window(
    client, symbol: str, start_date: date, end_date: date, depth: int = 0
) -> list[pl.DataFrame]:
    """One window of open interest, bisecting around a server fault.

    Halving until the bad session is alone loses that one day instead of the
    whole symbol-year. Recursion is bounded by the window collapsing to a
    single date.
    """
    try:
        chunk_df = client.option_history_open_interest(
            symbol, "*", start_date=start_date, end_date=end_date
        )
        return [chunk_df] if chunk_df.height else []
    except Exception as error:
        if is_missing_data(error):
            return []
        if not is_server_fault(error) or start_date >= end_date:
            if is_server_fault(error):
                print(f"\n  {symbol}: server fault on {start_date}, skipping that session")
                return []
            raise
        midpoint = start_date + (end_date - start_date) / 2
        return (
            fetch_window(client, symbol, start_date, midpoint, depth + 1)
            + fetch_window(client, symbol, midpoint + timedelta(days=1), end_date, depth + 1)
        )


def fetch_symbol_open_interest(
    symbol: str, start_date: date, end_date: date, output_dir: Path
) -> tuple[str, int, int]:
    """Every contract-day of open interest for one symbol."""
    output_path = output_dir / f"{symbol}.parquet"
    if output_path.exists():
        return symbol, 0, 0  # resumable: already pulled

    client = make_client()
    chunks = []
    for chunk_start, chunk_end in date_chunks(start_date, end_date):
        chunks.extend(fetch_window(client, symbol, chunk_start, chunk_end))

    if not chunks:
        return symbol, 0, 0
    oi_df = pl.concat(chunks, how="vertical_relaxed")
    oi_df.write_parquet(output_path)
    return symbol, oi_df.height, output_path.stat().st_size


def run(
    start_date: date,
    end_date: date,
    universe_path: str,
    output_dir: str,
    workers: int,
    limit: int | None,
    symbols: list[str] | None = None,
    universe_year: int | None = None,
) -> None:
    if symbols is None:
        symbols = load_universe_tickers(universe_path, "option", limit, universe_year)
    elif limit:
        symbols = symbols[:limit]

    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    pending = [s for s in symbols if not (directory / f"{s}.parquet").exists()]
    print(
        f"open_interest: {len(symbols)} symbols"
        f" ({len(pending)} pending), {start_date} .. {end_date}"
    )

    started = time.perf_counter()
    results = fetch_many(
        pending,
        lambda symbol: fetch_symbol_open_interest(symbol, start_date, end_date, directory),
        workers,
    )
    elapsed = time.perf_counter() - started

    rows = sum(count for _, count, _ in results)
    size = sum(nbytes for _, _, nbytes in results)
    print(
        f"\nopen_interest: {rows:,} contract-days"
        f" | {size / 1e6:.1f} MB | {elapsed / 60:.1f} min"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
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
    parser.add_argument("--universe", default=str(paths.UNIVERSE))
    parser.add_argument("--output", default=str(paths.OPEN_INTEREST_DIR))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--symbols", nargs="+", default=None)
    args = parser.parse_args()

    start_date, end_date = args.start, args.end
    universe_path = args.universe
    output_dir = args.output
    if args.year is not None:
        start_date = date(args.year, 1, 1)
        end_date = date(args.year, 12, 31)
        # An explicit --output-dir still wins: that is how index roots are
        # pulled into their own directory rather than the constituent one.
        if args.output == str(paths.OPEN_INTEREST_DIR):
            output_dir = str(paths.option_dir("open_interest", args.year))
        if args.year != paths.SAMPLE_YEAR and args.universe == str(paths.UNIVERSE):
            # A backfill year needs that year's members, not 2025's.
            universe_path = str(paths.UNIVERSE_HISTORY)

    run(
        start_date, end_date, universe_path, output_dir,
        args.workers, args.limit, args.symbols, args.year,
    )


if __name__ == "__main__":
    main()
