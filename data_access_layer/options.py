"""Per-symbol option chains: greeks with implied vol, and open interest.

These are the only loaders that read a year-stamped directory, so they are also
the only ones that resolve which files to open — from the date window, which
means the window is the single control on how much gets read.

They are pure reads. Nothing is screened out for you: a chain comes back with
its unquoted contracts, its failed IV inversions, and no check that the symbol
is the company the universe names. See `quality.py` for that last one, and the
notes on `load_option_greeks` for the first two.
"""

from datetime import date

import polars as pl

from data_access_layer import paths
from data_access_layer.equities import load_underlying
from data_access_layer.errors import MissingDataset
from data_access_layer.filters import deliver, in_window
from data_access_layer.reference import load_indices

# Index option roots quote against a published index level rather than a stock.
# SPXW is the weekly root on the same underlying as SPX.
INDEX_ROOT_TO_SPOT = {"SPX": "SPX", "SPXW": "SPX", "XSP": "XSP"}


def option_paths(
    symbol: str, dataset: str, start: date | None, end: date | None, pipeline: str
) -> list:
    """The parquet files holding one symbol over a date window.

    The store keeps one directory per calendar year, so the window picks the
    directories and the loader's own filter trims the edges. An open-ended
    window takes every year on disk.
    """
    years = [
        year
        for year in paths.available_years(dataset)
        if (start is None or year >= start.year) and (end is None or year <= end.year)
    ]
    found = [
        path
        for path in (
            paths.option_dir(dataset, year) / f"{symbol.upper()}.parquet"
            for year in years
        )
        if path.exists()
    ]
    if not found:
        # Name the window and what the store does hold, since the usual cause is
        # a year that was never backfilled rather than a symbol that does not
        # exist.
        window = f"{start or 'start'} to {end or 'end'}"
        raise MissingDataset(
            f"no {dataset} on disk for {symbol.upper()} over {window}."
            f" The store holds {paths.available_years(dataset)}."
            f" Create it with:\n  uv run python -m {pipeline}"
        )
    return found


def load_option_greeks(
    symbol: str,
    start: date | None = None,
    end: date | None = None,
    index: bool = False,
    lazy: bool = False,
) -> pl.LazyFrame | pl.DataFrame:
    """One symbol's EOD chain with greeks, IV and the underlying price.

    `index=True` reads the index roots (SPX, SPXW, VIX, XSP), which live in
    their own directory because they are not universe members.

    Raw but for two columns the window itself needs: `date`, and `expiration`
    parsed from string to Date. Everything else — `mid`, `dte`, `moneyness` —
    is one `with_columns` away:

        chain.with_columns(
            ((pl.col('bid') + pl.col('ask')) / 2).alias('mid'),
            (pl.col('expiration') - pl.col('date')).dt.total_days().alias('dte'),
            (pl.col('strike') / pl.col('underlying_price') - 1).alias('moneyness'),
        )

    Three things to know before you use a number out of here:

    - `date` comes from `underlying_timestamp`, not `timestamp`. It is the stamp
      on the spot print the greeks were struck against, so it is defined even
      for a contract that never traded; `timestamp` is the contract's own last
      trade. The two agree on the date throughout the 2025 store, checked symbol
      by symbol, but only the former is guaranteed to.
    - `implied_vol` is only as good as `iv_error`. About 3% of contract-days
      fail to invert and come back pinned near 0.5 with an error of +/-100.
      Screen on `pl.col('iv_error').abs() <= 1.0` or tighter.
    - Contracts with no quote are present, and that is also where `implied_vol`
      is 0.0 rather than null. `(pl.col('bid') > 0) & (pl.col('implied_vol') > 0)`
      drops them.

    Because `underlying_price` rides on the row, this needs no stock tier and so
    reaches the whole option history — which the stock loader, floored at
    2023-06, does not.
    """
    dataset = paths.option_dataset_name(index)
    sources = option_paths(
        symbol, dataset, start, end,
        f"data_pipelines.option_greeks --symbols {symbol.upper()}"
        + (f" --output-dir {paths.option_dir(dataset, paths.SAMPLE_YEAR)}" if index else ""),
    )
    frame = pl.scan_parquet(sources).with_columns(
        pl.col("underlying_timestamp").dt.date().alias("date"),
        pl.col("expiration").str.strptime(pl.Date, "%Y-%m-%d").alias("expiration"),
    )
    frame = in_window(frame, start, end).sort("date", "expiration", "strike", "right")
    return deliver(frame, lazy)


def load_open_interest(
    symbol: str,
    start: date | None = None,
    end: date | None = None,
    lazy: bool = False,
) -> pl.LazyFrame | pl.DataFrame:
    """One symbol's EOD open interest, with a derived `date` and `expiration`.

    Open interest is stamped pre-open (~06:30 ET) and reports the position
    standing after the *previous* close, which is the number a trader forming at
    today's close actually knows. So `date` joins straight onto a chain row for
    the same session with no shift, but the figure is settled and one day stale
    — see `data_store/README.md`.
    """
    sources = option_paths(
        symbol, "open_interest", start, end,
        f"data_pipelines.open_interest --symbols {symbol.upper()}",
    )
    frame = pl.scan_parquet(sources).with_columns(
        pl.col("timestamp").dt.date().alias("date"),
        pl.col("expiration").str.strptime(pl.Date, "%Y-%m-%d").alias("expiration"),
    )
    frame = in_window(frame, start, end).sort("date", "expiration", "strike", "right")
    return deliver(frame, lazy)


def spot_series(
    symbol: str,
    start: date | None = None,
    end: date | None = None,
    index: bool = False,
    lazy: bool = False,
) -> pl.LazyFrame | pl.DataFrame:
    """Daily spot for an option root: index level for roots, stock close otherwise."""
    if index:
        root = INDEX_ROOT_TO_SPOT.get(symbol.upper(), symbol.upper())
        levels = load_indices(start, end, lazy=True).filter(pl.col("symbol") == root)
    else:
        levels = load_underlying(start, end, lazy=True).filter(
            pl.col("symbol") == symbol.upper()
        )
    return deliver(levels.select("date", pl.col("close").alias("spot")), lazy)
