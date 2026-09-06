"""Cross-section of the single-name variance premium with a real forecast.

Goyal and Saretto compared IV with twelve months of trailing realized vol.
This replaces the trailing number with an out-of-sample forecast of the
next month's realized variance, a pooled log-HAR refit every formation month
on all data whose target window ended before that day, and sorts on:

* `har`: log(forecast vol) - log(IV), the paper's signal with a forecast in
  place of history. Decile 10 is where options look cheapest.
* `rv21`: log(21-day realized vol) - log(IV), the same with a short window.
* `idio`: Cao and Han's idiosyncratic volatility, negative so decile 10 is
  the low-idio-vol end where their delta-hedged returns were highest.
* `har_only`: the forecast alone, no IV, as a control.

Each sort is scored on the hold-to-expiry straddle and static-hedged returns
from the Goyal-Saretto machinery and on the daily-hedged short from the
variance-premium study, sign-flipped to a long. Run from the project root,
after `panel.py` here and the two upstream panels:

    uv run python -m research.vrp_cross_section.analysis
"""

import numpy as np
import polars as pl

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from research.goyal_saretto.analysis import (  # noqa: E402
    DECILES, MONEYNESS_BAND, assign_deciles, compute_portfolio_returns, compute_returns, summarize,
)
from research.goyal_saretto.panel import PANEL_PATH  # noqa: E402
from research.variance_risk_premium.analysis import compute_daily_hedge  # noqa: E402
from research.variance_risk_premium.panel import PATHS_PATH  # noqa: E402
from research.variance_risk_premium.panel import RESULTS_DIR as VRP_RESULTS  # noqa: E402
from research.vrp_cross_section.panel import FEATURES_PATH, RESULTS_DIR, STUDY_DIR  # noqa: E402

FIGURES_DIR = STUDY_DIR / "figures"
STRATEGIES = ("straddle", "call_hedged", "put_hedged", "daily_hedged")
MIN_FIT_ROWS = 2000


def fit_har_forecasts(daily_df: pl.DataFrame, formation_days: list) -> pl.DataFrame:
    """Pooled log-HAR, refit for each formation day on every row whose
    forward window closed before it. Returns the forecast on each
    formation day for every symbol, plus the coefficients used."""
    clean = daily_df.filter(
        pl.col("rv_1") > 0, pl.col("rv_5") > 0, pl.col("rv_22") > 0, pl.col("rv_forward") > 0,
    ).with_columns(
        pl.col("rv_1").log().alias("x1"), pl.col("rv_5").log().alias("x5"), pl.col("rv_22").log().alias("x22"),
        pl.col("rv_forward").log().alias("y"),
    ).sort("date")
    session_dates = clean.select("date").unique().sort("date")["date"].to_list()
    position = {day: index for index, day in enumerate(session_dates)}
    forecasts, coefficients = [], []
    for day in sorted(formation_days):
        cutoff_index = position.get(day, None)
        if cutoff_index is None or cutoff_index < 22:
            continue
        # The forward window of a row on date d ends d + 21 sessions; only rows
        # whose window has closed are known on the formation day.
        cutoff = session_dates[cutoff_index - 22]
        train = clean.filter(pl.col("date") <= cutoff)
        if train.height < MIN_FIT_ROWS:
            continue
        design = np.column_stack([np.ones(train.height), train["x1"].to_numpy(), train["x5"].to_numpy(), train["x22"].to_numpy()])
        beta, *_ = np.linalg.lstsq(design, train["y"].to_numpy(), rcond=None)
        today = clean.filter(pl.col("date") == day)
        if today.is_empty():
            continue
        today_design = np.column_stack([np.ones(today.height), today["x1"].to_numpy(), today["x5"].to_numpy(), today["x22"].to_numpy()])
        residual_var = float(np.var(train["y"].to_numpy() - design @ beta))
        forecasts.append(today.select("symbol", pl.col("date").alias("formation_day")).with_columns(
            # Lognormal correction so the forecast is of variance, not of its log.
            pl.Series("har_forecast_var", np.exp(today_design @ beta + residual_var / 2)),
            pl.lit(train.height).alias("fit_rows"),
        ))
        coefficients.append({"formation_day": day, "rows": train.height, "const": beta[0], "b_1": beta[1], "b_5": beta[2], "b_22": beta[3]})
    return pl.concat(forecasts), pl.DataFrame(coefficients)


def load_panel() -> tuple[pl.DataFrame, pl.DataFrame]:
    panel_df = pl.read_parquet(PANEL_PATH).filter((pl.col("moneyness") - 1).abs() <= MONEYNESS_BAND)
    features_df = pl.read_parquet(FEATURES_PATH).drop("formation_day")
    daily_df = pl.read_parquet(RESULTS_DIR / "daily_features.parquet")
    realized_df = pl.read_parquet(VRP_RESULTS / "realized.parquet")
    hedge_df = compute_daily_hedge(pl.read_parquet(PATHS_PATH))
    forecasts_df, coefficients_df = fit_har_forecasts(daily_df, panel_df["formation_day"].unique().to_list())
    coefficients_df.write_csv(RESULTS_DIR / "har_coefficients.csv")
    merged = (
        panel_df.join(features_df, on=["symbol", "month"], how="left")
        .join(forecasts_df, on=["symbol", "formation_day"], how="left")
        .join(realized_df, on=["symbol", "month"], how="left")
        .join(hedge_df, on=["symbol", "month"], how="left")
    )
    return merged, coefficients_df


