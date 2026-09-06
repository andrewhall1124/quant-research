"""Build the stock-month panel behind Goyal and Saretto (2008).

One row per (symbol, formation month): the signal known at the close of the
formation day, the quotes on the entry day, and the option payoffs at the
following monthly expiration. Everything the portfolio tables need, and
nothing that depends on how the deciles are cut.

The paper's calendar, translated to modern listed equity options:

* Monthly options expire on the third Friday (the paper's era settled on the
  Saturday after). If that Friday is an exchange holiday the expiry is the
  Thursday before.
* Formation day is the first session after the expiry; the signal uses that
  day's close. Trading begins the next session at the closing bid/ask
  midpoint, and the position is held to the next monthly expiry, where it is
  settled at intrinsic value.
* HV is the annualized standard deviation of split-adjusted daily log returns
  over the 252 sessions ending on the formation day. IV is the mean of the
  ATM call and put implied vols on the formation day, for the contract that
  will be traded.

Run from the project root:

    uv run python -m research.goyal_saretto.panel

It writes `results/panel.parquet` in a few minutes; `analysis.py` reads it.
"""

import argparse
import calendar
import time
from datetime import date, timedelta
from pathlib import Path

import polars as pl

import data_access_layer as dal

STUDY_DIR = Path(__file__).resolve().parent
RESULTS_DIR = STUDY_DIR / "results"
PANEL_PATH = RESULTS_DIR / "panel.parquet"

HV_WINDOW = 252
HV_MIN_OBSERVATIONS = 200
# The paper keeps strikes within 2.5% of spot and, as a robustness check, 5%.
# Candidates are pulled at the wider band so the same cache serves both.
CANDIDATE_BAND = 0.05
# 3% of contract-days fail to invert; their IV is pinned near 0.5 with a
# +/-100 error. See data_access_layer.options.load_option_greeks.
MAX_IV_ERROR = 1.0

CHAIN_COLUMNS = [
    "date", "expiration", "strike", "right", "bid", "ask", "volume",
    "delta", "implied_vol", "iv_error", "underlying_price",
]


def third_friday(year: int, month: int) -> date:
    first_weekday = calendar.monthrange(year, month)[0]  # Monday == 0
    offset = (calendar.FRIDAY - first_weekday) % 7
    return date(year, month, 1 + offset + 14)


def build_calendar() -> list[date]:
    """Sessions the exchange was open, taken from the universe's daily rows."""
    return dal.load_universe(lazy=True).select("date").unique().sort("date").collect()["date"].to_list()


def build_schedule(sessions: list[date], start_year: int, end_year: int) -> pl.DataFrame:
    """One row per formation month: expiry just passed, formation day, entry
    day, and the expiry the position will be held to."""
    session_set = set(sessions)

    def monthly_expiry(year: int, month: int) -> date:
        expiry = third_friday(year, month)
        while expiry not in session_set:
            expiry -= timedelta(days=1)
        return expiry

    def next_session(day: date, count: int = 1) -> date | None:
        later = [session for session in sessions if session > day]
        return later[count - 1] if len(later) >= count else None

    rows = []
    for year in range(start_year, end_year + 1):
        for month in range(1, 13):
            formation_expiry = monthly_expiry(year, month)
            next_year, next_month = (year, month + 1) if month < 12 else (year + 1, 1)
            if date(next_year, next_month, 1) > sessions[-1]:
                continue
            target_expiry = monthly_expiry(next_year, next_month)
            formation = next_session(formation_expiry)
            entry = next_session(formation_expiry, 2)
            if formation is None or entry is None or target_expiry > sessions[-1]:
                continue
            previous = max(session for session in sessions if session < formation)
            rows.append({
                "month": date(year, month, 1),
                "previous_day": previous,
                "formation_day": formation,
                "entry_day": entry,
                "expiration": target_expiry,
            })
    return pl.DataFrame(rows)


def compute_spot_history(chain: pl.LazyFrame) -> pl.DataFrame:
    """Daily spot and trailing HV from the price stamped on every chain row.

    The stock loader is floored at 2023-06, so the option history is the only
    source of a 2017 close. Splits come from the corporate actions table so a
    2-for-1 day is not a -50% return.
    """
    spot = (
        chain.filter(pl.col("underlying_price") > 0)
        .sort("underlying_timestamp")
        .group_by("date")
        .agg(pl.col("symbol").last(), pl.col("underlying_price").last().alias("spot"))
        .sort("date")
    )
    # The join does not preserve order, and the rolling window needs it.
    spot = dal.with_corporate_actions(spot).sort("date").with_columns(
        (pl.col("spot") * pl.col("split_ratio") / pl.col("spot").shift(1)).log().alias("log_return")
    )
    return spot.with_columns(
        (pl.col("log_return").rolling_std(HV_WINDOW, min_samples=HV_MIN_OBSERVATIONS) * (252 ** 0.5)).alias("hv"),
        pl.col("log_return").rolling_sum(21).alias("return_1m"),
    ).select("symbol", "date", "spot", "log_return", "hv", "return_1m").collect()


