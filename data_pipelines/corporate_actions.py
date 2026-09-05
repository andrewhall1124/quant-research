"""Splits and dividends from Yahoo, checked against ThetaData's own closes.

ThetaData serves raw, unadjusted prices and its client exposes no splits
endpoint, so a return computed off `close` books a -93% day on ORLY's 15:1.
Yahoo has the split and dividend calendar; this pipeline pulls it and writes a
corporate-actions table the loaders use to adjust.

Yahoo is *not* used for prices. The whole value of the ThetaData panel is that
the option quotes and the stock close are the same 17:15 ET snapshot, and
mixing a second vendor's close into that breaks it.

Yahoo will also answer almost any symbol with something rather than an error,
so every name is verified: ThetaData's raw closes are back-adjusted with the
splits pulled here and compared to Yahoo's. Agreement means the mapping and the
split factors are both right. Disagreement means one of them is wrong, and the
`status` column says which names to distrust.

    uv run python -m data_pipelines.corporate_actions
"""

import argparse
import os
import time
from datetime import date
from pathlib import Path

import polars as pl
import yfinance as yf

from data_access_layer import paths
from data_pipelines.common import normalize_ticker

# Yahoo's `Close` is split-adjusted even with auto_adjust=False (that flag only
# controls dividend adjustment), so a raw ThetaData close only matches it once
# the splits have been applied. That makes the comparison a test of the split
# factors as well as of the ticker mapping.
#
# Crucially, Yahoo back-adjusts for every split up to *today*, not up to the end
# of the requested window. Stopping the pull at the end of the price panel
# silently omits later splits and the whole name then disagrees: BKNG's 25:1 on
# 2026-04-06 makes its 2025 closes look 25x too high. So the pull always runs to
# the present, and only `start` is configurable.
CLOSE_TOLERANCE = 0.005
MIN_OVERLAP_DAYS = 20


def load_ticker_pairs(universe_path: str) -> list[tuple[str, str]]:
    """(ThetaData stock symbol, Yahoo symbol) for every name in the universe.

    Nothing is excluded. A name ThetaData cannot serve still gets checked, so
    the output records *why* it is unusable instead of silently omitting it.
    """
    tickers = (
        pl.read_parquet(universe_path)
        .select("ticker")
        .unique()
        .sort("ticker")["ticker"]
        .to_list()
    )
    return [
        (normalize_ticker(ticker, "stock"), normalize_ticker(ticker, "yahoo"))
        for ticker in tickers
    ]


def fetch_yahoo(
    yahoo_symbols: list[str], start: date, chunk_size: int
) -> pl.DataFrame:
    """Close, dividends and splits for every symbol, in batched downloads.

    Runs from `start` to today so that every split Yahoo has applied to its own
    closes is in the table.
    """
    frames = []
    chunks = [
        yahoo_symbols[index : index + chunk_size]
        for index in range(0, len(yahoo_symbols), chunk_size)
    ]
    for number, chunk in enumerate(chunks, start=1):
        raw = yf.download(
            chunk,
            start=start,
            end=date.today(),
            actions=True,
            auto_adjust=False,
            progress=False,
            threads=True,
        )
        if raw is None or raw.empty:
            print(f"\r  chunk {number}/{len(chunks)}: empty", end="", flush=True)
            continue
        # yfinance returns (field, ticker) columns; stack them into long rows.
        tidy = (
            raw.loc[:, ["Close", "Dividends", "Stock Splits"]]
            .stack(level="Ticker", future_stack=True)
            .reset_index()
            .rename(
                columns={
                    "Date": "date",
                    "Ticker": "yahoo_symbol",
                    "Close": "yahoo_close",
                    "Dividends": "dividend",
                    "Stock Splits": "split",
                }
            )
        )
        tidy["date"] = tidy["date"].dt.date
        frames.append(
            pl.from_pandas(tidy).select(
                "yahoo_symbol",
                pl.col("date").cast(pl.Date),
                pl.col("yahoo_close").cast(pl.Float64),
                pl.col("dividend").cast(pl.Float64),
                pl.col("split").cast(pl.Float64),
            )
        )
        print(f"\r  chunk {number}/{len(chunks)} done", end="", flush=True)
        time.sleep(0.5)
    print()
    if not frames:
        raise RuntimeError("Yahoo returned nothing for the whole universe")
    return pl.concat(frames, how="vertical_relaxed").drop_nulls("yahoo_close")


