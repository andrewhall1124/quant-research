"""Index term-structure signal and the straddle it would time.

The VIX complex in the store starts in 2024, so the term slope is rebuilt
from the option chain itself: ATM implied vol on the expiry nearest 30 days
minus ATM implied vol on the expiry nearest 90 days, every session, from the
XSP chain. XSP is the mini-SPX contract, PM-settled on every expiry, and it
is the one index root whose chain is quoted through 2020-2021 (SPX and SPXW
have zero bids and asks for most of those two years in this store).

The traded position is the monthly XSP ATM straddle on the Goyal-Saretto
calendar: selected the session after the third Friday, entered the next
session at the midpoint, held to the next third Friday. Daily marks are
kept so it can be delta-hedged daily.

Run from the project root, after `research.goyal_saretto.panel`:

    uv run python -m research.vix_term_structure.panel
"""

from pathlib import Path

import polars as pl

import data_access_layer as dal
from research.goyal_saretto.panel import MAX_IV_ERROR, build_calendar, build_schedule
from research.variance_risk_premium.panel import build_spx_panel, extract_paths, extract_realized

STUDY_DIR = Path(__file__).resolve().parent
RESULTS_DIR = STUDY_DIR / "results"
SLOPE_PATH = RESULTS_DIR / "slope.parquet"
PANEL_PATH = RESULTS_DIR / "panel.parquet"
PATHS_PATH = RESULTS_DIR / "paths.parquet"

ROOT = "XSP"


def compute_daily_slope(root: str = ROOT) -> pl.DataFrame:
    """Daily ATM IV at ~30 and ~90 days, and the slope between them."""
    chain = dal.load_option_greeks(root, index=True, lazy=True)
    rows = (
        chain.select("date", "expiration", "strike", "right", "bid", "implied_vol", "iv_error", "underlying_price")
        .filter(pl.col("underlying_price") > 0, pl.col("bid") > 0, pl.col("implied_vol") > 0,
                pl.col("iv_error").abs() <= MAX_IV_ERROR)
        .with_columns(
            (pl.col("expiration") - pl.col("date")).dt.total_days().alias("dte"),
            (pl.col("strike") / pl.col("underlying_price") - 1).abs().alias("distance"),
        )
        .filter(pl.col("dte").is_between(20, 150), pl.col("distance") <= 0.03)
        .collect()
    )

    def atm_at(target: int, low: int, high: int, name: str) -> pl.DataFrame:
        chosen = (
            rows.filter(pl.col("dte").is_between(low, high))
            .with_columns((pl.col("dte") - target).abs().alias("gap"))
            .sort("date", "gap").group_by("date", maintain_order=True).agg(pl.col("expiration").first())
        )
        return (
            rows.join(chosen, on=["date", "expiration"], how="inner")
            .sort("date", "right", "distance")
            .group_by("date", "right", maintain_order=True).agg(pl.col("implied_vol").first(), pl.col("dte").first())
            .group_by("date").agg(pl.col("implied_vol").mean().alias(name), pl.col("dte").first().alias(f"{name}_dte"), pl.len().alias("legs"))
            .filter(pl.col("legs") == 2).drop("legs")
        )

    slope = (
        atm_at(30, 20, 45, "iv_30").join(atm_at(90, 60, 150, "iv_90"), on="date", how="inner")
        .with_columns((pl.col("iv_90") - pl.col("iv_30")).alias("slope"))
        .sort("date")
    )
    vix = dal.load_indices(lazy=True).filter(pl.col("symbol").is_in(["VIX", "VIX3M", "SPX"])).collect().pivot(
        on="symbol", index="date", values="close").rename({"SPX": "spx_close"})
    return slope.join(vix, on="date", how="left").with_columns(
        # Trailing-year z-score of the slope, so a signal can be "steep for this regime".
        ((pl.col("slope") - pl.col("slope").rolling_mean(252, min_samples=120))
         / pl.col("slope").rolling_std(252, min_samples=120)).alias("slope_z"),
        pl.col("iv_30").rolling_mean(252, min_samples=120).alias("iv_30_mean_1y"),
    )


def main() -> None:
    RESULTS_DIR.mkdir(exist_ok=True)
    slope_df = compute_daily_slope()
    slope_df.write_parquet(SLOPE_PATH)
    schedule_df = build_schedule(build_calendar(), 2017, 2025)
    panel_df = build_spx_panel(schedule_df, ROOT)
    contracts = panel_df.select("symbol", "month", "entry_day", "expiration", "strike")
    extract_paths(ROOT, contracts, index=True).write_parquet(PATHS_PATH)
    panel_df = panel_df.join(extract_realized(ROOT, contracts, index=True), on=["symbol", "month"])
    panel_df.write_parquet(PANEL_PATH)
    print(f"slope on {slope_df.height} sessions {slope_df['date'].min()} to {slope_df['date'].max()}; "
          f"{panel_df.height} of {schedule_df.height} straddle months")


if __name__ == "__main__":
    main()
