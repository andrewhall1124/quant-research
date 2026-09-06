"""Decile sorts on IV skew and term slope, 2017-2025.

Two claims from the literature, tested on S&P 500 names with the
Goyal-Saretto calendar and contracts:

* Xing, Zhang and Zhao (2010): stocks with steep put skew underperform over
  the following month. Outcome: the stock's return from entry to expiry.
* Vasquez (2017): straddles on stocks with steep (upward) IV term
  structures outperform. Outcome: the one-month ATM straddle held to expiry,
  its static-hedged legs, and the daily delta-hedged straddle.

Each sort is run on the full panel and on stock-months with no earnings
release inside the hold, since an inverted term structure on a formation
day is very often a report due before expiry.

Run from the project root, after `panel.py` here and the upstream panels:

    uv run python -m research.iv_skew_slope.analysis
"""

import matplotlib
import polars as pl

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from research.goyal_saretto.analysis import (  # noqa: E402
    DECILES, MONEYNESS_BAND, assign_deciles, compute_portfolio_returns, compute_returns, summarize,
)
from research.goyal_saretto.panel import PANEL_PATH  # noqa: E402
from research.iv_skew_slope.panel import FEATURES_PATH, RESULTS_DIR, STUDY_DIR  # noqa: E402
from research.variance_risk_premium.analysis import compute_daily_hedge  # noqa: E402
from research.variance_risk_premium.panel import PATHS_PATH  # noqa: E402

FIGURES_DIR = STUDY_DIR / "figures"
OUTCOMES = ("stock", "straddle", "call_hedged", "put_hedged", "daily_hedged")


def load_panel() -> pl.DataFrame:
    panel_df = pl.read_parquet(PANEL_PATH).filter((pl.col("moneyness") - 1).abs() <= MONEYNESS_BAND)
    features_df = pl.read_parquet(FEATURES_PATH)
    hedge_df = compute_daily_hedge(pl.read_parquet(PATHS_PATH))
    merged = panel_df.join(features_df, on=["symbol", "formation_day"], how="left").join(hedge_df, on=["symbol", "month"], how="left")
    premium = pl.col("call_mid") + pl.col("put_mid")
    return compute_returns(merged).with_columns((-pl.col("daily_hedge_gain") / premium).alias("daily_hedged"))


def score(name: str, signal_df: pl.DataFrame) -> tuple[dict, pl.DataFrame]:
    ranked = assign_deciles(signal_df)
    portfolio_df = compute_portfolio_returns(ranked, OUTCOMES)
    spread_df = portfolio_df.filter(pl.col("decile") == 0)
    row = {"sort": name, "months": spread_df.height, "names_per_month": float(ranked.group_by("month").len()["len"].mean())}
    for outcome in OUTCOMES:
        stats = summarize(spread_df[outcome])
        row |= {f"{outcome}_10_1": stats["mean"], f"{outcome}_t": stats["t_stat"]}
    # Costs on the straddle spread: buy decile 10, sell decile 1, half the quoted spread.
    long_df = compute_returns(ranked, 0.5, "long").filter(pl.col("decile") == DECILES)
    short_df = compute_returns(ranked, 0.5, "short").filter(pl.col("decile") == 1)
    costed = long_df.group_by("month").agg(pl.col("straddle").mean().alias("long")).join(
        short_df.group_by("month").agg(pl.col("straddle").mean().alias("short")), on="month")
    stats = summarize(costed["long"] - costed["short"])
    row |= {"straddle_10_1_cost50": stats["mean"], "straddle_cost50_t": stats["t_stat"]}
    characteristics = ranked.group_by("month", "decile").agg(
        pl.col("signal").mean(), pl.col("iv").mean(), pl.col("earnings_in_hold").cast(pl.Float64).mean(),
        *[pl.col(outcome).mean() for outcome in OUTCOMES],
    ).group_by("decile").agg(pl.all().exclude("month").mean()).sort("decile").with_columns(pl.lit(name).alias("sort"))
    return row, characteristics


def build_sorts(panel_df: pl.DataFrame) -> dict:
    no_earnings = panel_df.filter(~pl.col("earnings_in_hold"))
    return {
        "skew: OTM put IV - ATM call IV": panel_df.filter(pl.col("skew").is_not_null()).with_columns(pl.col("skew").alias("signal")),
        "skew, no earnings in hold": no_earnings.filter(pl.col("skew").is_not_null()).with_columns(pl.col("skew").alias("signal")),
        "slope: 90d ATM IV - 30d ATM IV": panel_df.filter(pl.col("slope").is_not_null()).with_columns(pl.col("slope").alias("signal")),
        "slope, no earnings in hold": no_earnings.filter(pl.col("slope").is_not_null()).with_columns(pl.col("slope").alias("signal")),
        "slope relative: (long - short) / short": panel_df.filter(pl.col("slope").is_not_null()).with_columns(
            (pl.col("slope") / pl.col("iv_short")).alias("signal")),
    }


def plot_deciles(characteristics_df: pl.DataFrame) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.5))
    for name in ("skew: OTM put IV - ATM call IV", "skew, no earnings in hold"):
        subset = characteristics_df.filter(pl.col("sort") == name)
        axes[0].plot(subset["decile"].to_list(), (subset["stock"] * 100).to_list(), marker="o", label=name)
    axes[0].axhline(0, color="black", linewidth=0.8)
    axes[0].set_title("Skew sort: next-month stock return")
    axes[0].set_xlabel("skew decile (10 = steepest put skew)")
    axes[0].set_ylabel("mean monthly return, %")
    axes[0].legend(fontsize=8)
    for name in ("slope: 90d ATM IV - 30d ATM IV", "slope, no earnings in hold"):
        subset = characteristics_df.filter(pl.col("sort") == name)
        axes[1].plot(subset["decile"].to_list(), (subset["straddle"] * 100).to_list(), marker="o", label=f"{name}, straddle")
        axes[1].plot(subset["decile"].to_list(), (subset["daily_hedged"] * 100).to_list(), marker="s", linestyle="--", label=f"{name}, daily hedged")
    axes[1].axhline(0, color="black", linewidth=0.8)
    axes[1].set_title("Slope sort: one-month straddle return")
    axes[1].set_xlabel("slope decile (10 = steepest upward term structure)")
    axes[1].set_ylabel("mean monthly return, %")
    axes[1].legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "deciles.png", dpi=150)
    plt.close(fig)


def main() -> None:
    FIGURES_DIR.mkdir(exist_ok=True)
    panel_df = load_panel()
    rows, frames = [], []
    for name, signal_df in build_sorts(panel_df).items():
        row, characteristics = score(name, signal_df)
        rows.append(row)
        frames.append(characteristics)
    summary_df = pl.DataFrame(rows)
    characteristics_df = pl.concat(frames)
    summary_df.write_csv(RESULTS_DIR / "sorts.csv")
    characteristics_df.write_csv(RESULTS_DIR / "deciles.csv")
    plot_deciles(characteristics_df)

    pl.Config.set_tbl_rows(60)
    pl.Config.set_float_precision(4)
    pl.Config.set_tbl_width_chars(220)
    print(summary_df.select("sort", "names_per_month", "stock_10_1", "stock_t", "straddle_10_1", "straddle_t",
                            "daily_hedged_10_1", "daily_hedged_t", "straddle_10_1_cost50", "straddle_cost50_t"))
    print(characteristics_df.select("sort", "decile", "signal", "iv", "earnings_in_hold", "stock", "straddle", "daily_hedged"))


if __name__ == "__main__":
    main()
