"""Timing the short index straddle with the volatility term structure.

The classic VIX carry rule: sell volatility when the curve is in contango
(three-month above one-month) and stand aside, or buy, when it inverts. The
curve here is the ATM implied-vol slope from the XSP chain, checked against
VIX3M minus VIX where both exist.

Every month one ATM XSP straddle is sold on the Goyal-Saretto calendar and
held to expiry, naked and daily delta-hedged. Rules are evaluated on the
slope at the close of the formation day, the session before entry:

* always short (the unconditional premium);
* short only in contango, flat otherwise;
* short in contango, long in backwardation;
* short only when the slope is above its trailing-year mean (z > 0);
* short only when one-month IV is above its trailing-year mean (the level
  rule everyone compares against).

Run from the project root, after `panel.py`:

    uv run python -m research.vix_term_structure.analysis
"""

import matplotlib
import polars as pl

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from research.goyal_saretto.analysis import summarize  # noqa: E402
from research.variance_risk_premium.analysis import compute_daily_hedge  # noqa: E402
from research.vix_term_structure.panel import PANEL_PATH, PATHS_PATH, RESULTS_DIR, SLOPE_PATH, STUDY_DIR  # noqa: E402

FIGURES_DIR = STUDY_DIR / "figures"
SPREAD_FRACTIONS = (0.25, 0.5, 1.0)


def load_months() -> pl.DataFrame:
    panel_df = pl.read_parquet(PANEL_PATH)
    slope_df = pl.read_parquet(SLOPE_PATH).select(
        pl.col("date").alias("formation_day"), "iv_30", "iv_90", "slope", "slope_z", "iv_30_mean_1y", "VIX", "VIX3M")
    hedge_df = compute_daily_hedge(pl.read_parquet(PATHS_PATH))
    premium = pl.col("call_mid") + pl.col("put_mid")
    payoff = pl.col("call_payoff") + pl.col("put_payoff")
    return (
        panel_df.join(slope_df, on="formation_day", how="left").join(hedge_df, on=["symbol", "month"], how="left")
        .with_columns(
            ((premium - payoff) / premium).alias("short_naked"),
            (pl.col("daily_hedge_gain") / premium).alias("short_hedged"),
            (pl.col("iv") ** 2 - pl.col("realized_var")).alias("variance_premium"),
            (premium / pl.col("spot_entry")).alias("premium_over_spot"),
            ((pl.col("call_ask") - pl.col("call_bid") + pl.col("put_ask") - pl.col("put_bid")) / premium).alias("relative_spread"),
        )
        .sort("month")
    )


def build_rules(months_df: pl.DataFrame) -> dict:
    contango = pl.col("slope") > 0
    return {
        "always short": pl.lit(-1.0),
        "short in contango, flat in backwardation": pl.when(contango).then(-1.0).otherwise(0.0),
        "short in contango, long in backwardation": pl.when(contango).then(-1.0).otherwise(1.0),
        "short when slope z > 0": pl.when(pl.col("slope_z") > 0).then(-1.0).otherwise(0.0),
        "short when slope z > 0, long when z < -1": pl.when(pl.col("slope_z") > 0).then(-1.0).when(pl.col("slope_z") < -1).then(1.0).otherwise(0.0),
        "short when 1m IV above its 1y mean": pl.when(pl.col("iv_30") > pl.col("iv_30_mean_1y")).then(-1.0).otherwise(0.0),
        "short when 1m IV below its 1y mean": pl.when(pl.col("iv_30") <= pl.col("iv_30_mean_1y")).then(-1.0).otherwise(0.0),
    }


def apply_rule(months_df: pl.DataFrame, position: pl.Expr, spread_fraction: float = 0.0) -> pl.DataFrame:
    """Return of the rule, positive when the position makes money. A
    non-zero position pays `spread_fraction` of the half-spread at entry."""
    cost = spread_fraction * pl.col("relative_spread") / 2
    return months_df.with_columns(position.alias("position")).with_columns(
        (-pl.col("position") * pl.col("short_naked") - pl.col("position").abs() * cost).alias("naked"),
        (-pl.col("position") * pl.col("short_hedged") - pl.col("position").abs() * cost).alias("hedged"),
    )


def build_summary(months_df: pl.DataFrame) -> pl.DataFrame:
    rows = []
    for name, position in build_rules(months_df).items():
        for fraction in (0.0, *SPREAD_FRACTIONS):
            applied = apply_rule(months_df, position, fraction)
            row = {"rule": name, "effective_spread": fraction, "months": applied.height,
                   "months_short": int((applied["position"] < 0).sum()), "months_long": int((applied["position"] > 0).sum())}
            for column in ("naked", "hedged"):
                stats = summarize(applied[column])
                cumulative = applied[column].cum_sum()
                drawdown = float((cumulative - cumulative.cum_max()).min())
                row |= {f"{column}_mean": stats["mean"], f"{column}_t": stats["t_stat"], f"{column}_sharpe": stats["sharpe"],
                        f"{column}_min": stats["min"], f"{column}_max_drawdown": drawdown}
            rows.append(row)
    return pl.DataFrame(rows)


