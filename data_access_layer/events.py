"""Earnings announcements, and the distance from any panel row to one.

An implied-vol signal ranked cross-sectionally will sort largely on days to
earnings unless it is controlled for, which is the whole reason this exists.
"""

from datetime import date

import polars as pl

from data_access_layer import paths
from data_access_layer.filters import deliver, in_window, require


def load_earnings(
    start: date | None = None, end: date | None = None, lazy: bool = False
) -> pl.LazyFrame | pl.DataFrame:
    """Announcement dates, one row per (symbol, date).

    `session` is "bmo", "amc" or "unknown". It matters: a before-the-open
    report moves that day's close-to-close return, an after-the-close report
    moves the *next* one.
    """
    frame = pl.scan_parquet(require(paths.EARNINGS, "data_pipelines.earnings")).sort(
        "symbol", "date"
    )
    return deliver(in_window(frame, start, end), lazy)


def with_earnings_distance(prices):
    """Add `days_to_earnings` and `days_since_earnings` to a (symbol, date) panel.

    Both are calendar days, and `days_to_earnings` counts to the next
    announcement on or after the row's date.

    Takes and returns whichever of DataFrame or LazyFrame it was given.
    """
    lazy = isinstance(prices, pl.LazyFrame)
    frame = prices.lazy()
    events = (
        load_earnings(lazy=True)
        .select("symbol", pl.col("date").alias("earnings_date"))
        .sort("symbol", "earnings_date")
    )
    ordered = frame.sort("symbol", "date")
    sides = []
    for strategy, name in (("forward", "next_earnings"), ("backward", "previous_earnings")):
        sides.append(
            ordered.join_asof(
                events, left_on="date", right_on="earnings_date", by="symbol",
                strategy=strategy, check_sortedness=False,
            ).select("symbol", "date", pl.col("earnings_date").alias(name))
        )
    joined = (
        frame.join(sides[0], on=["symbol", "date"], how="left")
        .join(sides[1], on=["symbol", "date"], how="left")
        .with_columns(
            (pl.col("next_earnings") - pl.col("date")).dt.total_days().alias("days_to_earnings"),
            (pl.col("date") - pl.col("previous_earnings")).dt.total_days().alias("days_since_earnings"),
        )
    )
    return joined if lazy else joined.collect()
