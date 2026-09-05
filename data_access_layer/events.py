"""Earnings announcements, and the distance from any panel row to one.

An implied-vol signal ranked cross-sectionally will sort largely on days to
earnings unless it is controlled for, which is the whole reason this exists.
"""

from datetime import date

import polars as pl

from data_access_layer import paths
from data_access_layer.filters import in_window, only_symbols, require


def load_earnings(
    symbols: str | list[str] | None = None,
    start: date | None = None,
    end: date | None = None,
) -> pl.DataFrame:
    """Earnings announcement dates, one row per (symbol, date).

    `session` is "bmo", "amc" or "unknown". It matters: a before-the-open
    report moves that day's close-to-close return, an after-the-close report
    moves the *next* one.
    """
    frame = pl.scan_parquet(require(paths.EARNINGS, "data_pipelines.earnings"))
    return in_window(only_symbols(frame, symbols), start, end).sort("symbol", "date").collect()


def with_earnings_distance(prices_df: pl.DataFrame) -> pl.DataFrame:
    """Add `days_to_earnings` and `days_since_earnings` to a (symbol, date) panel.

    Both are calendar days, and `days_to_earnings` counts to the next
    announcement on or after the row's date. An implied-vol signal ranked
    cross-sectionally will sort largely on this column unless it is controlled
    for, which is the whole reason the table exists.
    """
    events_df = load_earnings().select(
        "symbol", pl.col("date").alias("earnings_date")
    )
    ordered = prices_df.sort("symbol", "date")
    events = events_df.sort("symbol", "earnings_date")
    forward = ordered.join_asof(
        events, left_on="date", right_on="earnings_date", by="symbol",
        strategy="forward", check_sortedness=False,
    ).select("symbol", "date", pl.col("earnings_date").alias("next_earnings"))
    backward = ordered.join_asof(
        events, left_on="date", right_on="earnings_date", by="symbol",
        strategy="backward", check_sortedness=False,
    ).select("symbol", "date", pl.col("earnings_date").alias("previous_earnings"))
    return (
        prices_df.join(forward, on=["symbol", "date"], how="left")
        .join(backward, on=["symbol", "date"], how="left")
        .with_columns(
            (pl.col("next_earnings") - pl.col("date")).dt.total_days().alias("days_to_earnings"),
            (pl.col("date") - pl.col("previous_earnings")).dt.total_days().alias("days_since_earnings"),
        )
    )


