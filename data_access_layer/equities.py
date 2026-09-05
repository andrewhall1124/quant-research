"""Stock prices and the corporate actions that make their returns comparable.

The two live together because a return is only correct with both: a split
falling between two closes is a -50% day in the raw prices, and `load_underlying
(with_actions=True)` is how a study gets the ratio onto the row that needs it.
"""

from datetime import date

import polars as pl

from data_access_layer import paths
from data_access_layer.filters import in_window, only_symbols, require
from data_access_layer.universe import load_universe

# Mirrors data_pipelines.common.TICKER_OVERRIDES["stock"]. Duplicated rather
# than imported so the access layer does not depend on the pipeline package.
THETA_STOCK_OVERRIDES = {"BNY": "BK"}


def load_underlying(
    symbols: str | list[str] | None = None,
    start: date | None = None,
    end: date | None = None,
    drop_zero_prices: bool = True,
    with_actions: bool = False,
    in_universe: bool = False,
    with_history: bool = False,
) -> pl.DataFrame:
    """EOD stock prices. The raw `created` timestamp becomes a plain `date`.

    `drop_zero_prices` removes the final row ThetaData emits for a delisted
    name, which carries open = high = low = close = 0 (sometimes with real
    volume attached) rather than being absent. Left in, it books a -100% day.

    `with_actions` adds `split_ratio` and `dividend` for that date from the
    corporate-actions table, so returns can be adjusted at the point of use.

    `with_history` prepends `underlying_history.parquet` (2023-06 to 2024-12)
    and, when `in_universe` is also set, widens the membership table to match
    so the extra years are not filtered straight back out. It
    which exists so a volatility model can burn in *before* the option sample
    starts instead of eating the first months of it. It is off by default so
    that every study written against the 2025 panel keeps loading exactly what
    it always did.

    `in_universe` keeps only (symbol, date) pairs that were actually in the
    index that day. Besides being what a universe-based study wants, it drops
    the rows ThetaData returns for a symbol *before* its listing: SOLS has four
    Jan-Apr 2025 rows priced at $0.0001, with volume, months before it began
    trading on 2025-10-30.
    """
    sources = [require(paths.UNDERLYING, "data_pipelines.underlying")]
    if with_history:
        sources.insert(
            0,
            require(
                paths.UNDERLYING_HISTORY,
                "data_pipelines.underlying --start 2023-06-01 --end 2024-05-30"
                " --output data_store/underlying_history.parquet",
            ),
        )
    frame = (
        pl.scan_parquet(sources)
        .with_columns(pl.col("created").dt.date().alias("date"))
        .select("date", "symbol", "open", "high", "low", "close", "volume", "bid", "ask")
    )
    if drop_zero_prices:
        frame = frame.filter(pl.col("close") > 0)
    if in_universe:
        # universe.parquet is spelled in Wikipedia tickers; underlying.parquet
        # in ThetaData's, so the membership table has to be mapped across.
        #
        # Membership follows the price window: asking for history and then
        # semi-joining against 2025-only membership silently drops every
        # pre-2025 row, which is a filter that looks like a data gap.
        members = (
            load_universe(with_history=with_history)
            .with_columns(
                pl.col("ticker").replace(THETA_STOCK_OVERRIDES).alias("symbol")
            )
            .select("date", "symbol")
            .unique()
        )
        frame = frame.join(members.lazy(), on=["date", "symbol"], how="semi")
    if with_actions:
        actions_df = load_corporate_actions()
        splits_df = (
            actions_df.filter(pl.col("action") == "split")
            .select("symbol", "date", pl.col("value").alias("split_ratio"))
        )
        dividends_df = (
            actions_df.filter(pl.col("action") == "dividend")
            .group_by("symbol", "date")
            .agg(pl.col("value").sum().alias("dividend"))
        )
        frame = (
            frame.join(splits_df.lazy(), on=["symbol", "date"], how="left")
            .join(dividends_df.lazy(), on=["symbol", "date"], how="left")
            .with_columns(
                pl.col("split_ratio").fill_null(1.0),
                pl.col("dividend").fill_null(0.0),
            )
        )
    return in_window(only_symbols(frame, symbols), start, end).sort("date", "symbol").collect()


def load_corporate_actions(
    kind: str | None = None,
    symbols: str | list[str] | None = None,
    start: date | None = None,
    end: date | None = None,
) -> pl.DataFrame:
    """Splits and dividends from Yahoo. `kind` is "split", "dividend" or None.

    Split values are ex-date ratios (ORLY 2025-06-10 is 15.0). Yahoo also
    encodes spinoff adjustment factors here as non-integer "splits" (DD 2.39 on
    the Qnity spinoff), which is what you want for the same reason.
    """
    frame = pl.scan_parquet(
        require(paths.CORPORATE_ACTIONS, "data_pipelines.corporate_actions")
    )
    if kind is not None:
        frame = frame.filter(pl.col("action") == kind)
    return in_window(only_symbols(frame, symbols), start, end).sort("symbol", "date").collect()


