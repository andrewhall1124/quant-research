"""One row per earnings event: the ATM straddle around the print.

For each S&P 500 announcement with a known session, two windows on the
same contract:

* **through the print**: entered at the last close before the release and
  closed at the first close after it. A before-open report on day E is
  entered at E-1 and exited at E; an after-close report is entered at E and
  exited at E+1.
* **run-up**: entered five sessions before the print entry and closed at the
  print entry, so it holds the pre-announcement IV rise and none of the
  event.

Each window selects its own contract on its own entry day: the strike
closest to spot, within 2.5%, on the nearest expiry that still has at least
five calendar days left after the print exit. Selecting the run-up contract
on the print-entry day instead would bias it, since spot has by then moved
toward that strike. Quotes are midpoints; a leg with a zero bid is marked at
half the ask, and a row with no ask is dropped.

Run from the project root:

    uv run python -m research.earnings_straddles.panel
"""

import time
from pathlib import Path

import polars as pl

import data_access_layer as dal
from research.goyal_saretto.panel import MAX_IV_ERROR, build_calendar

STUDY_DIR = Path(__file__).resolve().parent
RESULTS_DIR = STUDY_DIR / "results"
PANEL_PATH = RESULTS_DIR / "panel.parquet"

RUNUP_SESSIONS = 5
MIN_DAYS_AFTER_EXIT = 5
MAX_DTE = 60
BAND = 0.025
CANDIDATE_BAND = 0.10

QUOTE_COLUMNS = ["date", "expiration", "strike", "right", "bid", "ask", "volume", "delta",
                 "implied_vol", "iv_error", "underlying_price"]


def build_events() -> pl.DataFrame:
    """Announcements for names in the index that day, with the three session
    dates each window needs."""
    sessions = build_calendar()
    index = {day: position for position, day in enumerate(sessions)}
    first, last = sessions[0], sessions[-1]
    members = (
        dal.load_universe()
        .with_columns(pl.col("ticker").replace(dal.THETA_STOCK_OVERRIDES).alias("symbol"))
        .select("date", "symbol").unique()
    )
    events = (
        dal.load_earnings(first, last)
        .filter(pl.col("session").is_in(["bmo", "amc"]), pl.col("date").is_in(pl.Series(sessions).implode()))
        .join(members, on=["date", "symbol"], how="semi")
        .select("symbol", "date", "session")
        .unique()
    )
    rows = []
    for row in events.iter_rows(named=True):
        position = index[row["date"]]
        entry_position = position - 1 if row["session"] == "bmo" else position
        exit_position = entry_position + 1
        runup_position = entry_position - RUNUP_SESSIONS
        if runup_position < 0 or exit_position >= len(sessions):
            continue
        rows.append({
            "symbol": row["symbol"], "event_date": row["date"], "session": row["session"],
            "runup_day": sessions[runup_position], "entry_day": sessions[entry_position],
            "exit_day": sessions[exit_position],
        })
    return pl.DataFrame(rows).sort("symbol", "event_date")


