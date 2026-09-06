"""Volatility features on each formation day, for sorting the premium.

For every (symbol, formation month) in the Goyal-Saretto panel:

* realized variance over the last 1, 5 and 22 sessions, the HAR components
  of Corsi (2009), and the realized variance over the following 21 sessions
  as the forecasting target;
* idiosyncratic volatility per Cao and Han (2013): the standard deviation of
  residuals from a one-factor regression on the equal-weighted universe
  return over the last 21 sessions, and its total-vol counterpart.

Run from the project root, after the Goyal-Saretto panel:

    uv run python -m research.vrp_cross_section.panel
"""

import time
from pathlib import Path

import polars as pl

import data_access_layer as dal
from research.goyal_saretto.panel import PANEL_PATH, compute_spot_history

STUDY_DIR = Path(__file__).resolve().parent
RESULTS_DIR = STUDY_DIR / "results"
FEATURES_PATH = RESULTS_DIR / "features.parquet"

FORWARD_SESSIONS = 21
IDIO_WINDOW = 21


def load_returns(symbols: list[str]) -> pl.DataFrame:
    started = time.time()
    pieces = []
    for count, symbol in enumerate(symbols, 1):
        try:
            chain = dal.load_option_greeks(symbol, lazy=True)
        except dal.MissingDataset:
            continue
        pieces.append(compute_spot_history(chain).select("symbol", "date", "log_return"))
        if count % 100 == 0 or count == len(symbols):
            print(f"  {count}/{len(symbols)} symbols, {time.time() - started:.0f}s", flush=True)
    return pl.concat(pieces).drop_nulls("log_return").sort("symbol", "date")


def compute_features(returns_df: pl.DataFrame) -> pl.DataFrame:
    """Daily HAR components, forward realized variance and idiosyncratic vol."""
    market = returns_df.group_by("date").agg(pl.col("log_return").mean().alias("market_return"))
    frame = returns_df.join(market, on="date").sort("symbol", "date")
    squared = pl.col("log_return") ** 2
    by = "symbol"
    frame = frame.with_columns(
        (squared * 252).alias("rv_1"),
        (squared.rolling_mean(5).over(by) * 252).alias("rv_5"),
        (squared.rolling_mean(22).over(by) * 252).alias("rv_22"),
        (squared.reverse().rolling_mean(FORWARD_SESSIONS).reverse().shift(-1).over(by) * 252).alias("rv_forward"),
    )
    # One-factor residual vol over a rolling window, done in closed form so it
    # stays a set of rolling moments: var(e) = var(r) - cov(r, m)^2 / var(m).
    cov = pl.rolling_cov("log_return", "market_return", window_size=IDIO_WINDOW).over(by)
    var_r = pl.col("log_return").rolling_var(IDIO_WINDOW).over(by)
    var_m = pl.col("market_return").rolling_var(IDIO_WINDOW).over(by)
    frame = frame.with_columns(
        ((var_r - cov ** 2 / var_m).clip(lower_bound=0).sqrt() * (252 ** 0.5)).alias("idio_vol"),
        (var_r.sqrt() * (252 ** 0.5)).alias("total_vol_21"),
        (cov / var_m).alias("beta"),
    )
    return frame.select("symbol", "date", "rv_1", "rv_5", "rv_22", "rv_forward", "idio_vol", "total_vol_21", "beta")


def main() -> None:
    RESULTS_DIR.mkdir(exist_ok=True)
    panel_df = pl.read_parquet(PANEL_PATH)
    returns_df = load_returns(panel_df["symbol"].unique().sort().to_list())
    features_df = compute_features(returns_df)
    features_df.write_parquet(RESULTS_DIR / "daily_features.parquet")
    on_formation = panel_df.select("symbol", "month", "formation_day").join(
        features_df, left_on=["symbol", "formation_day"], right_on=["symbol", "date"], how="left")
    on_formation.write_parquet(FEATURES_PATH)
    print(f"wrote {FEATURES_PATH}: {on_formation.height} rows, "
          f"{on_formation['rv_22'].is_not_null().mean():.0%} with HAR inputs, "
          f"{on_formation['idio_vol'].is_not_null().mean():.0%} with idio vol")


if __name__ == "__main__":
    main()
