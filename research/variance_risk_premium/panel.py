"""Daily paths for the variance risk premium study.

Starts from the Goyal-Saretto stock-month panel (one ATM straddle per S&P 500
name per month, entered the session after the monthly expiry, held to the
next one) and adds what a variance-premium study needs that a hold-to-expiry
study does not:

* the straddle's midpoint, net delta and spot on every session of the hold,
  so it can be delta-hedged daily rather than once at entry;
* realized variance over the hold, to set against the implied variance paid.

It also builds the same stock-month rows for SPX monthly options, so the
single-name premium can be read next to the index premium everyone quotes.

Run from the project root, after `research.goyal_saretto.panel`:

    uv run python -m research.variance_risk_premium.panel
"""

import time
from pathlib import Path

import polars as pl

import data_access_layer as dal
from research.goyal_saretto.panel import (
    PANEL_PATH, build_calendar, build_schedule, compute_spot_history, is_clean_quote, has_valid_iv,
)

STUDY_DIR = Path(__file__).resolve().parent
RESULTS_DIR = STUDY_DIR / "results"
PATHS_PATH = RESULTS_DIR / "paths.parquet"
INDEX_PANEL_PATH = RESULTS_DIR / "spx_panel.parquet"

PATH_COLUMNS = ["date", "expiration", "strike", "right", "bid", "ask", "delta", "underlying_price"]


def extract_paths(symbol: str, contracts_df: pl.DataFrame, index: bool = False) -> pl.DataFrame:
    """Every session's quotes for each selected contract from entry to expiry.

    A leg with a zero bid is marked at half the ask; a leg with no ask at all
    is null and the day is skipped by the hedger.
    """
    chain = dal.load_option_greeks(symbol, index=index, lazy=True)
    rows = (
        chain.select(PATH_COLUMNS)
        .filter(
            pl.col("expiration").is_in(contracts_df["expiration"].implode()),
            pl.col("strike").is_in(contracts_df["strike"].implode()),
        )
        .collect()
        .join(contracts_df, on=["expiration", "strike"], how="inner")
        .filter(pl.col("date") >= pl.col("entry_day"), pl.col("date") <= pl.col("expiration"))
        .with_columns(
            pl.when(pl.col("ask") > 0).then((pl.col("bid") + pl.col("ask")) / 2).alias("mid"),
        )
    )
    if rows.is_empty():
        return rows.select("month", "date")
    wide = rows.pivot(
        on="right", index=["symbol", "month", "date", "expiration", "strike", "underlying_price"],
        values=["mid", "delta"],
    )
    for column in ("mid_CALL", "mid_PUT", "delta_CALL", "delta_PUT"):
        if column not in wide.columns:
            wide = wide.with_columns(pl.lit(None, dtype=pl.Float64).alias(column))
    return wide.select(
        "symbol", "month", "date", "expiration", "strike",
        pl.col("underlying_price").alias("spot"),
        (pl.col("mid_CALL") + pl.col("mid_PUT")).alias("straddle_mid"),
        (pl.col("delta_CALL") + pl.col("delta_PUT")).alias("net_delta"),
    ).sort("month", "date")


def extract_realized(symbol: str, contracts_df: pl.DataFrame, index: bool = False) -> pl.DataFrame:
    """Realized variance of daily log returns from entry to expiry, annualized."""
    chain = dal.load_option_greeks(symbol, index=index, lazy=True)
    spot_df = compute_spot_history(chain).select("date", "spot", "log_return")
    pieces = []
    for row in contracts_df.iter_rows(named=True):
        window = spot_df.filter(pl.col("date") > row["entry_day"], pl.col("date") <= row["expiration"])
        pieces.append({
            "symbol": symbol, "month": row["month"],
            "realized_var": float((window["log_return"] ** 2).sum() * 252 / max(window.height, 1)),
            "hold_sessions": window.height,
            "max_abs_return": float(window["log_return"].abs().max()) if window.height else None,
        })
    return pl.DataFrame(pieces)


