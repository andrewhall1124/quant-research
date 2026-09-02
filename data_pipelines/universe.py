"""Historical S&P 500 constituency.

Reconstructs point-in-time membership by walking the current constituent list
backwards through Wikipedia's "selected changes" table. Adapted from the
nt-data-pipelines universe flow, minus Prefect/ClickHouse.
"""

import argparse
import io
import os
from datetime import date, timedelta

import pandas as pd
import polars as pl
import requests
from dotenv import load_dotenv
from thetadata import ThetaClient

from data_access_layer import paths

load_dotenv()

CURRENT_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
CHANGES_URL = "https://en.wikipedia.org/wiki/Historical_components_of_the_S%26P_500"


def fetch_first_table(url: str) -> pd.DataFrame:
    user_agent = os.getenv(
        "WIKIPEDIA_USER_AGENT", "quant-research/0.1 (data pipeline)"
    )
    response = requests.get(url, headers={"User-Agent": user_agent})
    response.raise_for_status()
    return pd.read_html(io.StringIO(response.text))[0]


def get_trading_calendar(start_date: date, end_date: date) -> list[date]:
    """Trading days: weekdays minus the exchange's full closures.

    Taken from ThetaData's own `calendar_year`, which is free at every tier and
    reaches back to 2016 — unlike SPY's EOD history, which this used to read
    and which the free stock tier refuses before 2023-06-01. Checked against
    that older SPY-derived calendar over 2025: the two agree on all 250
    sessions, including the 2025-01-09 day of mourning.

    Early closes (half days) are trading days and are deliberately kept; only
    `full_close` rows are dropped.
    """
    client = ThetaClient(dataframe_type="polars")
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


def clean_current_constituents(current_df: pd.DataFrame) -> pl.DataFrame:
    return (
        pl.from_pandas(current_df)
        .select(pl.col("Symbol").alias("ticker"))
        .drop_nulls("ticker")
        .sort("ticker")
    )


def clean_constituent_changes(changes_df: pd.DataFrame) -> pl.DataFrame:
    added_df = changes_df[["Effective Date", "Added", "Reason"]].copy()
    added_df.columns = added_df.columns.droplevel(0)
    added_df.columns = ["effective_date", "ticker", "security", "reason"]
    added_df["action"] = "Added"

    removed_df = changes_df[["Effective Date", "Removed", "Reason"]].copy()
    removed_df.columns = removed_df.columns.droplevel(0)
    removed_df.columns = ["effective_date", "ticker", "security", "reason"]
    removed_df["action"] = "Removed"

    stacked_df = pd.concat([added_df, removed_df], ignore_index=True)

    return (
        pl.from_pandas(stacked_df)
        .with_columns(
            pl.col("effective_date").str.strptime(pl.Date, "%B %d, %Y", strict=False),
            # A few cells carry trailing wiki markup (e.g. "ITT |"); keep the symbol.
            pl.col("ticker").cast(pl.String).str.extract(r"^\s*([A-Za-z0-9.\-]+)"),
        )
        .drop_nulls(["ticker", "effective_date"])
    )


def build_universe(
    current_df: pl.DataFrame,
    changes_df: pl.DataFrame,
    calendar_dates: list[date],
) -> pl.DataFrame:
    """Walk backwards from today's members, undoing each change as we pass it."""
    constituents = set(current_df["ticker"].to_list())

    changes_by_date = {
        row["effective_date"]: list(zip(row["ticker"], row["action"]))
        for row in changes_df.group_by("effective_date")
        .agg(pl.col("ticker"), pl.col("action"))
        .iter_rows(named=True)
    }

    # Undo every change that took effect after the window we care about.
    window_end = max(calendar_dates)
    for effective_date in sorted(changes_by_date, reverse=True):
        if effective_date <= window_end:
            break
        for ticker, action in changes_by_date[effective_date]:
            if action == "Added":
                constituents.discard(ticker)
            else:
                constituents.add(ticker)

    snapshots = []
    for snapshot_date in sorted(calendar_dates, reverse=True):
        snapshots.append({"date": snapshot_date, "ticker": sorted(constituents)})

        for ticker, action in changes_by_date.get(snapshot_date, []):
            if action == "Added":
                constituents.discard(ticker)
            else:
                constituents.add(ticker)

    return (
        pl.DataFrame(snapshots)
        .explode("ticker")
        .with_columns(pl.col("date").dt.year().alias("year"))
        .sort("date", "ticker")
    )


def run(start_date: date, end_date: date, output_path: str) -> pl.DataFrame:
    current_df = clean_current_constituents(fetch_first_table(CURRENT_URL))
    changes_df = clean_constituent_changes(fetch_first_table(CHANGES_URL))
    calendar_dates = get_trading_calendar(start_date, end_date)

    universe_df = build_universe(current_df, changes_df, calendar_dates)
    universe_df.write_parquet(output_path)
    return universe_df


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, default=date(2025, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2025, 12, 31))
    parser.add_argument("--output", default=None)
    parser.add_argument(
        "--history",
        action="store_true",
        help=(
            "write universe_history.parquet for the backfill years"
            " (defaults to 2016-01-01 .. 2024-12-31) instead of universe.parquet"
        ),
    )
    args = parser.parse_args()

    start_date, end_date = args.start, args.end
    output_path = args.output
    if args.history:
        if start_date == date(2025, 1, 1):
            start_date = date(2016, 1, 1)
        if end_date == date(2025, 12, 31):
            end_date = date(2024, 12, 31)
        output_path = output_path or str(paths.UNIVERSE_HISTORY)
    output_path = output_path or str(paths.UNIVERSE)

    universe_df = run(start_date, end_date, output_path)
    print(universe_df)
    print(f"\nwrote {output_path}")
    print(f"trading days: {universe_df['date'].n_unique()}")
    print(f"unique tickers over the window: {universe_df['ticker'].n_unique()}")
    print(f"members per day: {universe_df.group_by('date').len()['len'].describe()}")


if __name__ == "__main__":
    main()
