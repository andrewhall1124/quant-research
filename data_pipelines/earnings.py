"""Earnings announcement dates from Yahoo, for the whole universe.

A cross-sectional volatility signal is an earnings-timing machine unless you
can see the announcements: implied vol lifts into a report, so any ranking on
implied-minus-realized puts "names reporting soon" at one end and "names that
just reported" at the other, for reasons that have nothing to do with a
volatility premium. This table is what lets a study neutralise that, or measure
it deliberately.

Yahoo carries about 100 announcements per name, which reaches back to roughly
2002 — deeper than any option history ThetaData sells, so this never becomes
the binding constraint.

    uv run python -m data_pipelines.earnings
"""

import argparse
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import polars as pl
import yfinance as yf

from data_access_layer import paths
from data_pipelines.common import normalize_ticker

# Yahoo stamps the announcement with its scheduled time, which is how the
# session is recovered: 16:00 or later is after the close, anything before
# 09:30 is before the open. A handful carry neither and are left unknown.
MARKET_OPEN_HOUR = 9.5
MARKET_CLOSE_HOUR = 16.0


def fetch_earnings(yahoo_symbol: str, limit: int, attempts: int = 3) -> pl.DataFrame | None:
    """Announcements for one symbol, or None if Yahoo has nothing usable."""
    for attempt in range(attempts):
        try:
            raw = yf.Ticker(yahoo_symbol).get_earnings_dates(limit=limit)
            break
        except Exception:
            if attempt == attempts - 1:
                return None
            time.sleep(2**attempt)
    if raw is None or raw.empty:
        return None

    frame = raw.reset_index()
    frame.columns = [
        "announced_at", "eps_estimate", "reported_eps", "surprise_pct"
    ][: len(frame.columns)]
    frame["yahoo_symbol"] = yahoo_symbol
    return pl.from_pandas(frame)


def classify_session(frame: pl.DataFrame) -> pl.DataFrame:
    """Add the announcement date and whether it lands before or after the bell."""
    hour = pl.col("announced_at").dt.hour() + pl.col("announced_at").dt.minute() / 60
    return frame.with_columns(
        pl.col("announced_at").dt.date().alias("date"),
        pl.when(hour >= MARKET_CLOSE_HOUR)
        .then(pl.lit("amc"))
        .when(hour < MARKET_OPEN_HOUR)
        .then(pl.lit("bmo"))
        .otherwise(pl.lit("unknown"))
        .alias("session"),
    )


def run(universe_path: str, limit: int, workers: int) -> None:
    tickers = (
        pl.read_parquet(universe_path)
        .select("ticker")
        .unique()
        .sort("ticker")["ticker"]
        .to_list()
    )
    pairs = [
        (normalize_ticker(ticker, "stock"), normalize_ticker(ticker, "yahoo"))
        for ticker in tickers
    ]
    print(f"earnings: {len(pairs)} symbols, up to {limit} announcements each")

    started = time.perf_counter()
    frames, missing = [], []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(fetch_earnings, yahoo_symbol, limit): (symbol, yahoo_symbol)
            for symbol, yahoo_symbol in pairs
        }
        for done, future in enumerate(as_completed(futures), start=1):
            symbol, yahoo_symbol = futures[future]
            frame = future.result()
            if frame is None:
                missing.append(symbol)
            else:
                frames.append(frame.with_columns(pl.lit(symbol).alias("symbol")))
            elapsed = time.perf_counter() - started
            print(
                f"\r  {done}/{len(pairs)} | {elapsed:6.1f}s | {len(missing)} without data",
                end="",
                flush=True,
            )
    print()

    earnings_df = (
        classify_session(pl.concat(frames, how="vertical_relaxed"))
        .select(
            "symbol", "yahoo_symbol", "date", "session",
            pl.col("announced_at").dt.convert_time_zone("America/New_York"),
            pl.col("eps_estimate").cast(pl.Float64),
            pl.col("reported_eps").cast(pl.Float64),
            pl.col("surprise_pct").cast(pl.Float64),
        )
        .unique(subset=["symbol", "date"])
        .sort("symbol", "date")
    )
    paths.EARNINGS.parent.mkdir(parents=True, exist_ok=True)
    earnings_df.write_parquet(paths.EARNINGS)

    print(
        f"\ndone in {time.perf_counter() - started:.1f}s"
        f" | {earnings_df.height:,} announcements"
        f" | {earnings_df['symbol'].n_unique()} symbols"
        f" | {earnings_df['date'].min()} .. {earnings_df['date'].max()}"
    )
    print(earnings_df.group_by("session").agg(pl.len().alias("rows")).sort("rows", descending=True))
    if missing:
        print(f"no earnings data for {len(missing)}: {sorted(missing)[:20]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", default=str(paths.UNIVERSE))
    parser.add_argument("--limit", type=int, default=100, help="announcements per symbol")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    run(args.universe, args.limit, args.workers)
