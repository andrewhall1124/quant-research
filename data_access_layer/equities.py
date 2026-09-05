"""Stock prices and the corporate actions that make their returns comparable.

The two live together because a return is only correct with both: a split
falling between two closes is a -50% day in the raw prices, and
`with_corporate_actions` is how a study gets the ratio onto the row that needs
it.
"""

from datetime import date

import polars as pl

from data_access_layer import paths
from data_access_layer.filters import deliver, in_window, require

# Mirrors data_pipelines.common.TICKER_OVERRIDES["stock"]. Duplicated rather
# than imported so the access layer does not depend on the pipeline package.
THETA_STOCK_OVERRIDES = {"BNY": "BK"}


def load_underlying(
    start: date | None = None, end: date | None = None, lazy: bool = False
) -> pl.LazyFrame | pl.DataFrame:
    """EOD stock prices, raw, with `created` also given as a plain `date`.

    Reads `underlying_history.parquet` (2023-06 to 2024-12) and
    `underlying_2025.parquet` together; they do not overlap, so the window
    alone decides what you get. 2023-06 is the stock tier's floor, which is why
    the chain loaders — whose `underlying_price` rides on the row — reach the
    whole option history and this one cannot.

    Two things in here will book a wrong return if you do not screen for them:

    * ThetaData emits a final row for a delisted name with
      open = high = low = close = 0, sometimes with real volume attached, rather
      than omitting it. Left in, it is a -100% day. Drop with
      `filter(pl.col('close') > 0)`.
    * Rows appear for a symbol before it was listed. `filter_to_universe`
      removes them.
    """
    sources = [
        require(
            paths.UNDERLYING_HISTORY,
            "data_pipelines.underlying --start 2023-06-01 --end 2024-05-30"
            " --output data_store/underlying_history.parquet",
        ),
        require(paths.UNDERLYING, "data_pipelines.underlying"),
    ]
    frame = (
        pl.scan_parquet(sources)
        .with_columns(pl.col("created").dt.date().alias("date"))
        .sort("date", "symbol")
    )
    return deliver(in_window(frame, start, end), lazy)


def load_corporate_actions(
    start: date | None = None, end: date | None = None, lazy: bool = False
) -> pl.LazyFrame | pl.DataFrame:
    """Splits and dividends from Yahoo, one row per (symbol, date, action).

    `action` is "split" or "dividend". Split values are ex-date ratios (ORLY
    2025-06-10 is 15.0). Yahoo also encodes spinoff adjustment factors here as
    non-integer "splits" (DD 2.39 on the Qnity spinoff), which is what you want
    for the same reason.
    """
    frame = pl.scan_parquet(
        require(paths.CORPORATE_ACTIONS, "data_pipelines.corporate_actions")
    ).sort("symbol", "date")
    return deliver(in_window(frame, start, end), lazy)


def with_corporate_actions(prices):
    """Add `split_ratio` and `dividend` for each row's date.

    Null becomes 1.0 and 0.0 respectively, so the columns are safe to multiply
    and add without a further fill. Pair with `split_adjusted_return`.

    Takes and returns whichever of DataFrame or LazyFrame it was given.
    """
    lazy = isinstance(prices, pl.LazyFrame)
    actions = load_corporate_actions(lazy=True)
    splits = actions.filter(pl.col("action") == "split").select(
        "symbol", "date", pl.col("value").alias("split_ratio")
    )
    dividends = (
        actions.filter(pl.col("action") == "dividend")
        .group_by("symbol", "date")
        .agg(pl.col("value").sum().alias("dividend"))
    )
    joined = (
        prices.lazy()
        .join(splits, on=["symbol", "date"], how="left")
        .join(dividends, on=["symbol", "date"], how="left")
        .with_columns(
            pl.col("split_ratio").fill_null(1.0),
            pl.col("dividend").fill_null(0.0),
        )
    )
    return joined if lazy else joined.collect()