def extract_symbol(symbol: str, events_df: pl.DataFrame) -> pl.DataFrame | None:
    try:
        chain = dal.load_option_greeks(symbol, lazy=True)
    except dal.MissingDataset:
        return None
    wanted = pl.concat([events_df["runup_day"], events_df["entry_day"], events_df["exit_day"]]).unique()
    quotes = (
        chain.select(QUOTE_COLUMNS)
        .filter(
            pl.col("date").is_in(wanted.implode()),
            pl.col("underlying_price") > 0,
            (pl.col("expiration") - pl.col("date")).dt.total_days().is_between(1, MAX_DTE + RUNUP_SESSIONS * 2),
            (pl.col("strike") / pl.col("underlying_price") - 1).abs() <= CANDIDATE_BAND,
            pl.col("ask") > 0,
        )
        .with_columns(((pl.col("bid") + pl.col("ask")) / 2).alias("mid"))
        .collect()
    )
    if quotes.is_empty():
        return None
    wide = quotes.pivot(
        on="right", index=["date", "expiration", "strike", "underlying_price"],
        values=["bid", "ask", "mid", "volume", "delta", "implied_vol", "iv_error"],
    )
    needed = [f"{field}_{right}" for field in ("bid", "ask", "mid", "volume", "delta", "implied_vol", "iv_error")
              for right in ("CALL", "PUT")]
    if any(column not in wide.columns for column in needed):
        return None
    wide = wide.rename({column: column.split("_")[-1].lower() + "_" + "_".join(column.split("_")[:-1]) for column in needed})

    def select(entry_column: str, prefix: str) -> pl.DataFrame:
        """Closest-to-ATM clean straddle on the window's entry day."""
        return (
            wide.join(events_df.select("symbol", "event_date", pl.col(entry_column).alias("date"), "exit_day"), on="date", how="inner")
            .filter(
                (pl.col("strike") / pl.col("underlying_price") - 1).abs() <= BAND,
                (pl.col("expiration") - pl.col("exit_day")).dt.total_days() >= MIN_DAYS_AFTER_EXIT,
                (pl.col("expiration") - pl.col("date")).dt.total_days() <= MAX_DTE,
                pl.col("call_bid") > 0, pl.col("put_bid") > 0,
                pl.col("call_ask") > pl.col("call_bid"), pl.col("put_ask") > pl.col("put_bid"),
                pl.col("call_volume") > 0, pl.col("put_volume") > 0,
                pl.col("call_implied_vol") > 0, pl.col("put_implied_vol") > 0,
                pl.col("call_iv_error").abs() <= MAX_IV_ERROR, pl.col("put_iv_error").abs() <= MAX_IV_ERROR,
            )
            .with_columns((pl.col("strike") / pl.col("underlying_price") - 1).abs().alias("distance"))
            .sort("event_date", "expiration", "distance")
            .group_by("event_date", maintain_order=True).first()
            .select(
                "symbol", "event_date", pl.col("date").alias(entry_column), "exit_day",
                pl.col("expiration").alias(f"{prefix}expiration"), pl.col("strike").alias(f"{prefix}strike"),
                pl.col("underlying_price").alias(f"{prefix}spot_entry"),
                (pl.col("expiration") - pl.col("date")).dt.total_days().alias(f"{prefix}dte"),
                pl.col("call_bid").alias(f"{prefix}call_bid"), pl.col("call_ask").alias(f"{prefix}call_ask"),
                pl.col("put_bid").alias(f"{prefix}put_bid"), pl.col("put_ask").alias(f"{prefix}put_ask"),
                (pl.col("call_delta") + pl.col("put_delta")).alias(f"{prefix}net_delta"),
                ((pl.col("call_implied_vol") + pl.col("put_implied_vol")) / 2).alias(f"{prefix}iv_entry"),
                (pl.col("call_mid") + pl.col("put_mid")).alias(f"{prefix}straddle_entry"),
            )
        )

    marks = wide.select(
        "date", "expiration", "strike", pl.col("underlying_price").alias("spot"),
        (pl.col("call_mid") + pl.col("put_mid")).alias("straddle"),
        pl.when((pl.col("call_implied_vol") > 0) & (pl.col("call_iv_error").abs() <= MAX_IV_ERROR)
                & (pl.col("put_implied_vol") > 0) & (pl.col("put_iv_error").abs() <= MAX_IV_ERROR))
        .then((pl.col("call_implied_vol") + pl.col("put_implied_vol")) / 2).alias("iv"),
    )
    print_df = select("entry_day", "").join(
        marks.rename({"date": "exit_day", "spot": "spot_exit", "straddle": "straddle_exit", "iv": "iv_exit"}),
        on=["exit_day", "expiration", "strike"], how="inner",
    )
    runup_df = select("runup_day", "runup_").drop("exit_day").join(
        events_df.select("event_date", "entry_day"), on="event_date", how="inner",
    ).join(
        marks.rename({"date": "entry_day", "expiration": "runup_expiration", "strike": "runup_strike",
                      "spot": "runup_spot_exit", "straddle": "runup_straddle_exit", "iv": "runup_iv_exit"}),
        on=["entry_day", "runup_expiration", "runup_strike"], how="inner",
    ).drop("entry_day")
    return print_df.join(runup_df, on=["symbol", "event_date"], how="left")


def main() -> None:
    RESULTS_DIR.mkdir(exist_ok=True)
    events_df = build_events()
    print(f"{events_df.height} events, {events_df['symbol'].n_unique()} symbols")
    started = time.time()
    pieces = []
    symbols = events_df["symbol"].unique().sort().to_list()
    for count, symbol in enumerate(symbols, 1):
        piece = extract_symbol(symbol, events_df.filter(pl.col("symbol") == symbol))
        if piece is not None and not piece.is_empty():
            pieces.append(piece)
        if count % 100 == 0 or count == len(symbols):
            print(f"  {count}/{len(symbols)} symbols, {time.time() - started:.0f}s", flush=True)
    panel_df = pl.concat(pieces, how="vertical_relaxed").join(events_df.select("symbol", "event_date", "session"), on=["symbol", "event_date"])
    panel_df = panel_df.with_columns(pl.col("event_date").dt.year().alias("year")).join(
        dal.usable_symbol_years(), on=["symbol", "year"], how="semi")
    panel_df.write_parquet(PANEL_PATH)
    print(f"wrote {PANEL_PATH}: {panel_df.height} events with a tradable straddle, "
          f"{panel_df['runup_straddle_entry'].is_not_null().mean():.0%} with a run-up trade")


if __name__ == "__main__":
    main()
