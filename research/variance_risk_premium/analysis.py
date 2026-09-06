"""The variance risk premium on S&P 500 single names, 2017-2025.

No signal. Every month, every eligible name, one ATM straddle sold at the
midpoint the session after the monthly expiry and carried to the next one.
Four ways of carrying it, from the crude to the textbook:

* naked: sell the straddle, settle at intrinsic value;
* static hedge: sell the straddle and buy its net delta in stock once, at
  entry (the Goyal-Saretto construction);
* daily hedge: rebalance the stock hedge to the straddle's net delta at every
  close, so the P&L is close to a pure variance swap;
* log contract: implied variance paid minus realized variance delivered, in
  variance points, which is the premium itself with no option accounting.

Reads the Goyal-Saretto stock-month panel plus the daily paths that
`panel.py` in this directory adds. Run from the project root:

    uv run python -m research.variance_risk_premium.analysis
"""

import math

import matplotlib
import polars as pl

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import data_access_layer as dal  # noqa: E402
from research.goyal_saretto.analysis import MONEYNESS_BAND, summarize  # noqa: E402
from research.goyal_saretto.panel import PANEL_PATH  # noqa: E402
from research.variance_risk_premium.panel import INDEX_PANEL_PATH, PATHS_PATH, RESULTS_DIR, STUDY_DIR  # noqa: E402

FIGURES_DIR = STUDY_DIR / "figures"
SPREAD_FRACTIONS = (0.25, 0.5, 1.0)
CARRIES = ("naked", "static_hedge", "daily_hedge")


def load_stock_panel() -> pl.DataFrame:
    """Baseline stock-months with realized variance, minus the symbol-years
    whose returns carry an unadjusted spinoff."""
    panel_df = pl.read_parquet(PANEL_PATH).filter((pl.col("moneyness") - 1).abs() <= MONEYNESS_BAND)
    realized_df = pl.read_parquet(RESULTS_DIR / "realized.parquet")
    tainted = dal.corporate_action_symbol_years().select("symbol", "year")
    return (
        panel_df.join(realized_df, on=["symbol", "month"], how="inner")
        .join(tainted, on=["symbol", "year"], how="anti")
    )


def compute_daily_hedge(paths_df: pl.DataFrame) -> pl.DataFrame:
    """P&L of a short straddle hedged to its net delta at every close.

    Each session the position is short one straddle and long yesterday's
    net delta in stock. The day's P&L is the stock leg's gain minus the
    straddle's change in value. Sessions where a leg has no ask are skipped
    and the hedge is carried through them. The expiry-day quote is replaced
    by intrinsic value: closing quotes on an expiring contract are unreliable
    and the position settles at the payoff regardless.
    """
    ordered = (
        paths_df.with_columns(
            pl.when(pl.col("date") == pl.col("expiration"))
            .then((pl.col("spot") - pl.col("strike")).abs())
            .otherwise(pl.col("straddle_mid")).alias("straddle_mid")
        )
        .filter(pl.col("straddle_mid").is_not_null())
        .sort("symbol", "month", "date")
    )
    by = ["symbol", "month"]
    daily = ordered.with_columns(
        (pl.col("straddle_mid").shift(1).over(by) - pl.col("straddle_mid")).alias("straddle_gain"),
        (pl.col("net_delta").shift(1).over(by) * (pl.col("spot") - pl.col("spot").shift(1).over(by))).alias("hedge_gain"),
    ).drop_nulls(["straddle_gain", "hedge_gain"])
    return daily.group_by(by).agg(
        pl.col("straddle_gain").sum().alias("straddle_gain"),
        pl.col("hedge_gain").sum().alias("hedge_gain"),
        pl.len().alias("hedged_sessions"),
        pl.col("straddle_mid").first().alias("first_mark"),
    ).with_columns((pl.col("straddle_gain") + pl.col("hedge_gain")).alias("daily_hedge_gain"))


def compute_returns(panel_df: pl.DataFrame, hedge_df: pl.DataFrame, spread_fraction: float = 0.0) -> pl.DataFrame:
    """Short-side returns per stock-month, scaled by the straddle premium
    received. `spread_fraction` of the half-spread is given up at entry."""
    call_price = pl.col("call_mid") - spread_fraction * (pl.col("call_ask") - pl.col("call_bid")) / 2
    put_price = pl.col("put_mid") - spread_fraction * (pl.col("put_ask") - pl.col("put_bid")) / 2
    premium = call_price + put_price
    payoff = pl.col("call_payoff") + pl.col("put_payoff")
    stock_move = pl.col("spot_at_expiry") - pl.col("spot_entry")
    net_delta = pl.col("call_delta") + pl.col("put_delta")
    cost_shift = (pl.col("call_mid") + pl.col("put_mid")) - premium
    return panel_df.join(hedge_df, on=["symbol", "month"], how="left").with_columns(
        ((premium - payoff) / premium).alias("naked"),
        ((premium - payoff + net_delta * stock_move) / premium).alias("static_hedge"),
        ((pl.col("daily_hedge_gain") - cost_shift) / premium).alias("daily_hedge"),
        (pl.col("iv") ** 2 - pl.col("realized_var")).alias("variance_premium"),
        (pl.col("iv") - pl.col("realized_var").sqrt()).alias("vol_premium"),
        (premium / pl.col("spot_entry")).alias("premium_over_spot"),
    )


