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
from datetime import date
from pathlib import Path

import polars as pl
from dotenv import load_dotenv

from data_access_layer import paths
from data_pipelines.common import fetch_many, load_universe_tickers, make_client
from data_pipelines.reference import date_chunks

load_dotenv()


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
        try:
            chunk_df = client.option_history_open_interest(
                symbol, "*", start_date=chunk_start, end_date=chunk_end
            )
        except Exception as error:
            # A symbol with no listed chain in the window is a NOT_FOUND, not a
            # failure of the pull; anything else should surface.
            if "NoDataFound" in type(error).__name__ or "NOT_FOUND" in str(error):
                continue
            raise
        if chunk_df.height:
            chunks.append(chunk_df)

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
) -> None:
    if symbols is None:
        symbols = load_universe_tickers(universe_path, "option", limit)
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
    output_dir = args.output
    if args.year is not None:
        start_date = date(args.year, 1, 1)
        end_date = date(args.year, 12, 31)
        output_dir = str(paths.option_dir("open_interest", args.year))

    run(
        start_date, end_date, args.universe, output_dir,
        args.workers, args.limit, args.symbols,
    )


if __name__ == "__main__":
    main()
