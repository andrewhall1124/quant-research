"""Implied-vol skew and term slope on each formation day.

Two shape measures of the surface, taken from the chain on the session
after each monthly expiry (the Goyal-Saretto formation day):

* **skew**, after Xing, Zhang and Zhao (2010): IV of the OTM put with
  moneyness closest to 0.95 within 0.80-0.95, minus IV of the ATM call with
  moneyness closest to 1.00 within 0.95-1.05, both on the nearest expiry
  with 10-60 days to go. Their finding is that steep skew predicts low
  stock returns.
* **term slope**, after Vasquez (2017): ATM IV on the expiry closest to 90
  days (60-150) minus ATM IV on the expiry closest to 30 days (20-45), each
  the mean of the closest-to-ATM call and put. His finding is that steep
  (upward) slopes predict high straddle returns.

Both need quoted, inverted contracts: bid above zero and an IV error of at
most one point. Run from the project root:

    uv run python -m research.iv_skew_slope.panel
"""

import time
from pathlib import Path

import polars as pl

import data_access_layer as dal
from research.goyal_saretto.panel import MAX_IV_ERROR, PANEL_PATH, build_calendar, build_schedule

STUDY_DIR = Path(__file__).resolve().parent
RESULTS_DIR = STUDY_DIR / "results"
FEATURES_PATH = RESULTS_DIR / "features.parquet"

COLUMNS = ["date", "expiration", "strike", "right", "bid", "implied_vol", "iv_error", "underlying_price", "delta"]


def extract_symbol(symbol: str, formation_days: pl.Series) -> pl.DataFrame | None:
    try:
        chain = dal.load_option_greeks(symbol, lazy=True)
    except dal.MissingDataset:
        return None
    rows = (
        chain.select(COLUMNS)
        .filter(
            pl.col("date").is_in(formation_days.implode()),
            pl.col("underlying_price") > 0, pl.col("bid") > 0,
            pl.col("implied_vol") > 0, pl.col("iv_error").abs() <= MAX_IV_ERROR,
        )
        .with_columns(
            (pl.col("expiration") - pl.col("date")).dt.total_days().alias("dte"),
            (pl.col("strike") / pl.col("underlying_price")).alias("moneyness"),
        )
        .filter(pl.col("dte").is_between(10, 150), pl.col("moneyness").is_between(0.75, 1.10))
        .collect()
    )
    if rows.is_empty():
        return None

    def nearest_expiry(frame: pl.DataFrame, target: int, low: int, high: int) -> pl.DataFrame:
        """Per date, keep the expiry inside [low, high] closest to `target` days."""
        chosen = (
            frame.filter(pl.col("dte").is_between(low, high))
            .with_columns((pl.col("dte") - target).abs().alias("gap"))
            .sort("date", "gap")
            .group_by("date", maintain_order=True).agg(pl.col("expiration").first())
        )
        return frame.join(chosen, on=["date", "expiration"], how="inner")

    def atm_iv(frame: pl.DataFrame, name: str) -> pl.DataFrame:
        """Mean of the closest-to-ATM call and put IV, per date."""
        return (
            frame.filter(pl.col("moneyness").is_between(0.95, 1.05))
            .with_columns((pl.col("moneyness") - 1).abs().alias("distance"))
            .sort("date", "right", "distance")
            .group_by("date", "right", maintain_order=True).agg(pl.col("implied_vol").first(), pl.col("dte").first())
            .group_by("date").agg(pl.col("implied_vol").mean().alias(name), pl.col("dte").first().alias(f"{name}_dte"), pl.len().alias("legs"))
            .filter(pl.col("legs") == 2).drop("legs")
        )

    # Skew: nearest expiry to 30 days within 10-60.
    skew_frame = nearest_expiry(rows, 30, 10, 60)
    otm_put = (
        skew_frame.filter(pl.col("right") == "PUT", pl.col("moneyness").is_between(0.80, 0.95))
        .with_columns((pl.col("moneyness") - 0.95).abs().alias("distance"))
        .sort("date", "distance").group_by("date", maintain_order=True)
        .agg(pl.col("implied_vol").first().alias("otm_put_iv"), pl.col("moneyness").first().alias("otm_put_moneyness"))
    )
    atm_call = (
        skew_frame.filter(pl.col("right") == "CALL", pl.col("moneyness").is_between(0.95, 1.05))
        .with_columns((pl.col("moneyness") - 1).abs().alias("distance"))
        .sort("date", "distance").group_by("date", maintain_order=True)
        .agg(pl.col("implied_vol").first().alias("atm_call_iv"), pl.col("dte").first().alias("skew_dte"))
    )
    skew = otm_put.join(atm_call, on="date", how="inner").with_columns(
        (pl.col("otm_put_iv") - pl.col("atm_call_iv")).alias("skew"))

    # Term slope: ATM IV at ~30 and ~90 days.
    short = atm_iv(nearest_expiry(rows, 30, 20, 45), "iv_short")
    long = atm_iv(nearest_expiry(rows, 90, 60, 150), "iv_long")
    slope = short.join(long, on="date", how="inner").with_columns((pl.col("iv_long") - pl.col("iv_short")).alias("slope"))

    return (
        skew.join(slope, on="date", how="full", coalesce=True)
        .with_columns(pl.lit(symbol).alias("symbol"))
        .rename({"date": "formation_day"})
    )


def main() -> None:
    RESULTS_DIR.mkdir(exist_ok=True)
    panel_df = pl.read_parquet(PANEL_PATH)
    schedule_df = build_schedule(build_calendar(), 2017, 2025)
    formation_days = schedule_df["formation_day"]
    symbols = panel_df["symbol"].unique().sort().to_list()
    started = time.time()
    pieces = []
    for count, symbol in enumerate(symbols, 1):
        piece = extract_symbol(symbol, formation_days)
        if piece is not None and not piece.is_empty():
            pieces.append(piece)
        if count % 100 == 0 or count == len(symbols):
            print(f"  {count}/{len(symbols)} symbols, {time.time() - started:.0f}s", flush=True)
    features_df = pl.concat(pieces, how="diagonal_relaxed")
    features_df.write_parquet(FEATURES_PATH)
    print(f"wrote {FEATURES_PATH}: {features_df.height} symbol-days, "
          f"skew on {features_df['skew'].is_not_null().mean():.0%}, slope on {features_df['slope'].is_not_null().mean():.0%}")


if __name__ == "__main__":
    main()