def build_spx_panel(schedule_df: pl.DataFrame) -> pl.DataFrame:
    """SPX monthly ATM straddles on the same calendar as the stock panel.

    SPX monthlies settle on the opening prints of the third Friday; the payoff
    here uses the chain's spot on the expiry date, which is the close. That
    is a known approximation and the report says so.
    """
    chain = dal.load_option_greeks("SPX", index=True, lazy=True)
    days = pl.concat([schedule_df["previous_day"], schedule_df["formation_day"], schedule_df["entry_day"]]).unique()
    quotes = (
        chain.select("date", "expiration", "strike", "right", "bid", "ask", "volume", "delta",
                     "implied_vol", "iv_error", "underlying_price")
        .filter(
            pl.col("date").is_in(days.implode()),
            pl.col("expiration").is_in(schedule_df["expiration"].implode()),
            (pl.col("strike") / pl.col("underlying_price") - 1).abs() <= 0.025,
        )
        .collect()
        .pivot(on="right", index=["date", "expiration", "strike", "underlying_price"],
               values=["bid", "ask", "volume", "delta", "implied_vol", "iv_error"])
        .rename({f"{field}_{right}": f"{right.lower()}_{field}"
                 for field in ("bid", "ask", "volume", "delta", "implied_vol", "iv_error") for right in ("CALL", "PUT")})
        .with_columns(pl.lit(False).alias("call_stale"), pl.lit(False).alias("put_stale"),
                      (pl.col("strike") / pl.col("underlying_price")).alias("moneyness"))
    )
    spot_df = compute_spot_history(chain)
    formation = (
        quotes.join(schedule_df.select("month", pl.col("formation_day").alias("date"), "expiration", "entry_day"),
                    on=["date", "expiration"], how="inner")
        .filter(is_clean_quote("call"), is_clean_quote("put"), has_valid_iv("call"), has_valid_iv("put"))
        .sort("month", (pl.col("moneyness") - 1).abs())
        .group_by("month", maintain_order=True).first()
        .select("month", pl.col("date").alias("formation_day"), "entry_day", "expiration", "strike",
                ((pl.col("call_implied_vol") + pl.col("put_implied_vol")) / 2).alias("iv"))
    )
    entry = quotes.select(
        pl.col("date").alias("entry_day"), "expiration", "strike", pl.col("underlying_price").alias("spot_entry"),
        "call_bid", "call_ask", "call_delta", "put_bid", "put_ask", "put_delta", "call_stale", "put_stale",
        "call_volume", "put_volume",
    )
    expiry_spot = spot_df.select(pl.col("date").alias("expiration"), pl.col("spot").alias("spot_at_expiry"))
    return (
        formation.join(entry, on=["entry_day", "expiration", "strike"], how="inner")
        .filter(is_clean_quote("call"), is_clean_quote("put"))
        .join(expiry_spot, on="expiration", how="inner")
        .with_columns(
            pl.lit("SPX").alias("symbol"),
            ((pl.col("call_bid") + pl.col("call_ask")) / 2).alias("call_mid"),
            ((pl.col("put_bid") + pl.col("put_ask")) / 2).alias("put_mid"),
            (pl.col("spot_at_expiry") - pl.col("strike")).clip(lower_bound=0).alias("call_payoff"),
            (pl.col("strike") - pl.col("spot_at_expiry")).clip(lower_bound=0).alias("put_payoff"),
        )
        .drop("call_stale", "put_stale")
    )


def main() -> None:
    RESULTS_DIR.mkdir(exist_ok=True)
    panel_df = pl.read_parquet(PANEL_PATH)
    schedule_df = build_schedule(build_calendar(), 2017, 2025)

    started = time.time()
    paths, realized = [], []
    symbols = panel_df["symbol"].unique().sort().to_list()
    for count, symbol in enumerate(symbols, 1):
        contracts_df = panel_df.filter(pl.col("symbol") == symbol).select("symbol", "month", "entry_day", "expiration", "strike")
        paths.append(extract_paths(symbol, contracts_df))
        realized.append(extract_realized(symbol, contracts_df))
        if count % 100 == 0 or count == len(symbols):
            print(f"  {count}/{len(symbols)} symbols, {time.time() - started:.0f}s", flush=True)
    paths_df = pl.concat([piece for piece in paths if "symbol" in piece.columns], how="vertical_relaxed")
    realized_df = pl.concat(realized)
    paths_df.write_parquet(PATHS_PATH)
    realized_df.write_parquet(RESULTS_DIR / "realized.parquet")

    spx_df = build_spx_panel(schedule_df)
    spx_contracts = spx_df.select("symbol", "month", "entry_day", "expiration", "strike")
    extract_paths("SPX", spx_contracts, index=True).write_parquet(RESULTS_DIR / "spx_paths.parquet")
    spx_df.join(extract_realized("SPX", spx_contracts, index=True), on=["symbol", "month"]).write_parquet(INDEX_PANEL_PATH)
    print(f"wrote {paths_df.height} path rows for {realized_df.height} stock-months, {spx_df.height} SPX months")


if __name__ == "__main__":
    main()
