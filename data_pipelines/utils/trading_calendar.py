"""Which days the exchange was open, and how to cut a window into requests."""

from datetime import date, timedelta

import polars as pl

from data_pipelines.utils.theta import make_client

# ThetaData rejects any history request spanning more than 365 days, so longer
# windows are stitched from year-sized chunks.
MAX_REQUEST_DAYS = 360


def trading_sessions(start_date: date, end_date: date) -> list[date]:
    """Trading days: weekdays minus the exchange's full closures.

    Taken from ThetaData's own `calendar_year`, which is free at every tier and
    reaches back to 2016 — unlike SPY's EOD history, which this used to read
    and which the free stock tier refuses before 2023-06-01. Checked against
    that older SPY-derived calendar over 2025: the two agree on all 250
    sessions, including the 2025-01-09 day of mourning.

    Early closes (half days) are trading days and are deliberately kept; only
    `full_close` rows are dropped.
    """
    client = make_client()
    closed_dates = set()
    for year in range(start_date.year, end_date.year + 1):
        calendar_df = client.calendar_year(str(year))
        closed_dates |= set(
            calendar_df.filter(pl.col("type") == "full_close")["date"]
            .str.strptime(pl.Date, "%Y-%m-%d")
            .to_list()
        )

    sessions = []
    day = start_date
    while day <= end_date:
        if day.weekday() < 5 and day not in closed_dates:
            sessions.append(day)
        day += timedelta(days=1)
    return sessions


def date_chunks(
    start_date: date, end_date: date, span_days: int = MAX_REQUEST_DAYS
) -> list[tuple[date, date]]:
    """Split an inclusive window into pieces ThetaData will accept."""
    chunks = []
    chunk_start = start_date
    while chunk_start <= end_date:
        chunk_end = min(chunk_start + timedelta(days=span_days), end_date)
        chunks.append((chunk_start, chunk_end))
        chunk_start = chunk_end + timedelta(days=1)
    return chunks