def build_monthly(returns_df: pl.DataFrame) -> pl.DataFrame:
    return returns_df.group_by("month").agg(
        pl.len().alias("names"),
        *[pl.col(carry).mean() for carry in CARRIES],
        pl.col("variance_premium").mean(), pl.col("vol_premium").mean(),
        pl.col("iv").mean(), pl.col("realized_var").sqrt().mean().alias("realized_vol"),
        (pl.col("variance_premium") > 0).mean().alias("share_positive"),
    ).sort("month")


def build_summary(monthly_df: pl.DataFrame, label: str) -> pl.DataFrame:
    rows = []
    for column in (*CARRIES, "variance_premium", "vol_premium"):
        rows.append({"sample": label, "series": column} | summarize(monthly_df[column]))
    return pl.DataFrame(rows)


def build_cost_table(panel_df: pl.DataFrame, hedge_df: pl.DataFrame) -> pl.DataFrame:
    rows = []
    for fraction in (0.0, *SPREAD_FRACTIONS):
        monthly = build_monthly(compute_returns(panel_df, hedge_df, fraction))
        for carry in CARRIES:
            rows.append({"effective_spread": fraction, "carry": carry} | summarize(monthly[carry]))
    return pl.DataFrame(rows)


def build_annual(monthly_df: pl.DataFrame) -> pl.DataFrame:
    return monthly_df.group_by(pl.col("month").dt.year().alias("year")).agg(
        pl.len().alias("months"), *[pl.col(carry).mean() for carry in CARRIES],
        pl.col("variance_premium").mean(), pl.col("iv").mean(), pl.col("realized_vol").mean(),
    ).sort("year")


def build_tails(monthly_df: pl.DataFrame) -> pl.DataFrame:
    worst = monthly_df.sort("daily_hedge").head(8).with_columns(pl.lit("worst").alias("tail"))
    best = monthly_df.sort("daily_hedge", descending=True).head(5).with_columns(pl.lit("best").alias("tail"))
    return pl.concat([worst, best]).select("tail", "month", "names", *CARRIES, "iv", "realized_vol")


def build_conditional(returns_df: pl.DataFrame) -> pl.DataFrame:
    """The premium split by what was known at entry: earnings inside the
    hold, IV level tercile, and the IV/HV ratio the paper sorted on."""
    rows = []
    groups = {
        "earnings in hold": pl.col("earnings_in_hold"),
        "no earnings": ~pl.col("earnings_in_hold"),
    }
    iv_tercile = (pl.col("iv").rank().over("month") * 3 / pl.len().over("month")).ceil().clip(1, 3)
    ratio_tercile = (pl.col("signal").rank().over("month") * 3 / pl.len().over("month")).ceil().clip(1, 3)
    labelled = returns_df.with_columns(iv_tercile.alias("iv_tercile"), ratio_tercile.alias("ratio_tercile"))
    for tercile in (1, 2, 3):
        groups[f"iv tercile {tercile}"] = pl.col("iv_tercile") == tercile
        groups[f"hv/iv tercile {tercile}"] = pl.col("ratio_tercile") == tercile
    for name, condition in groups.items():
        monthly = build_monthly(labelled.filter(condition))
        for carry in ("naked", "daily_hedge", "variance_premium"):
            rows.append({"group": name, "series": carry, "names_per_month": float(monthly["names"].mean())}
                        | summarize(monthly[carry]))
    return pl.DataFrame(rows)