def with_returns(panel_df: pl.DataFrame) -> pl.DataFrame:
    """Long-side returns: the three hold-to-expiry ones from the baseline,
    and the daily-hedged straddle scaled by premium."""
    premium = pl.col("call_mid") + pl.col("put_mid")
    return compute_returns(panel_df).with_columns((-pl.col("daily_hedge_gain") / premium).alias("daily_hedged"))


def build_signals(panel_df: pl.DataFrame) -> dict:
    log = lambda column: pl.col(column).log()  # noqa: E731
    return {
        "har: log(forecast vol / IV)": panel_df.filter(pl.col("har_forecast_var") > 0).with_columns(
            (0.5 * log("har_forecast_var") - log("iv")).alias("signal")),
        "rv21: log(21d realized vol / IV)": panel_df.filter(pl.col("total_vol_21") > 0).with_columns(
            (log("total_vol_21") - log("iv")).alias("signal")),
        "hv252: log(12m realized vol / IV), the paper": panel_df,
        "idio: -idiosyncratic vol (Cao-Han)": panel_df.filter(pl.col("idio_vol") > 0).with_columns((-pl.col("idio_vol")).alias("signal")),
        "har_only: -forecast vol": panel_df.filter(pl.col("har_forecast_var") > 0).with_columns((-pl.col("har_forecast_var")).alias("signal")),
    }


def score(name: str, signal_df: pl.DataFrame) -> tuple[dict, pl.DataFrame]:
    ranked = assign_deciles(with_returns(signal_df))
    portfolio_df = compute_portfolio_returns(ranked, STRATEGIES)
    row = {"signal": name, "months": portfolio_df.filter(pl.col("decile") == 0).height,
           "names_per_month": float(ranked.group_by("month").len()["len"].mean())}
    spread_df = portfolio_df.filter(pl.col("decile") == 0)
    for strategy in STRATEGIES:
        stats = summarize(spread_df[strategy])
        row |= {f"{strategy}_10_1": stats["mean"], f"{strategy}_t": stats["t_stat"], f"{strategy}_sharpe": stats["sharpe"]}
    # Does the signal actually forecast the premium? Correlation of the signal
    # with the realized minus implied variance it is supposed to predict.
    row["corr_signal_vs_rv_minus_iv2"] = float(ranked.select(
        pl.corr("signal", pl.col("realized_var") - pl.col("iv") ** 2, method="spearman")).item())
    deciles = portfolio_df.filter(pl.col("decile") > 0).group_by("decile").agg(
        *[pl.col(strategy).mean() for strategy in STRATEGIES]).sort("decile").with_columns(pl.lit(name).alias("signal"))
    return row, deciles


def build_forecast_quality(panel_df: pl.DataFrame) -> pl.DataFrame:
    """How well each candidate predicts realized variance over the hold,
    out of sample: rank correlation and mean squared log error."""
    rows = []
    candidates = {
        "har forecast": pl.col("har_forecast_var"),
        "21d realized var": pl.col("total_vol_21") ** 2,
        "12m realized var (hv)": pl.col("hv") ** 2,
        "implied var": pl.col("iv") ** 2,
    }
    target = pl.col("realized_var")
    clean = panel_df.filter(target > 0)
    for name, expr in candidates.items():
        subset = clean.filter(expr > 0)
        rows.append({
            "predictor": name,
            "rows": subset.height,
            "spearman_with_realized": float(subset.select(pl.corr(expr, target, method="spearman")).item()),
            "mean_log_error": float(subset.select((expr.log() - target.log()).mean()).item()),
            "rms_log_error": float(subset.select(((expr.log() - target.log()) ** 2).mean().sqrt()).item()),
        })
    return pl.DataFrame(rows)


def plot_deciles(deciles_df: pl.DataFrame) -> None:
    signals = deciles_df["signal"].unique(maintain_order=True).to_list()
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.5))
    for axis, strategy, title in zip(axes, ("straddle", "daily_hedged"), ("Straddle, hold to expiry", "Straddle, daily delta-hedged")):
        for name in signals:
            subset = deciles_df.filter(pl.col("signal") == name)
            axis.plot(subset["decile"].to_list(), (subset[strategy] * 100).to_list(), marker="o", label=name.split(":")[0])
        axis.axhline(0, color="black", linewidth=0.8)
        axis.set_xlabel("decile (10 = options look cheapest)")
        axis.set_ylabel("mean monthly long return, %")
        axis.set_title(title)
        axis.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "deciles.png", dpi=150)
    plt.close(fig)


def main() -> None:
    FIGURES_DIR.mkdir(exist_ok=True)
    panel_df, coefficients_df = load_panel()
    rows, decile_frames = [], []
    for name, signal_df in build_signals(panel_df).items():
        row, deciles = score(name, signal_df)
        rows.append(row)
        decile_frames.append(deciles)
    summary_df = pl.DataFrame(rows)
    deciles_df = pl.concat(decile_frames)
    summary_df.write_csv(RESULTS_DIR / "signals.csv")
    deciles_df.write_csv(RESULTS_DIR / "deciles.csv")
    quality_df = build_forecast_quality(panel_df)
    quality_df.write_csv(RESULTS_DIR / "forecast_quality.csv")
    plot_deciles(deciles_df)

    pl.Config.set_tbl_rows(40)
    pl.Config.set_float_precision(4)
    pl.Config.set_tbl_width_chars(220)
    print(coefficients_df.tail(1))
    print(quality_df)
    print(summary_df.select("signal", "names_per_month", "corr_signal_vs_rv_minus_iv2",
                            "straddle_10_1", "straddle_t", "call_hedged_10_1", "call_hedged_t",
                            "daily_hedged_10_1", "daily_hedged_t"))


if __name__ == "__main__":
    main()