def build_conditional(months_df: pl.DataFrame) -> pl.DataFrame:
    """What the short straddle did in each slope state, no rule applied."""
    rows = []
    states = {
        "backwardation (slope < 0)": pl.col("slope") < 0,
        "contango (slope > 0)": pl.col("slope") > 0,
        "slope tercile 1": pl.col("slope") <= pl.col("slope").quantile(1 / 3),
        "slope tercile 2": (pl.col("slope") > pl.col("slope").quantile(1 / 3)) & (pl.col("slope") <= pl.col("slope").quantile(2 / 3)),
        "slope tercile 3": pl.col("slope") > pl.col("slope").quantile(2 / 3),
        "slope z < -1": pl.col("slope_z") < -1,
        "slope z > 1": pl.col("slope_z") > 1,
    }
    for name, condition in states.items():
        subset = months_df.filter(condition)
        row = {"state": name, "months": subset.height, "mean_slope": float(subset["slope"].mean()),
               "mean_iv_30": float(subset["iv_30"].mean())}
        for column in ("short_naked", "short_hedged", "variance_premium"):
            stats = summarize(subset[column])
            row |= {f"{column}_mean": stats["mean"], f"{column}_t": stats["t_stat"]}
        rows.append(row)
    return pl.DataFrame(rows)


def build_validation(slope_df: pl.DataFrame) -> pl.DataFrame:
    """The chain-derived slope against the VIX complex where both exist."""
    both = slope_df.filter(pl.col("VIX").is_not_null(), pl.col("VIX3M").is_not_null()).with_columns(
        ((pl.col("VIX3M") - pl.col("VIX")) / 100).alias("vix_slope"))
    return pl.DataFrame([{
        "sessions": both.height,
        "corr_slope_vs_vix3m_minus_vix": float(both.select(pl.corr("slope", "vix_slope")).item()),
        "corr_iv30_vs_vix": float(both.select(pl.corr("iv_30", pl.col("VIX") / 100)).item()),
        "mean_iv30_minus_vix": float(both.select((pl.col("iv_30") - pl.col("VIX") / 100).mean()).item()),
        "sign_agreement": float(both.select(((pl.col("slope") > 0) == (pl.col("vix_slope") > 0)).mean()).item()),
    }])


def plot(slope_df: pl.DataFrame, months_df: pl.DataFrame) -> None:
    fig, axes = plt.subplots(3, 1, figsize=(11, 10), sharex=True)
    dates = slope_df["date"].to_list()
    axes[0].plot(dates, (slope_df["iv_30"] * 100).to_list(), label="XSP ATM IV, ~30d", color="#dd8452", linewidth=0.8)
    axes[0].plot(dates, (slope_df["iv_90"] * 100).to_list(), label="XSP ATM IV, ~90d", color="#4c72b0", linewidth=0.8)
    vix = slope_df.filter(pl.col("VIX").is_not_null())
    axes[0].plot(vix["date"].to_list(), vix["VIX"].to_list(), label="VIX", color="black", linewidth=0.6, linestyle=":")
    axes[0].set_ylabel("annualized vol, %")
    axes[0].set_title("Index implied vol at two maturities")
    axes[0].legend(fontsize=8)
    axes[1].fill_between(dates, (slope_df["slope"] * 100).to_list(), 0, where=(slope_df["slope"] > 0).to_list(), color="#55a868", alpha=0.6, label="contango")
    axes[1].fill_between(dates, (slope_df["slope"] * 100).to_list(), 0, where=(slope_df["slope"] <= 0).to_list(), color="#c44e52", alpha=0.6, label="backwardation")
    axes[1].set_ylabel("90d minus 30d IV, points")
    axes[1].set_title("Term slope")
    axes[1].legend(fontsize=8)
    months = months_df["month"].to_list()
    for name, color in (("always short", "#4c72b0"), ("short in contango, flat in backwardation", "#55a868"),
                        ("short in contango, long in backwardation", "#c44e52"), ("short when 1m IV above its 1y mean", "#dd8452")):
        applied = apply_rule(months_df, build_rules(months_df)[name])
        axes[2].plot(months, (applied["hedged"].cum_sum() * 100).to_list(), label=name, color=color)
    axes[2].axhline(0, color="black", linewidth=0.8)
    axes[2].set_ylabel("cumulative, % of premium")
    axes[2].set_title("Monthly XSP straddle, daily delta-hedged, by rule")
    axes[2].legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "term_structure.png", dpi=150)
    plt.close(fig)


def main() -> None:
    FIGURES_DIR.mkdir(exist_ok=True)
    months_df = load_months()
    slope_df = pl.read_parquet(SLOPE_PATH)
    summary_df = build_summary(months_df)
    conditional_df = build_conditional(months_df)
    validation_df = build_validation(slope_df)
    months_df.select("month", "formation_day", "entry_day", "expiration", "strike", "spot_entry", "iv", "iv_30", "iv_90",
                     "slope", "slope_z", "realized_var", "premium_over_spot", "relative_spread",
                     "short_naked", "short_hedged", "variance_premium").write_csv(RESULTS_DIR / "months.csv")
    slope_df.write_csv(RESULTS_DIR / "slope_daily.csv")
    summary_df.write_csv(RESULTS_DIR / "rules.csv")
    conditional_df.write_csv(RESULTS_DIR / "conditional.csv")
    validation_df.write_csv(RESULTS_DIR / "validation.csv")
    plot(slope_df, months_df)

    pl.Config.set_tbl_rows(60)
    pl.Config.set_float_precision(4)
    pl.Config.set_tbl_width_chars(220)
    print(f"{months_df.height} months, {months_df['slope'].is_not_null().sum()} with a slope; "
          f"contango in {(months_df['slope'] > 0).mean():.0%}")
    print(validation_df)
    print(conditional_df)
    print(summary_df.filter(pl.col("effective_spread") == 0).select(
        "rule", "months_short", "months_long", "naked_mean", "naked_t", "hedged_mean", "hedged_t", "hedged_sharpe", "hedged_max_drawdown"))


if __name__ == "__main__":
    main()
