"""Yahoo Finance downloads shared by more than one pipeline."""

import time
from datetime import date

import polars as pl
import yfinance as yf


def fetch_yahoo(
    yahoo_symbols: list[str], start: date, chunk_size: int
) -> pl.DataFrame:
    """Close, dividends and splits for every symbol, in batched downloads.

    Runs from `start` to today so that every split Yahoo has applied to its own
    closes is in the table. Yahoo back-adjusts for every split up to *today*,
    not up to the end of the requested window, so stopping at the end of the
    price panel silently omits later splits and the whole name then disagrees:
    BKNG's 25:1 on 2026-04-06 makes its 2025 closes look 25x too high.
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