def extract_symbol(symbol: str, schedule_df: pl.DataFrame) -> pl.DataFrame | None:
    """Every candidate contract-day this symbol contributes to the panel.

    Rows on the previous, formation and entry days for the next monthly
    expiry, within the candidate moneyness band, with call and put side by
    side. HV and spot at expiration are attached here so the caller never
    needs the chain again.
    """
    try:
        chain = dal.load_option_greeks(symbol, lazy=True)
    except dal.MissingDataset:
        return None
    spot_df = compute_spot_history(chain)
    if spot_df.is_empty():
        return None

    wanted_days = pl.concat([
        schedule_df["previous_day"], schedule_df["formation_day"], schedule_df["entry_day"],
    ]).unique()
    quotes_df = (
        chain.select(CHAIN_COLUMNS)
        .filter(
            pl.col("date").is_in(wanted_days.implode()),
            pl.col("expiration").is_in(schedule_df["expiration"].implode()),
            pl.col("underlying_price") > 0,
            (pl.col("strike") / pl.col("underlying_price") - 1).abs() <= CANDIDATE_BAND,
        )
        .collect()
    )
    if quotes_df.is_empty():
        return None

    wide_df = (
        quotes_df.pivot(
            on="right",
            index=["date", "expiration", "strike", "underlying_price"],
            values=["bid", "ask", "volume", "delta", "implied_vol", "iv_error"],
        )
        .rename({
            f"{field}_{right}": f"{right.lower()}_{field}"
            for field in ("bid", "ask", "volume", "delta", "implied_vol", "iv_error")
            for right in ("CALL", "PUT")
        })
    )
    for side in ("call", "put"):
        if f"{side}_bid" not in wide_df.columns:
            return None

    # Stale quotes: bid and ask both unchanged from the previous session.
    previous_df = wide_df.select(
        "date", "expiration", "strike",
        pl.col("call_bid").alias("call_bid_prev"), pl.col("call_ask").alias("call_ask_prev"),
        pl.col("put_bid").alias("put_bid_prev"), pl.col("put_ask").alias("put_ask_prev"),
    )
    days_df = pl.concat([
        schedule_df.select(pl.col("formation_day").alias("date"), pl.col("previous_day").alias("quote_day_prev"), "expiration"),
        schedule_df.select(pl.col("entry_day").alias("date"), pl.col("formation_day").alias("quote_day_prev"), "expiration"),
    ])
    wide_df = (
        wide_df.join(days_df, on=["date", "expiration"], how="inner")
        .join(
            previous_df.rename({"date": "quote_day_prev"}),
            on=["quote_day_prev", "expiration", "strike"], how="left",
        )
        .with_columns(
            pl.lit(symbol).alias("symbol"),
            (pl.col("strike") / pl.col("underlying_price")).alias("moneyness"),
            *[
                (
                    (pl.col(f"{side}_bid") == pl.col(f"{side}_bid_prev"))
                    & (pl.col(f"{side}_ask") == pl.col(f"{side}_ask_prev"))
                ).fill_null(False).alias(f"{side}_stale")
                for side in ("call", "put")
            ],
        )
        .drop("quote_day_prev", "call_bid_prev", "call_ask_prev", "put_bid_prev", "put_ask_prev")
    )
    expiry_spot_df = spot_df.select(pl.col("date").alias("expiration"), pl.col("spot").alias("spot_at_expiry"))
    hv_df = spot_df.select(pl.col("date"), "hv", "return_1m")
    return (
        wide_df.join(expiry_spot_df, on="expiration", how="left")
        .join(hv_df, on="date", how="left")
    )


def is_clean_quote(side: str) -> pl.Expr:
    """The paper's quote filters: a positive bid, an ask above it, volume and
    a quote that moved since yesterday. The 1996-2005 minimum tick was a
    nickel; on penny-quoted names the spread floor is simply ask > bid."""
    return (
        (pl.col(f"{side}_bid") > 0)
        & (pl.col(f"{side}_ask") > pl.col(f"{side}_bid"))
        & (pl.col(f"{side}_volume") > 0)
        & ~pl.col(f"{side}_stale")
    )


def has_valid_iv(side: str) -> pl.Expr:
    return (pl.col(f"{side}_implied_vol") > 0) & (pl.col(f"{side}_iv_error").abs() <= MAX_IV_ERROR)


