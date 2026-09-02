"""Layer 1: the cached contract panels.

This is the only expensive part of the backtest. `option_greeks/` is 8.5 GB
across 519 files, and re-reading it for every configuration in an OI x
holding-period grid would make the grid impossible. So it is read twice, once,
and cached:

* `build_selection_panel` keeps a **tight** band around what a structure could
  pick — near a target dte, near the money. Small enough to hold in memory,
  and it is what the structure layer sorts over.
* `build_marks_panel` then fetches the daily quotes for *only* the contracts
  that were actually selected, over the days they are actually held. A
  semi-join, not a scan, so it stays ~1M rows instead of ~47M.

The split matters. A single band wide enough to do both jobs — a 30-day
straddle held 21 days ends at 9 dte, and its strike can drift 30% away — would
be roughly 47M rows and 8 GB, which defeats the point of caching.

Every metric a filter might later want (`open_interest`, `volume`,
`rel_spread`, `iv_error`) rides along as a **column**. Nothing is filtered
here except the band itself, so the eligibility grid sweeps in layer 2 without
touching disk again.
"""

from datetime import date
from pathlib import Path

import polars as pl

from data_access_layer import paths

PANEL_DIR = paths.REPO_ROOT / "tools" / "backtest" / "results"
SELECTION_PATH = PANEL_DIR / "selection_panel.parquet"
MARKS_PATH = PANEL_DIR / "marks_panel.parquet"

# The session date comes from `underlying_timestamp`: it is the stamp on the
# spot print the greeks were struck against, and is defined even for a contract
# that never traded.
SESSION = pl.col("underlying_timestamp").dt.date()

KEY = ["symbol", "expiration", "strike", "right"]

PANEL_COLUMNS = [
    "date", "symbol", "expiration", "strike", "right", "dte", "moneyness",
    "bid", "ask", "mid", "rel_spread", "volume", "open_interest",
    "bid_size", "ask_size", "delta", "gamma", "vega", "theta",
    "implied_vol", "iv_error", "underlying_price",
]


def read_greeks(symbol: str, years: list[int]) -> pl.LazyFrame:
    """One symbol's greeks across the requested years, with derived columns.

    A symbol can be missing from a year — it may not have been in the index
    then, or may not have had a listed chain — so absent files are skipped
    rather than raising. A symbol present in no requested year returns nothing
    and simply drops out of the panel.
    """
    files = [
        path
        for path in (
            paths.option_dir("option_greeks", year) / f"{symbol}.parquet"
            for year in years
        )
        if path.exists()
    ]
    if not files:
        return None
    return (
        pl.scan_parquet(files)
        .with_columns(
            SESSION.alias("date"),
            pl.col("expiration").str.strptime(pl.Date, "%Y-%m-%d"),
            ((pl.col("bid") + pl.col("ask")) / 2).alias("mid"),
        )
        .with_columns(
            (pl.col("expiration") - pl.col("date")).dt.total_days().alias("dte"),
            (pl.col("strike") / pl.col("underlying_price") - 1).alias("moneyness"),
        )
    )


def read_open_interest(symbol: str, years: list[int]) -> pl.LazyFrame | None:
    """Open interest for one symbol, keyed to join onto the greeks rows.

    The OI print is stamped pre-open (~06:30 ET) and reports the position
    standing after the *previous* session's close. That is the number a trader
    forming a position at today's close actually knows, so it joins onto the
    same `date` with no shift — but it is a settled, one-day-stale figure, not
    a live one.
    """
    files = [
        path
        for path in (
            paths.option_dir("open_interest", year) / f"{symbol}.parquet"
            for year in years
        )
        if path.exists()
    ]
    if not files:
        return None
    return (
        pl.scan_parquet(files)
        .with_columns(
            pl.col("timestamp").dt.date().alias("date"),
            pl.col("expiration").str.strptime(pl.Date, "%Y-%m-%d"),
        )
        .select("date", *KEY, "open_interest")
    )


def shape_panel(frame: pl.LazyFrame, symbol: str, years: list[int]) -> pl.LazyFrame:
    """Attach open interest and narrow to the panel columns."""
    oi = read_open_interest(symbol, years)
    if oi is None:
        frame = frame.with_columns(pl.lit(None, dtype=pl.Int64).alias("open_interest"))
    else:
        frame = frame.join(oi, on=["date", *KEY], how="left")
    return frame.with_columns(
        pl.when(pl.col("mid") > 0)
        .then((pl.col("ask") - pl.col("bid")) / pl.col("mid"))
        .otherwise(None)
        .alias("rel_spread")
    ).select(PANEL_COLUMNS)


