"""Historical S&P 500 constituency.

Reconstructs point-in-time membership by walking the current constituent list
backwards through Wikipedia's "selected changes" table. Adapted from the
nt-data-pipelines universe flow, minus Prefect/ClickHouse.
"""

import io
import os
from datetime import date

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
    """Trading days, taken from SPY's EOD history (free-tier friendly)."""
    client = ThetaClient(dataframe_type="polars")
    spy_df = client.stock_history_eod("SPY", start_date, end_date)
    return (
        spy_df.select(pl.col("created").dt.date().alias("date"))
        .unique()
        .sort("date")["date"]
        .to_list()
    )


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


if __name__ == "__main__":
    universe_df = run(date(2025, 1, 1), date(2025, 12, 31), str(paths.UNIVERSE))
    print(universe_df)
    print(f"\ntrading days: {universe_df['date'].n_unique()}")
    print(f"unique tickers over the year: {universe_df['ticker'].n_unique()}")
    print(f"members per day: {universe_df.group_by('date').len()['len'].describe()}")
