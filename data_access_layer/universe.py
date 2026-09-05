"""Point-in-time S&P 500 membership."""

from datetime import date

import polars as pl

from data_access_layer import paths
from data_access_layer.equities import THETA_STOCK_OVERRIDES
from data_access_layer.filters import deliver, in_window, require


def load_universe(
    start: date | None = None, end: date | None = None, lazy: bool = False
) -> pl.LazyFrame | pl.DataFrame:
    """Index membership, one row per (date, ticker).

    Reads `universe.parquet` (2025) and `universe_history.parquet` (2017-2024)
    together — they do not overlap, so the window alone decides what you get.
    Membership that far back is walked further through Wikipedia's changes
    table and carries more accumulated error than the 2025 sample.

    Tickers are spelled the way Wikipedia spells them, which is not always the
    way ThetaData does; `filter_to_universe` maps across the difference.
    """
    sources = [
        require(paths.UNIVERSE_HISTORY, "data_pipelines.universe --history"),
        require(paths.UNIVERSE, "data_pipelines.universe"),
    ]
    frame = pl.scan_parquet(sources).sort("date", "ticker")
    return deliver(in_window(frame, start, end), lazy)


def filter_to_universe(prices):
    """Keep only the (symbol, date) pairs that were in the index that day.

    Besides being what a universe-based study wants, this drops the rows
    ThetaData returns for a symbol *before* its listing: SOLS has four Jan-Apr
    2025 rows priced at $0.0001, with volume, months before it began trading on
    2025-10-30.

    Takes and returns whichever of DataFrame or LazyFrame it was given.
    """
    lazy = isinstance(prices, pl.LazyFrame)
    members = (
        load_universe(lazy=True)
        .with_columns(pl.col("ticker").replace(THETA_STOCK_OVERRIDES).alias("symbol"))
        .select("date", "symbol")
        .unique()
    )
    joined = prices.lazy().join(members, on=["date", "symbol"], how="semi")
    return joined if lazy else joined.collect()
