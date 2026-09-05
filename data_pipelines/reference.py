"""Index levels, the VIX term structure, and risk-free rates.

All of this is EOD and available on the free tier, unlike the intraday
endpoints. It is small enough to pull in one pass.
"""

import argparse
import time
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import yfinance as yf
from dotenv import load_dotenv

from data_access_layer import paths
from data_pipelines.common import make_client

load_dotenv()

# CBOE-family indices only. NDX is rejected outright, and DJI and MOVE return
# no data -- licensed indices ThetaData does not redistribute.
INDICES = ["SPX", "RUT", "OEX", "XSP"]

# The published VIX term structure, short end to long.
VOL_INDICES = ["VIX1D", "VIX9D", "VIX", "VIX3M", "VIX1Y", "VVIX", "SKEW"]

# The same four CBOE treasury yield indices, taken from Yahoo rather than
# ThetaData. Not a preference: the free index tier refuses anything before
# 2024-01-01, which would leave every option year from 2017 to 2023 without a
# discount rate. Yahoo serves these back to the 1960s.
#
# Checked over the 249 sessions of 2025 where both sources answer: the two
# agree to 0.000000 at the median *and* the maximum, on all four tenors. They
# are the same CBOE index arriving by a different road.
#
# Yahoo quotes the yield in percent (^TNX 4.57 = 4.57%); ThetaData quotes 10x
# that. Both are scaled to a decimal below, by different divisors.
YAHOO_YIELD_INDICES = {"^IRX": "13w", "^FVX": "5y", "^TNX": "10y", "^TYX": "30y"}

# The option store starts here, so the curve must too.
YIELD_HISTORY_START = date(2017, 1, 1)

# SOFR, from ThetaData's own rate endpoint. Tier-capped at 2024 like the index
# levels, so it does not reach the whole option history — but unlike
# `fred_rates.parquet`, which it replaces, there is code in this repo that can
# rebuild it. FRED is unreachable from here (the host resolves but refuses the
# connection), which is presumably why that file never had a pipeline.
RATES = ["SOFR"]

# ThetaData rejects any history request spanning more than 365 days, and the
# free tier refuses index history starting before roughly 2024-01-01 (earlier
# starts are quoted as VALUE / STANDARD / PROFESSIONAL by depth). Longer
# windows are stitched from year-sized chunks.
MAX_REQUEST_DAYS = 360
FREE_INDEX_HISTORY_START = date(2024, 1, 1)


def date_chunks(
    start_date: date, end_date: date, span_days: int = MAX_REQUEST_DAYS
) -> list[tuple[date, date]]:
    chunks = []
    chunk_start = start_date
    while chunk_start <= end_date:
        chunk_end = min(chunk_start + timedelta(days=span_days), end_date)
        chunks.append((chunk_start, chunk_end))
        chunk_start = chunk_end + timedelta(days=1)
    return chunks


def fetch_indices(symbols: list[str], start_date: date, end_date: date) -> pl.DataFrame:
    client = make_client()
    frames = []
    for symbol in symbols:
        for chunk_start, chunk_end in date_chunks(start_date, end_date):
            try:
                frames.append(
                    client.index_history_eod(symbol, chunk_start, chunk_end)
                    .select(
                    pl.col("created").dt.date().alias("date"),
                    pl.lit(symbol).alias("symbol"),
                    pl.col("open"),
                    pl.col("high"),
                        pl.col("low"),
                        pl.col("close"),
                    )
                )
            except Exception as error:
                detail = [
                    line for line in str(error).splitlines() if "details" in line
                ]
                reason = detail[0].strip()[:90] if detail else str(error)[:90]
                print(f"  skipped {symbol} {chunk_start}..{chunk_end}: {reason}")
    if not frames:
        raise RuntimeError(
            "no index data returned; on the free tier the history starts around"
            f" {FREE_INDEX_HISTORY_START}"
        )
    return (
        pl.concat(frames, how="vertical_relaxed")
        .unique(["date", "symbol"])
        .sort("date", "symbol")
    )


def fetch_yields(start_date: date, end_date: date) -> pl.DataFrame:
    """The treasury curve as a decimal yield, one row per (date, tenor)."""
    frames = []
    for ticker, tenor in YAHOO_YIELD_INDICES.items():
        history = yf.Ticker(ticker).history(
            start=start_date, end=end_date + timedelta(days=1), auto_adjust=False
        )["Close"]
        frames.append(
            pl.DataFrame(
                {
                    "date": [stamp.date() for stamp in history.index],
                    "tenor": tenor,
                    "yield": history.values / 100,
                }
            )
        )
    return (
        pl.concat(frames, how="vertical_relaxed")
        .filter(pl.col("yield").is_not_nan() & pl.col("yield").is_not_null())
        .unique(["date", "tenor"])
        .sort("date", "tenor")
    )


def fetch_rates(start_date: date, end_date: date) -> pl.DataFrame:
    client = make_client()
    frames = []
    for symbol in RATES:
        for chunk_start, chunk_end in date_chunks(start_date, end_date):
            rate_df = client.interest_rate_history_eod(symbol, chunk_start, chunk_end)
            frames.append(
                rate_df.select(
                    pl.col("created").str.strptime(pl.Date, "%Y-%m-%d").alias("date"),
                    pl.lit(symbol).alias("symbol"),
                    (pl.col("rate") / 100).alias("rate"),
                )
            )
    return (
        pl.concat(frames, how="vertical_relaxed")
        .unique(["date", "symbol"])
        .sort("date", "symbol")
    )


def run(
    start_date: date,
    end_date: date,
    output_dir: str,
    yield_start: date = YIELD_HISTORY_START,
) -> None:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()

    index_df = fetch_indices(INDICES + VOL_INDICES, start_date, end_date)
    index_df.write_parquet(paths.INDICES)

    # The curve runs from `yield_start`, not `start_date`: index levels are
    # capped at 2024 by the free tier but the yields are not, and every option
    # year needs a discount rate.
    yield_df = fetch_yields(yield_start, end_date)
    yield_df.write_parquet(paths.YIELDS)

    rate_df = fetch_rates(start_date, end_date)
    rate_df.write_parquet(paths.RATES)

    print(
        f"\ndone in {time.perf_counter() - started:.1f}s"
        f" | indices {index_df.height:,} rows ({index_df['symbol'].n_unique()} symbols)"
        f" | yields {yield_df.height:,} rows"
        f" ({yield_df['date'].min()} .. {yield_df['date'].max()})"
        f" | rates {rate_df.height:,} rows"
    )
    return index_df, yield_df, rate_df


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--start", type=date.fromisoformat, default=FREE_INDEX_HISTORY_START
    )
    parser.add_argument("--end", type=date.fromisoformat, default=date(2025, 12, 31))
    parser.add_argument("--output-dir", default=str(paths.DATA_STORE))
    parser.add_argument(
        "--yield-start",
        type=date.fromisoformat,
        default=YIELD_HISTORY_START,
        help="the curve reaches further back than the index levels the tier allows",
    )
    args = parser.parse_args()

    index_df, yield_df, rate_df = run(
        args.start, args.end, args.output_dir, args.yield_start
    )

    print("\nVIX term structure, most recent day:")
    latest = index_df.filter(pl.col("date") == index_df["date"].max())
    print(latest.filter(pl.col("symbol").is_in(VOL_INDICES)).select("symbol", "close"))
    print("\nyield curve, most recent day:")
    print(yield_df.filter(pl.col("date") == yield_df["date"].max()))
