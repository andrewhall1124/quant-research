"""Repair index option sessions whose EOD quotes came back empty.

ThetaData's EOD greeks endpoint returns zero bids and asks for the index
roots (SPX, SPXW, XSP, VIX) on a block of sessions, most of them in
2020-2021: the rows are there, with volume, but every quote is 0.0 and the
implied vol is pinned with an error of 100. A fresh EOD pull returns the
same thing. The intraday endpoints have the quotes, and the first-order
greeks endpoint at the 15:59 bar carries bid, ask, IV, its error, delta,
theta, vega, rho and the underlying price, on the Standard tier.

This pipeline finds the affected sessions in each stored file, pulls that
bar for every expiration listed on each of them, and splices the columns
in. Second- and third-order greeks on repaired rows are set to null rather
than left at values computed from empty quotes. The original file is kept
beside the repaired one as `<ROOT>.parquet.orig` the first time it is
touched, and the run is resumable: a session that is already quoted is not
requested again.

    uv run python -m data_pipelines.cli index-repair --dry-run
    uv run python -m data_pipelines.cli index-repair --symbols SPX --years 2021

One request per (session, expiration) at ~2 s each; SPXW lists 40-odd
expirations a day, so a full repair is a few hours at four workers.
"""

import argparse
import shutil
import time
from datetime import date
from pathlib import Path

import polars as pl
from dotenv import load_dotenv

from data_access_layer import paths
from data_pipelines.utils import fetch_many, make_client, write_atomic

load_dotenv()

INDEX_ROOTS = ["SPX", "SPXW", "XSP", "VIX"]
BAR_START, BAR_END = "15:59:00", "16:00:00"

# Columns the 15:59 first-order bar can replace, and the ones it cannot,
# which were computed from the empty quotes and are nulled instead.
REPLACED = ["bid", "ask", "delta", "theta", "vega", "rho", "epsilon", "lambda",
            "implied_vol", "iv_error", "underlying_timestamp", "underlying_price"]
NULLED = ["gamma", "vanna", "charm", "vomma", "veta", "vera", "speed", "zomma", "color",
          "ultima", "d1", "d2", "dual_delta", "dual_gamma"]


def find_unquoted_sessions(chain_df: pl.DataFrame, threshold: float) -> list[date]:
    """Sessions where fewer than `threshold` of near-ATM, 7-60 day contracts
    carry an ask."""
    daily = (
        chain_df.with_columns(
            pl.col("underlying_timestamp").dt.date().alias("date"),
            pl.col("expiration").str.strptime(pl.Date, "%Y-%m-%d").alias("expiry"),
        )
        .with_columns((pl.col("expiry") - pl.col("date")).dt.total_days().alias("dte"))
        .filter(
            pl.col("underlying_price") > 0,
            (pl.col("strike") / pl.col("underlying_price") - 1).abs() <= 0.02,
            pl.col("dte").is_between(7, 60),
        )
        .group_by("date").agg((pl.col("ask") > 0).mean().alias("quoted"))
    )
    return daily.filter(pl.col("quoted") < threshold).sort("date")["date"].to_list()


def fetch_bar(root: str, session: date, expiration: str) -> pl.DataFrame:
    client = make_client()
    try:
        bar = client.option_history_greeks_first_order(
            root, date.fromisoformat(expiration), interval="1h", date=session,
            start_time=BAR_START, end_time=BAR_END,
        )
    except Exception as error:
        if "NoDataFound" in type(error).__name__ or "NOT_FOUND" in str(error):
            return pl.DataFrame()
        raise
    if bar.is_empty():
        return bar
    # One bar was asked for; keep the last in case the server returns two.
    return bar.sort("timestamp").group_by("expiration", "strike", "right", maintain_order=True).last()


def repair_file(root: str, path: Path, threshold: float, workers: int, dry_run: bool) -> None:
    chain_df = pl.read_parquet(path)
    sessions = find_unquoted_sessions(chain_df, threshold)
    dated = chain_df.with_columns(pl.col("underlying_timestamp").dt.date().alias("date"))
    tasks = (
        dated.filter(pl.col("date").is_in(pl.Series(sessions).implode()))
        .select("date", "expiration").unique().sort("date", "expiration")
    )
    print(f"{path.name}: {len(sessions)} unquoted sessions, {tasks.height} (session, expiration) requests"
          f" ~{tasks.height * 2.1 / max(workers, 1) / 60:.0f} min", flush=True)
    if dry_run or tasks.is_empty():
        return

    started = time.perf_counter()
    bars = fetch_many(
        [(row["date"], row["expiration"]) for row in tasks.iter_rows(named=True)],
        lambda task: fetch_bar(root, task[0], task[1]),
        workers,
    )
    bars = [bar for bar in bars if not bar.is_empty()]
    if not bars:
        print(f"\n{path.name}: nothing returned")
        return
    patch_df = (
        pl.concat(bars, how="vertical_relaxed")
        .filter(pl.col("ask") > 0)
        .with_columns(pl.col("timestamp").dt.date().alias("date"))
        .select("date", "expiration", "strike", "right", *REPLACED)
    )
    patch_df = patch_df.with_columns(
        *[pl.col(column).cast(chain_df.schema[column]) for column in REPLACED]
    ).rename({column: f"{column}_new" for column in REPLACED})

    repaired = dated.join(patch_df, on=["date", "expiration", "strike", "right"], how="left")
    patched = pl.col("ask_new").is_not_null()
    repaired = repaired.with_columns(
        *[pl.when(patched).then(pl.col(f"{column}_new")).otherwise(pl.col(column)).alias(column) for column in REPLACED],
        *[pl.when(patched).then(None).otherwise(pl.col(column)).alias(column) for column in NULLED if column in chain_df.columns],
    ).select(chain_df.columns)

    backup = path.with_suffix(path.suffix + ".orig")
    if not backup.exists():
        shutil.copy2(path, backup)
    write_atomic(repaired, path)
    rows = int(repaired.join(patch_df.select("date", "expiration", "strike", "right"), on=["date", "expiration", "strike", "right"], how="semi").height) if "date" in repaired.columns else patch_df.height
    print(f"\n{path.name}: {patch_df.height:,} contract-days repaired on {patch_df['date'].n_unique()} sessions"
          f" in {(time.perf_counter() - started) / 60:.1f} min; original kept as {backup.name}", flush=True)


def run(symbols: list[str], years: list[int], threshold: float, workers: int, dry_run: bool) -> None:
    for year in years:
        directory = paths.option_dir("index_greeks", year)
        for root in symbols:
            path = directory / f"{root}.parquet"
            if not path.exists():
                print(f"{path} missing, skipped")
                continue
            repair_file(root, path, threshold, workers, dry_run)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--symbols", default=",".join(INDEX_ROOTS), help="comma-separated index roots")
    parser.add_argument("--years", type=int, nargs="*", default=list(range(2017, 2026)))
    parser.add_argument("--threshold", type=float, default=0.5,
                        help="a session is repaired when fewer than this share of near-ATM contracts carry an ask")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--dry-run", action="store_true", help="count the affected sessions and requests, pull nothing")


def main(args: argparse.Namespace) -> None:
    symbols = [symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip()]
    run(symbols, args.years, args.threshold, args.workers, args.dry_run)
