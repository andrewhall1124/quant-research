"""Index levels, the VIX term structure, and risk-free rates.

All of this is EOD and available on the free tier, unlike the intraday
endpoints. It is small enough to pull in one pass.
"""

import argparse
import time
from datetime import date, timedelta
from pathlib import Path

import polars as pl
from dotenv import load_dotenv

from data_access_layer import paths
from data_pipelines.common import make_client

load_dotenv()

# CBOE-family indices only. NDX is rejected outright, and DJI and MOVE return
# no data -- licensed indices ThetaData does not redistribute.
INDICES = ["SPX", "RUT", "OEX", "XSP"]

# The published VIX term structure, short end to long.
VOL_INDICES = ["VIX1D", "VIX9D", "VIX", "VIX3M", "VIX1Y", "VVIX", "SKEW"]

# CBOE treasury yield indices, quoted at 10x the yield in percent (TNX 43.0 =
# 4.30%). Scaled to a decimal yield below.
YIELD_INDICES = {"IRX": "13w", "FVX": "5y", "TNX": "10y", "TYX": "30y"}

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


def run(start_date: date, end_date: date, output_dir: str) -> None:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()

    index_df = fetch_indices(INDICES + VOL_INDICES, start_date, end_date)
    index_df.write_parquet(paths.INDICES)

    # Yield indices carry a 10x scaling, so keep them as a separate curve table.
    raw_yield_df = fetch_indices(list(YIELD_INDICES), start_date, end_date)
    yield_df = raw_yield_df.select(
        "date",
        pl.col("symbol").replace_strict(YIELD_INDICES).alias("tenor"),
        (pl.col("close") / 1000).alias("yield"),
    ).sort("date", "tenor")
    yield_df.write_parquet(paths.YIELDS)

    print(
        f"\ndone in {time.perf_counter() - started:.1f}s"
        f" | indices {index_df.height:,} rows ({index_df['symbol'].n_unique()} symbols)"
        f" | yields {yield_df.height:,} rows"
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
    args = parser.parse_args()

    index_df, yield_df, rate_df = run(args.start, args.end, args.output_dir)

    print("\nVIX term structure, most recent day:")
    latest = index_df.filter(pl.col("date") == index_df["date"].max())
    print(latest.filter(pl.col("symbol").is_in(VOL_INDICES)).select("symbol", "close"))
    print("\nyield curve, most recent day:")
    print(yield_df.filter(pl.col("date") == yield_df["date"].max()))
    print("\nSOFR, most recent day:")
    print(rate_df.tail(1))