def build_selection_panel(
    symbols: list[str] | None = None,
    years: list[int] | None = None,
    start: date | None = None,
    end: date | None = None,
    min_dte: int = 18,
    max_dte: int = 45,
    max_moneyness: float = 0.08,
    output_path: Path = SELECTION_PATH,
) -> pl.DataFrame:
    """Candidate contracts a structure could select, per (symbol, date).

    The default band covers a 30-day target with a +/-7 day tolerance and an
    ATM strike search. Widen it for a structure that reaches further out — a
    25-delta strangle needs more moneyness than 8% on a high-vol name — and
    rebuild.
    """
    if years is None:
        years = paths.available_years("option_greeks")
    if symbols is None:
        # The union across years: a name in the index for only part of the
        # window still belongs in the panel for the part it was there.
        symbols = sorted(
            {s for year in years for s in paths.available_option_symbols(year=year)}
        )

    frames = []
    for position, symbol in enumerate(symbols, start=1):
        greeks = read_greeks(symbol, years)
        if greeks is None:
            continue
        frame = greeks.filter(
            pl.col("dte").is_between(min_dte, max_dte),
            pl.col("moneyness").abs() <= max_moneyness,
        )
        if start is not None:
            frame = frame.filter(pl.col("date") >= start)
        if end is not None:
            frame = frame.filter(pl.col("date") <= end)
        frames.append(shape_panel(frame, symbol, years).collect())
        if position % 50 == 0:
            print(f"  selection {position}/{len(symbols)}", flush=True)

    panel_df = pl.concat(frames, how="vertical_relaxed").sort("date", "symbol", "expiration", "strike")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    panel_df.write_parquet(output_path)
    print(f"selection panel: {panel_df.height:,} rows -> {output_path}")
    return panel_df


def build_marks_panel(
    contracts_df: pl.DataFrame,
    years: list[int] | None = None,
    output_path: Path = MARKS_PATH,
) -> pl.DataFrame:
    """Daily quotes for exactly the contracts that were selected.

    `contracts_df` carries the key plus `mark_from` / `mark_to`, the window
    each contract has to be marked over. Only those symbols are read, and only
    those contracts are kept, so this is a targeted fetch rather than a scan.
    """
    if years is None:
        years = paths.available_years("option_greeks")
    wanted = contracts_df.select(*KEY, "mark_from", "mark_to")
    frames = []
    symbols = sorted(wanted["symbol"].unique().to_list())

    for position, symbol in enumerate(symbols, start=1):
        keys_df = wanted.filter(pl.col("symbol") == symbol)
        window_start = keys_df["mark_from"].min()
        window_end = keys_df["mark_to"].max()
        greeks = read_greeks(symbol, years)
        if greeks is None:
            continue
        frame = (
            greeks
            .filter(pl.col("date").is_between(window_start, window_end))
            .join(keys_df.lazy(), on=KEY, how="inner")
            .filter(pl.col("date").is_between(pl.col("mark_from"), pl.col("mark_to")))
            .drop("mark_from", "mark_to")
        )
        frames.append(shape_panel(frame, symbol, years).collect())
        if position % 50 == 0:
            print(f"  marks {position}/{len(symbols)}", flush=True)

    marks_df = (
        pl.concat(frames, how="vertical_relaxed")
        .unique(subset=["date", *KEY])
        .sort("symbol", "expiration", "strike", "right", "date")
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    marks_df.write_parquet(output_path)
    print(f"marks panel: {marks_df.height:,} rows -> {output_path}")
    return marks_df


def load_selection_panel(path: Path = SELECTION_PATH) -> pl.DataFrame:
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Build it with:\n"
            "  uv run python -m tools.backtest.panel --refresh"
        )
    return pl.read_parquet(path)


def load_marks_panel(path: Path = MARKS_PATH) -> pl.DataFrame:
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. It is built by the engine on first run."
        )
    return pl.read_parquet(path)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="rebuild even if cached")
    parser.add_argument("--start", type=date.fromisoformat, default=None)
    parser.add_argument("--end", type=date.fromisoformat, default=None)
    parser.add_argument("--min-dte", type=int, default=18)
    parser.add_argument("--max-dte", type=int, default=45)
    parser.add_argument("--max-moneyness", type=float, default=0.08)
    parser.add_argument("--limit", type=int, default=None, help="first N symbols, for a smoke test")
    parser.add_argument("--years", type=int, nargs="+", default=None,
                        help="which years to include; default is every year on disk")
    args = parser.parse_args()

    if SELECTION_PATH.exists() and not args.refresh:
        print(f"{SELECTION_PATH} exists; pass --refresh to rebuild")
        return

    symbols = paths.available_option_symbols()
    if args.limit:
        symbols = symbols[: args.limit]
    build_selection_panel(
        symbols=symbols,
        start=args.start,
        end=args.end,
        min_dte=args.min_dte,
        max_dte=args.max_dte,
        max_moneyness=args.max_moneyness,
    )


if __name__ == "__main__":
    main()
