"""Index levels, the VIX term structure, and risk-free rates.

All of this is EOD and available on the free tier, unlike the intraday
endpoints. It is small enough to pull in one pass.
"""

import argparse
import time
from datetime import date
from pathlib import Path

import polars as pl
from dotenv import load_dotenv

from common import make_client

load_dotenv()

# CBOE-family indices only. NDX is rejected outright, and DJI and MOVE return
# no data -- licensed indices ThetaData does not redistribute.
INDICES = ["SPX", "RUT", "OEX", "XSP"]

# The published VIX term structure, short end to long.
VOL_INDICES = ["VIX1D", "VIX9D", "VIX", "VIX3M", "VIX1Y", "VVIX", "SKEW"]

# CBOE treasury yield indices, quoted at 10x the yield in percent (TNX 43.0 =
# 4.30%). Scaled to a decimal yield below.
YIELD_INDICES = {"IRX": "13w", "FVX": "5y", "TNX": "10y", "TYX": "30y"}

RATES = ["SOFR"]


def fetch_indices(symbols: list[str], start_date: date, end_date: date) -> pl.DataFrame:
    client = make_client()
    frames = []
    for symbol in symbols:
        try:
            frames.append(
                client.index_history_eod(symbol, start_date, end_date)
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
            print(f"  skipped {symbol}: {str(error).splitlines()[0][:60]}")
    return pl.concat(frames, how="vertical_relaxed").sort("date", "symbol")


def fetch_rates(start_date: date, end_date: date) -> pl.DataFrame:
    client = make_client()
    frames = []
    for symbol in RATES:
        rate_df = client.interest_rate_history_eod(symbol, start_date, end_date)
        frames.append(
            rate_df.select(
                pl.col("created").str.strptime(pl.Date, "%Y-%m-%d").alias("date"),
                pl.lit(symbol).alias("symbol"),
                (pl.col("rate") / 100).alias("rate"),
            )
        )
    return pl.concat(frames, how="vertical_relaxed").sort("date", "symbol")


def run(start_date: date, end_date: date, output_dir: str) -> None:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()

    index_df = fetch_indices(INDICES + VOL_INDICES, start_date, end_date)
    index_df.write_parquet(out / "indices_2025.parquet")

    # Yield indices carry a 10x scaling, so keep them as a separate curve table.
    raw_yield_df = fetch_indices(list(YIELD_INDICES), start_date, end_date)
    yield_df = raw_yield_df.select(
        "date",
        pl.col("symbol").replace_strict(YIELD_INDICES).alias("tenor"),
        (pl.col("close") / 1000).alias("yield"),
    ).sort("date", "tenor")
    yield_df.write_parquet(out / "yields_2025.parquet")

    rate_df = fetch_rates(start_date, end_date)
    rate_df.write_parquet(out / "rates_2025.parquet")

    print(
        f"\ndone in {time.perf_counter() - started:.1f}s"
        f" | indices {index_df.height:,} rows ({index_df['symbol'].n_unique()} symbols)"
        f" | yields {yield_df.height:,} rows"
        f" | rates {rate_df.height:,} rows"
    )
    return index_df, yield_df, rate_df


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, default=date(2025, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2025, 12, 31))
    parser.add_argument("--output-dir", default="data")
    args = parser.parse_args()

    index_df, yield_df, rate_df = run(args.start, args.end, args.output_dir)

    print("\nVIX term structure, most recent day:")
    latest = index_df.filter(pl.col("date") == index_df["date"].max())
    print(latest.filter(pl.col("symbol").is_in(VOL_INDICES)).select("symbol", "close"))
    print("\nyield curve, most recent day:")
    print(yield_df.filter(pl.col("date") == yield_df["date"].max()))
    print("\nSOFR, most recent day:")
    print(rate_df.tail(1))