def plot_premium(monthly_df: pl.DataFrame, spx_df: pl.DataFrame) -> None:
    fig, axes = plt.subplots(3, 1, figsize=(11, 10), sharex=True)
    months = monthly_df["month"].to_list()
    axes[0].plot(months, (monthly_df["iv"] * 100).to_list(), label="mean ATM IV at entry", color="#dd8452")
    axes[0].plot(months, (monthly_df["realized_vol"] * 100).to_list(), label="mean realized vol over hold", color="#4c72b0")
    axes[0].set_ylabel("annualized, %")
    axes[0].set_title("Single names: implied at entry versus realized over the hold")
    axes[0].legend()
    axes[1].bar(months, (monthly_df["daily_hedge"] * 100).to_list(), width=20, color="#4c72b0")
    axes[1].axhline(0, color="black", linewidth=0.8)
    axes[1].set_ylabel("% of premium")
    axes[1].set_title("Short straddle, daily delta-hedged, equal-weighted across names")
    for column, label, color in (("naked", "naked", "#c44e52"), ("static_hedge", "static hedge", "#dd8452"),
                                 ("daily_hedge", "daily hedge", "#4c72b0")):
        axes[2].plot(months, (monthly_df[column].cum_sum() * 100).to_list(), label=f"stocks, {label}", color=color)
    spx_months = spx_df["month"].to_list()
    axes[2].plot(spx_months, (spx_df["daily_hedge"].cum_sum() * 100).to_list(), label="SPX, daily hedge", color="#55a868", linestyle="--")
    axes[2].axhline(0, color="black", linewidth=0.8)
    axes[2].set_ylabel("cumulative, % of premium")
    axes[2].set_title("Cumulative short-straddle return, constant premium each month")
    axes[2].legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "premium.png", dpi=150)
    plt.close(fig)


def plot_scatter(returns_df: pl.DataFrame) -> None:
    sample = returns_df.filter(pl.col("realized_var") < 4).sample(min(8000, returns_df.height), seed=1)
    fig, axis = plt.subplots(figsize=(6, 6))
    axis.scatter((sample["iv"] * 100).to_list(), (sample["realized_var"].sqrt() * 100).to_list(), s=3, alpha=0.3, color="#4c72b0")
    limit = 150
    axis.plot([0, limit], [0, limit], color="black", linewidth=0.8)
    axis.set_xlim(0, limit)
    axis.set_ylim(0, limit)
    axis.set_xlabel("ATM IV at entry, %")
    axis.set_ylabel("realized vol over the hold, %")
    axis.set_title("Stock-months: points below the line paid the premium")
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "implied_vs_realized.png", dpi=150)
    plt.close(fig)


def main() -> None:
    FIGURES_DIR.mkdir(exist_ok=True)
    panel_df = load_stock_panel()
    hedge_df = compute_daily_hedge(pl.read_parquet(PATHS_PATH))
    returns_df = compute_returns(panel_df, hedge_df)
    monthly_df = build_monthly(returns_df)

    spx_panel = pl.read_parquet(INDEX_PANEL_PATH).with_columns(pl.lit(False).alias("earnings_in_hold"), pl.lit(0.0).alias("signal"))
    spx_hedge = compute_daily_hedge(pl.read_parquet(RESULTS_DIR / "spx_paths.parquet"))
    spx_monthly = build_monthly(compute_returns(spx_panel, spx_hedge))

    returns_df.select(
        "symbol", "month", "entry_day", "expiration", "strike", "spot_entry", "iv", "realized_var",
        "premium_over_spot", *CARRIES, "variance_premium", "earnings_in_hold", "hedged_sessions",
    ).write_csv(RESULTS_DIR / "stock_months.csv")
    monthly_df.write_csv(RESULTS_DIR / "monthly.csv")
    spx_monthly.write_csv(RESULTS_DIR / "spx_monthly.csv")
    summary_df = pl.concat([build_summary(monthly_df, "S&P 500 names"), build_summary(spx_monthly, "SPX index")])
    summary_df.write_csv(RESULTS_DIR / "summary.csv")
    build_cost_table(panel_df, hedge_df).write_csv(RESULTS_DIR / "costs.csv")
    build_annual(monthly_df).write_csv(RESULTS_DIR / "annual.csv")
    build_tails(monthly_df).write_csv(RESULTS_DIR / "tails.csv")
    build_conditional(returns_df).write_csv(RESULTS_DIR / "conditional.csv")
    plot_premium(monthly_df, spx_monthly)
    plot_scatter(returns_df)

    pl.Config.set_tbl_rows(40)
    pl.Config.set_float_precision(4)
    pl.Config.set_tbl_width_chars(200)
    print(f"{returns_df.height} stock-months, {monthly_df.height} months, "
          f"{monthly_df['names'].mean():.0f} names per month; SPX {spx_monthly.height} months")
    print(summary_df.select("sample", "series", "mean", "std", "t_stat", "sharpe", "min", "max"))
    print(f"share of stock-months with IV^2 > RV: {(returns_df['variance_premium'] > 0).mean():.1%}")


if __name__ == "__main__":
    main()