def select_contracts(candidates_df: pl.DataFrame, schedule_df: pl.DataFrame) -> pl.DataFrame:
    """Pick the cleanest closest-to-ATM strike on the formation day, then
    attach the same contract's entry-day quotes and its expiry payoff."""
    formation_df = (
        candidates_df.join(
            schedule_df.select("month", pl.col("formation_day").alias("date"), "expiration", "entry_day"),
            on=["date", "expiration"], how="inner",
        )
        .filter(
            is_clean_quote("call"), is_clean_quote("put"),
            has_valid_iv("call"), has_valid_iv("put"),
            pl.col("hv").is_not_null(),
        )
        .with_columns((pl.col("moneyness") - 1).abs().alias("atm_distance"))
        .sort("symbol", "month", "atm_distance", "strike")
        .group_by("symbol", "month", maintain_order=True)
        .first()
        .select(
            "symbol", "month", pl.col("date").alias("formation_day"), "entry_day", "expiration",
            "strike", pl.col("underlying_price").alias("spot_formation"), "moneyness", "hv",
            "return_1m",
            pl.col("call_implied_vol").alias("call_iv_formation"),
            pl.col("put_implied_vol").alias("put_iv_formation"),
            ((pl.col("call_bid") + pl.col("call_ask")) / 2).alias("call_mid_formation"),
            ((pl.col("put_bid") + pl.col("put_ask")) / 2).alias("put_mid_formation"),
        )
    )
    entry_df = candidates_df.select(
        "symbol", pl.col("date").alias("entry_day"), "expiration", "strike",
        pl.col("underlying_price").alias("spot_entry"), "spot_at_expiry",
        "call_bid", "call_ask", "call_volume", "call_delta", "call_stale",
        "put_bid", "put_ask", "put_volume", "put_delta", "put_stale",
    )
    return (
        formation_df.join(entry_df, on=["symbol", "entry_day", "expiration", "strike"], how="inner")
        .filter(is_clean_quote("call"), is_clean_quote("put"), pl.col("spot_at_expiry") > 0)
        .drop("call_stale", "put_stale")
        .with_columns(
            ((pl.col("call_iv_formation") + pl.col("put_iv_formation")) / 2).alias("iv"),
            ((pl.col("call_bid") + pl.col("call_ask")) / 2).alias("call_mid"),
            ((pl.col("put_bid") + pl.col("put_ask")) / 2).alias("put_mid"),
            (pl.col("spot_at_expiry") - pl.col("strike")).clip(lower_bound=0).alias("call_payoff"),
            (pl.col("strike") - pl.col("spot_at_expiry")).clip(lower_bound=0).alias("put_payoff"),
        )
        .with_columns((pl.col("hv").log() - pl.col("iv").log()).alias("signal"))
        .sort("month", "symbol")
    )


def filter_to_members(panel_df: pl.DataFrame) -> pl.DataFrame:
    """Keep stock-months where the name was in the S&P 500 on the formation
    day and the chain is known not to be a different company's."""
    members_df = (
        dal.load_universe()
        .with_columns(pl.col("ticker").replace(dal.THETA_STOCK_OVERRIDES).alias("symbol"))
        .select(pl.col("date").alias("formation_day"), "symbol")
        .unique()
    )
    return (
        panel_df.join(members_df, on=["formation_day", "symbol"], how="semi")
        .with_columns(pl.col("formation_day").dt.year().alias("year"))
        .join(dal.usable_symbol_years(), on=["symbol", "year"], how="semi")
    )


def with_earnings_flag(panel_df: pl.DataFrame) -> pl.DataFrame:
    """Whether an announcement falls inside the holding period, for the
    paper's earnings robustness check."""
    earnings_df = dal.load_earnings(lazy=True).select("symbol", pl.col("date").alias("earnings_date"))
    flagged = (
        panel_df.lazy()
        .join(earnings_df, on="symbol", how="left")
        .with_columns(
            pl.col("earnings_date").is_between(pl.col("entry_day"), pl.col("expiration")).alias("hit")
        )
        .group_by("symbol", "month")
        .agg(pl.col("hit").any().fill_null(False).alias("earnings_in_hold"))
        .collect()
    )
    return panel_df.join(flagged, on=["symbol", "month"], how="left").with_columns(
        pl.col("earnings_in_hold").fill_null(False)
    )


def build_panel(start_year: int, end_year: int, symbols: list[str] | None = None) -> pl.DataFrame:
    sessions = build_calendar()
    schedule_df = build_schedule(sessions, start_year, end_year)
    if symbols is None:
        symbols = sorted({
            symbol
            for year in dal.available_years("option_greeks")
            for symbol in dal.available_option_symbols(year=year)
        })
    started = time.time()
    pieces = []
    for count, symbol in enumerate(symbols, 1):
        piece = extract_symbol(symbol, schedule_df)
        if piece is not None and not piece.is_empty():
            pieces.append(piece)
        if count % 50 == 0 or count == len(symbols):
            print(f"  {count}/{len(symbols)} symbols, {time.time() - started:.0f}s", flush=True)
    candidates_df = pl.concat(pieces, how="vertical_relaxed")
    panel_df = select_contracts(candidates_df, schedule_df)
    panel_df = filter_to_members(panel_df)
    return with_earnings_flag(panel_df)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start-year", type=int, default=2017)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument("--symbols", nargs="*", help="restrict to these roots (debugging)")
    args = parser.parse_args()
    RESULTS_DIR.mkdir(exist_ok=True)
    panel_df = build_panel(args.start_year, args.end_year, args.symbols or None)
    panel_df.write_parquet(PANEL_PATH)
    print(f"wrote {PANEL_PATH}: {panel_df.height} stock-months, "
          f"{panel_df['symbol'].n_unique()} symbols, {panel_df['month'].n_unique()} months")


if __name__ == "__main__":
    main()