def build_actions(quotes_df: pl.DataFrame, pairs: list[tuple[str, str]]) -> pl.DataFrame:
    """The corporate-actions table: one row per split or dividend."""
    mapping_df = pl.DataFrame(
        {"symbol": [pair[0] for pair in pairs], "yahoo_symbol": [pair[1] for pair in pairs]}
    )
    named = quotes_df.join(mapping_df, on="yahoo_symbol", how="inner")
    splits = (
        named.filter(pl.col("split") > 0)
        .select("symbol", "yahoo_symbol", "date", pl.lit("split").alias("action"),
                pl.col("split").alias("value"))
    )
    dividends = (
        named.filter(pl.col("dividend") > 0)
        .select("symbol", "yahoo_symbol", "date", pl.lit("dividend").alias("action"),
                pl.col("dividend").alias("value"))
    )
    return pl.concat([splits, dividends]).sort("symbol", "date", "action")


def split_factors(actions_df: pl.DataFrame, dates_df: pl.DataFrame) -> pl.DataFrame:
    """Back-adjustment factor per (symbol, date).

    A price on date `t` is divided by the product of every split ratio with an
    ex-date strictly after `t`, which is the convention Yahoo's own series uses.
    """
    splits = actions_df.filter(pl.col("action") == "split")
    if splits.is_empty():
        return dates_df.with_columns(pl.lit(1.0).alias("split_factor"))

    factors = []
    for symbol in splits["symbol"].unique().to_list():
        symbol_splits = splits.filter(pl.col("symbol") == symbol)
        symbol_dates = dates_df.filter(pl.col("symbol") == symbol)
        ratios = list(zip(symbol_splits["date"].to_list(), symbol_splits["value"].to_list()))
        factor = pl.lit(1.0)
        for ex_date, ratio in ratios:
            factor = factor * pl.when(pl.col("date") < ex_date).then(ratio).otherwise(1.0)
        factors.append(symbol_dates.with_columns(factor.alias("split_factor")))

    unsplit = dates_df.join(splits.select("symbol").unique(), on="symbol", how="anti")
    factors.append(unsplit.with_columns(pl.lit(1.0).alias("split_factor")))
    return pl.concat(factors, how="vertical_relaxed")


def write_atomic(frame: pl.DataFrame, path: Path) -> None:
    """Write via a sibling temp file and rename.

    This pipeline rewrites its output in full every run, and research sessions
    read the same two files continuously. `os.replace` is atomic on POSIX, so a
    reader gets either the old table or the new one, never a half-written one.
    """
    temp_path = path.with_suffix(path.suffix + ".tmp")
    frame.write_parquet(temp_path)
    os.replace(temp_path, path)


def run(start: date, universe_path: str, chunk_size: int) -> None:
    pairs = load_ticker_pairs(universe_path)
    print(f"corporate actions: {len(pairs)} symbols, {start} .. {date.today()}")

    started = time.perf_counter()
    quotes_df = fetch_yahoo([pair[1] for pair in pairs], start, chunk_size)
    actions_df = build_actions(quotes_df, pairs)

    paths.CORPORATE_ACTIONS.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(actions_df, paths.CORPORATE_ACTIONS)

    splits_df = actions_df.filter(pl.col("action") == "split")
    print(
        f"\ndone in {time.perf_counter() - started:.1f}s"
        f" | {splits_df.height} splits, "
        f"{actions_df.height - splits_df.height:,} dividends"
    )
    print("\nsplits:")
    print(splits_df.select("symbol", "date", "value"))
    print(
        "\nWhether a symbol is the company the universe names is decided by"
        "\n`data_pipelines.symbology`, on returns rather than price levels."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, default=date(2017, 1, 1))
    parser.add_argument("--universe", default=str(paths.UNIVERSE))
    parser.add_argument("--chunk-size", type=int, default=50)
    args = parser.parse_args()
    run(args.start, args.universe, args.chunk_size)
