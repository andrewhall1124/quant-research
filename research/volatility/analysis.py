"""Do RV, ARCH, GARCH and IV forecast forward realized and forward implied vol?

Regenerates every figure and table in REPORT.md from `data_store/`:

    uv run python -m research.volatility.analysis

Design of the horse race, in one place so the report can stay short:

* Sample — SPX daily closes, 2024-01-02 .. 2025-12-31 (the free-tier index
  history floor). Rows where SPX is null are market holidays on which VIX still
  prints, and are dropped before differencing.
* Horizons — 5 and 21 trading days, each paired with the implied-vol index that
  actually spans it: VIX9D for a week, VIX for a month.
* Forecasts — all four models produce annualized decimal vol for `t+1..t+h`
  using information through `t` only. ARCH and GARCH are estimated on an
  expanding window with a 250-day burn-in, so every number here is
  out-of-sample.
* Targets — forward realized vol over `t+1..t+h`, and the implied-vol index
  `h` days ahead.
* Inference — horizons overlap, so every regression uses Newey-West standard
  errors with `h` lags. The point estimates are unbiased regardless; the
  t-statistics would be badly overstated without it.
"""

from datetime import date
from itertools import combinations
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import polars as pl
import seaborn as sns
import statsmodels.api as sm

import data_access_layer as dal
from tools.scoring import (
    LOSSES,
    diebold_mariano_pair,
    hac_kwargs,
    mincer_zarnowitz,
    qlike_loss,
)
from research.volatility.implied_vol import build_atm_iv_series
from tools.vol_models import (
    fit_and_forecast,
    forward_realized_vol,
    trailing_realized_vol,
)

HERE = Path(__file__).parent
FIGURES = HERE / "figures"
RESULTS = HERE / "results"

SAMPLE_START = date(2024, 1, 1)
SAMPLE_END = date(2025, 12, 31)
BURN_IN = 250
HORIZONS = {5: "VIX9D", 21: "VIX"}
MODELS = ["RV", "ARCH", "GARCH", "IV"]
# A level guess, carried through the loss and Diebold-Mariano tables only: the
# expanding-window mean of the relevant series, using information through `t`.
# A forecast that cannot beat this is not a forecast.
BENCHMARK = "MEAN"
BENCHMARK_COLUMN = {"target_rv": "mean_rv", "target_iv": "mean_iv"}
PALETTE = {
    "RV": "#4C72B0",
    "ARCH": "#DD8452",
    "GARCH": "#937860",
    "IV": "#C44E52",
    "Realized": "#2A2A2A",
}


def setup_style() -> None:
    sns.set_theme(style="whitegrid", context="notebook")
    plt.rcParams.update(
        {
            "figure.dpi": 130,
            "savefig.dpi": 130,
            "savefig.bbox": "tight",
            "axes.titleweight": "bold",
            "axes.grid": True,
            "grid.alpha": 0.3,
        }
    )


def load_panel() -> pl.DataFrame:
    """SPX closes and the VIX complex on real trading days, plus log returns."""
    levels_df = dal.load_index_closes(
        ["SPX", "VIX", "VIX9D", "VIX3M"], SAMPLE_START, SAMPLE_END
    )
    # VIX prints on market holidays where SPX does not; those are not trading days.
    return (
        levels_df.drop_nulls("SPX")
        .sort("date")
        .with_columns((pl.col("SPX").log().diff()).alias("ret"))
        .drop_nulls("ret")
    )


def build_forecasts(panel_df: pl.DataFrame, horizon: int, iv_symbol: str) -> pd.DataFrame:
    """One tidy frame per horizon: both targets and all four forecasts, aligned."""
    returns = panel_df["ret"].to_numpy()
    implied = panel_df[iv_symbol].to_numpy() / 100

    frame = pd.DataFrame(
        {
            "date": panel_df["date"].to_numpy(),
            "spx": panel_df["SPX"].to_numpy(),
            "target_rv": forward_realized_vol(returns, horizon),
            "target_iv": np.concatenate([implied[horizon:], np.full(horizon, np.nan)]),
            "RV": trailing_realized_vol(returns, horizon),
            "ARCH": fit_and_forecast(returns, horizon, "ARCH", BURN_IN),
            "GARCH": fit_and_forecast(returns, horizon, "GARCH", BURN_IN),
            "IV": implied,
        }
    )
    # Keep only origins where the models are out-of-sample and both targets exist.
    frame = frame.iloc[BURN_IN:].dropna().reset_index(drop=True)
    # The constant benchmark: everything known about the level so far, and
    # nothing about today. Expanding, so it stays honest out of sample.
    frame["mean_rv"] = frame["RV"].expanding().mean()
    frame["mean_iv"] = frame["IV"].expanding().mean()
    return frame


def attach_benchmark(frame: pd.DataFrame, target: str) -> pd.DataFrame:
    """Expose the target's level benchmark under the common name `MEAN`."""
    return frame.assign(**{BENCHMARK: frame[BENCHMARK_COLUMN[target]]})




def score(frame: pd.DataFrame, target: str, horizon: int) -> pd.DataFrame:
    """Both robust losses, the descriptive metrics and the MZ regression, per model."""
    frame = attach_benchmark(frame, target)
    actual = frame[target].to_numpy()
    rows = []
    for model in MODELS + [BENCHMARK]:
        forecast = frame[model].to_numpy()
        error = forecast - actual
        rows.append(
            {
                "horizon": horizon,
                "target": target,
                "model": model,
                "rmse": float(np.sqrt(np.mean(error**2))),
                "qlike": float(np.mean(qlike_loss(actual, forecast))),
                "mae": float(np.mean(np.abs(error))),
                "bias": float(np.mean(error)),
                "corr": float(np.corrcoef(forecast, actual)[0, 1]),
                **mincer_zarnowitz(actual, forecast, hac_kwargs(horizon)),
            }
        )
    scored_df = pd.DataFrame(rows)
    scored_df["rmse_vs_best"] = scored_df["rmse"] / scored_df["rmse"].min()
    scored_df["qlike_vs_best"] = scored_df["qlike"] / scored_df["qlike"].min()
    return scored_df


def diebold_mariano(frame: pd.DataFrame, target: str, horizon: int) -> pd.DataFrame:
    """Every pair of forecasts against every other, under both robust losses."""
    frame = attach_benchmark(frame, target)
    actual = frame[target].to_numpy()
    rows = []
    for name_a, name_b in combinations(MODELS + [BENCHMARK], 2):
        for loss in LOSSES:
            rows.append(
                {
                    "horizon": horizon,
                    "target": target,
                    "model_a": name_a,
                    "model_b": name_b,
                    **diebold_mariano_pair(
                        actual,
                        frame[name_a].to_numpy(),
                        frame[name_b].to_numpy(),
                        loss,
                        hac_kwargs(horizon),
                    ),
                }
            )
    return pd.DataFrame(rows)


def premium_stats(frames: dict[int, pd.DataFrame]) -> pd.DataFrame:
    """How often, and by how much, implied sits above subsequent realized."""
    rows = []
    for horizon, frame in frames.items():
        premium = (frame["IV"] - frame["target_rv"]) * 100
        rows.append(
            {
                "horizon": horizon,
                "mean_points": premium.mean(),
                "median_points": premium.median(),
                "share_positive": float((premium > 0).mean()),
                "min_points": premium.min(),
            }
        )
    return pd.DataFrame(rows)


def encompassing(frame: pd.DataFrame, target: str, horizon: int) -> pd.DataFrame:
    """Run every forecast against the target jointly: who survives the others?"""
    design = sm.add_constant(frame[MODELS].to_numpy())
    fit = sm.OLS(frame[target].to_numpy(), design).fit(
        cov_type="HAC", cov_kwds={"maxlags": horizon}
    )
    return pd.DataFrame(
        {
            "horizon": horizon,
            "target": target,
            "term": ["const"] + MODELS,
            "coef": fit.params,
            "t_stat": fit.tvalues,
            "r2": fit.rsquared,
        }
    )


def plot_landscape(panel_df: pl.DataFrame) -> None:
    """What the sample actually contains — the April 2025 shock dominates it."""
    returns = panel_df["ret"].to_numpy()
    plot_df = pd.DataFrame(
        {
            "date": panel_df["date"].to_numpy(),
            "SPX": panel_df["SPX"].to_numpy(),
            "Realized vol (21d, trailing)": trailing_realized_vol(returns, 21) * 100,
            "VIX": panel_df["VIX"].to_numpy(),
        }
    )

    figure, axes = plt.subplots(2, 1, figsize=(11, 6.5), sharex=True, height_ratios=[1, 1.3])
    axes[0].plot(plot_df["date"], plot_df["SPX"], color="#2A2A2A", lw=1.2)
    axes[0].set_ylabel("SPX")
    axes[0].set_title("SPX and volatility, 2024-2025", loc="left")

    axes[1].plot(
        plot_df["date"], plot_df["Realized vol (21d, trailing)"],
        color=PALETTE["Realized"], lw=1.3, label="Realized vol (21d, trailing)",
    )
    axes[1].plot(plot_df["date"], plot_df["VIX"], color=PALETTE["IV"], lw=1.3, label="VIX")
    axes[1].fill_between(
        plot_df["date"], plot_df["Realized vol (21d, trailing)"], plot_df["VIX"],
        where=plot_df["VIX"] >= plot_df["Realized vol (21d, trailing)"],
        color=PALETTE["IV"], alpha=0.12, label="Implied above realized",
    )
    axes[1].set_ylabel("annualized vol (%)")
    axes[1].legend(loc="upper left", frameon=False)
    figure.savefig(FIGURES / "01_volatility_landscape.png")
    plt.close(figure)


def plot_forecast_paths(frames: dict[int, pd.DataFrame]) -> None:
    """Every forecast against the thing it is trying to hit."""
    figure, axes = plt.subplots(len(frames), 1, figsize=(11, 7), sharex=True)
    for axis, (horizon, frame) in zip(axes, frames.items()):
        axis.plot(
            frame["date"], frame["target_rv"] * 100,
            color=PALETTE["Realized"], lw=2.0, label=f"realized vol, next {horizon}d",
        )
        for model in MODELS:
            axis.plot(
                frame["date"], frame[model] * 100,
                color=PALETTE[model], lw=1.1, alpha=0.85, label=model,
            )
        axis.set_ylabel("annualized vol (%)")
        axis.set_title(f"h = {horizon} trading days", loc="left", fontsize=11)
    axes[0].legend(loc="upper left", ncol=5, frameon=False, fontsize=9)
    figure.suptitle("Out-of-sample forecasts vs forward realized vol", x=0.125, ha="left", weight="bold")
    figure.savefig(FIGURES / "02_forecast_paths.png")
    plt.close(figure)


def plot_scatter(frames: dict[int, pd.DataFrame], target: str, filename: str, label: str) -> None:
    """Forecast on x, outcome on y, with the 45-degree line and the MZ fit."""
    horizons = list(frames)
    figure, axes = plt.subplots(
        len(horizons), len(MODELS), figsize=(13, 6.2), sharex="row", sharey="row"
    )
    for row, horizon in enumerate(horizons):
        frame = frames[horizon]
        actual = frame[target].to_numpy() * 100
        for column, model in enumerate(MODELS):
            axis = axes[row, column]
            forecast = frame[model].to_numpy() * 100
            axis.scatter(forecast, actual, s=9, alpha=0.45, color=PALETTE[model], edgecolor="none")

            limits = [min(forecast.min(), actual.min()), max(forecast.max(), actual.max())]
            axis.plot(limits, limits, color="#999999", lw=1, ls="--", label="45°")
            fit = np.polyfit(forecast, actual, 1)
            axis.plot(limits, np.polyval(fit, limits), color="#2A2A2A", lw=1.4, label="MZ fit")

            stats = mincer_zarnowitz(actual, forecast, hac_kwargs(horizon))
            axis.set_title(
                f"{model}, h={horizon}\nR²={stats['r2']:.2f}  β={stats['beta']:.2f}",
                fontsize=9.5,
            )
            if column == 0:
                axis.set_ylabel(f"{label} (%)")
            if row == len(horizons) - 1:
                axis.set_xlabel("forecast (%)")
    axes[0, 0].legend(fontsize=8, frameon=False, loc="upper left")
    figure.suptitle(f"Forecast vs {label}", x=0.09, ha="left", weight="bold")
    figure.tight_layout()
    figure.savefig(FIGURES / filename)
    plt.close(figure)


def plot_scoreboard(scores_df: pd.DataFrame) -> None:
    """The headline comparison: accuracy and explanatory power, both targets."""
    labels = {"target_rv": "forward realized vol", "target_iv": "forward implied vol"}
    figure, axes = plt.subplots(2, 3, figsize=(15, 6.8))
    for row, (target, label) in enumerate(labels.items()):
        subset_df = scores_df[scores_df["target"] == target]
        for column, (metric, title) in enumerate(
            [
                ("rmse", "RMSE (vol points)"),
                ("qlike", "QLIKE loss"),
                ("r2", "Mincer-Zarnowitz R²"),
            ]
        ):
            axis = axes[row, column]
            plot_df = subset_df.copy()
            if metric == "rmse":
                plot_df[metric] = plot_df[metric] * 100
            sns.barplot(
                data=plot_df, x="model", y=metric, hue="horizon",
                palette=["#8FA9C9", "#3B6394"], ax=axis,
            )
            axis.set_title(f"{title} — {label}", fontsize=10, loc="left")
            axis.set_xlabel("")
            axis.set_ylabel("")
            axis.legend(title="horizon (d)", fontsize=8, title_fontsize=8, frameon=False)
    figure.suptitle(
        "Scoreboard: lower RMSE and QLIKE are better, higher R² is better "
        "(MEAN = expanding-mean level guess)",
        x=0.06, ha="left", weight="bold",
    )
    figure.tight_layout()
    figure.savefig(FIGURES / "05_scoreboard.png")
    plt.close(figure)


def plot_dm_heatmap(dm_df: pd.DataFrame, target: str, filename: str, label: str) -> None:
    """Pairwise Diebold-Mariano t-statistics, one panel per loss and horizon.

    Cell `(row=A, col=B)` is the t-statistic on `L(A) - L(B)`: blue (negative)
    means the row model is more accurate, and |t| > 1.96 is the 5% bar.
    """
    names = MODELS + [BENCHMARK]
    subset_df = dm_df[dm_df["target"] == target]
    horizons = sorted(subset_df["horizon"].unique())

    figure, axes = plt.subplots(len(LOSSES), len(horizons), figsize=(11.5, 8.6))
    for row, loss in enumerate(LOSSES):
        for column, horizon in enumerate(horizons):
            cell_df = subset_df[
                (subset_df["loss"] == loss) & (subset_df["horizon"] == horizon)
            ]
            matrix = pd.DataFrame(np.nan, index=names, columns=names)
            for entry in cell_df.itertuples():
                matrix.loc[entry.model_a, entry.model_b] = entry.t_stat
                matrix.loc[entry.model_b, entry.model_a] = -entry.t_stat

            axis = axes[row, column]
            sns.heatmap(
                matrix, annot=True, fmt=".2f", center=0, vmin=-4, vmax=4,
                cmap="RdBu_r", linewidths=0.5, cbar=False,
                annot_kws={"fontsize": 9}, ax=axis,
            )
            axis.set_title(f"{loss.upper()}, h = {horizon}d", fontsize=10, loc="left")
            axis.set_ylabel("row model" if column == 0 else "")
    figure.suptitle(
        f"Diebold-Mariano t-statistics, {label} — blue: row beats column, |t|>1.96 to matter",
        x=0.07, ha="left", weight="bold",
    )
    figure.tight_layout()
    figure.savefig(FIGURES / filename)
    plt.close(figure)


def plot_risk_premium(frames: dict[int, pd.DataFrame]) -> None:
    """IV minus subsequent RV — the wedge that explains most of the IV result."""
    figure, axes = plt.subplots(1, 2, figsize=(11.5, 4.2))
    frame = frames[21]
    premium = (frame["IV"] - frame["target_rv"]) * 100

    axes[0].axhline(0, color="#999999", lw=1)
    axes[0].plot(frame["date"], premium, color=PALETTE["IV"], lw=1.2)
    axes[0].fill_between(frame["date"], 0, premium, color=PALETTE["IV"], alpha=0.15)
    axes[0].set_title("VIX minus subsequent 21d realized vol", fontsize=10, loc="left")
    axes[0].set_ylabel("vol points")

    sns.histplot(premium, bins=40, color=PALETTE["IV"], ax=axes[1], alpha=0.75)
    axes[1].axvline(0, color="#999999", lw=1)
    axes[1].axvline(premium.mean(), color="#2A2A2A", lw=1.5, ls="--",
                    label=f"mean {premium.mean():+.1f} pts")
    axes[1].set_title("distribution", fontsize=10, loc="left")
    axes[1].set_xlabel("vol points")
    axes[1].legend(frameon=False, fontsize=9)
    figure.suptitle("The variance risk premium", x=0.07, ha="left", weight="bold")
    figure.tight_layout()
    figure.savefig(FIGURES / "04_variance_risk_premium.png")
    plt.close(figure)


def plot_iv_validation(panel_df: pl.DataFrame) -> pd.DataFrame:
    """Check VIX against a 30-day ATM IV rebuilt from the SPXW chain."""
    atm_df = build_atm_iv_series(date(2025, 1, 1), date(2025, 12, 31))
    merged_df = (
        atm_df.join(panel_df.select("date", "VIX"), on="date", how="inner")
        .with_columns((pl.col("VIX") / 100).alias("vix"))
        .to_pandas()
    )

    figure, axes = plt.subplots(1, 2, figsize=(11.5, 4.2))
    axes[0].plot(merged_df["date"], merged_df["vix"] * 100, color=PALETTE["IV"], lw=1.3, label="VIX")
    axes[0].plot(merged_df["date"], merged_df["atm_iv"] * 100, color="#4C72B0", lw=1.3,
                 label="SPXW 30d ATM IV")
    axes[0].set_ylabel("annualized vol (%)")
    axes[0].set_title("VIX vs chain-implied ATM vol, 2025", fontsize=10, loc="left")
    axes[0].legend(frameon=False, fontsize=9)

    axes[1].scatter(merged_df["atm_iv"] * 100, merged_df["vix"] * 100, s=12,
                    alpha=0.5, color="#4C72B0", edgecolor="none")
    limits = [merged_df["atm_iv"].min() * 100, merged_df["vix"].max() * 100]
    axes[1].plot(limits, limits, color="#999999", ls="--", lw=1)
    correlation = merged_df["atm_iv"].corr(merged_df["vix"])
    spread = (merged_df["vix"] - merged_df["atm_iv"]).mean() * 100
    axes[1].set_title(
        f"corr {correlation:.3f}, VIX sits {spread:+.1f} pts above ATM", fontsize=10, loc="left"
    )
    axes[1].set_xlabel("SPXW 30d ATM IV (%)")
    axes[1].set_ylabel("VIX (%)")
    figure.suptitle("Sanity check on the implied-vol measure", x=0.07, ha="left", weight="bold")
    figure.tight_layout()
    figure.savefig(FIGURES / "06_implied_vol_validation.png")
    plt.close(figure)

    return pd.DataFrame(
        [{"days": len(merged_df), "corr": correlation, "mean_spread_points": spread}]
    )


def main() -> None:
    setup_style()
    FIGURES.mkdir(exist_ok=True)
    RESULTS.mkdir(exist_ok=True)

    panel_df = load_panel()
    print(f"panel: {panel_df.height} trading days, {panel_df['date'].min()} .. {panel_df['date'].max()}")

    frames = {}
    for horizon, iv_symbol in HORIZONS.items():
        print(f"fitting h={horizon} (implied vol: {iv_symbol}) ...")
        frames[horizon] = build_forecasts(panel_df, horizon, iv_symbol)
        print(f"  {len(frames[horizon])} out-of-sample origins")

    scores_df = pd.concat(
        [
            score(frame, target, horizon)
            for horizon, frame in frames.items()
            for target in ("target_rv", "target_iv")
        ],
        ignore_index=True,
    )
    encompassing_df = pd.concat(
        [
            encompassing(frame, target, horizon)
            for horizon, frame in frames.items()
            for target in ("target_rv", "target_iv")
        ],
        ignore_index=True,
    )

    dm_df = pd.concat(
        [
            diebold_mariano(frame, target, horizon)
            for horizon, frame in frames.items()
            for target in ("target_rv", "target_iv")
        ],
        ignore_index=True,
    )
    premium_df = premium_stats(frames)

    plot_landscape(panel_df)
    plot_forecast_paths(frames)
    plot_scatter(frames, "target_rv", "03a_scatter_forward_realized.png", "forward realized vol")
    plot_scatter(frames, "target_iv", "03b_scatter_forward_implied.png", "forward implied vol")
    plot_risk_premium(frames)
    plot_scoreboard(scores_df)
    plot_dm_heatmap(dm_df, "target_rv", "07a_dm_forward_realized.png", "forward realized vol")
    plot_dm_heatmap(dm_df, "target_iv", "07b_dm_forward_implied.png", "forward implied vol")
    validation_df = plot_iv_validation(panel_df)

    scores_df.to_csv(RESULTS / "scores.csv", index=False)
    dm_df.to_csv(RESULTS / "diebold_mariano.csv", index=False)
    premium_df.to_csv(RESULTS / "variance_risk_premium.csv", index=False)
    encompassing_df.to_csv(RESULTS / "encompassing.csv", index=False)
    validation_df.to_csv(RESULTS / "iv_validation.csv", index=False)

    pd.set_option("display.width", 160, "display.float_format", "{:.3f}".format)
    print("\n=== scores ===")
    print(scores_df.to_string(index=False))
    print("\n=== Diebold-Mariano: every pair, both losses ===")
    print(dm_df.to_string(index=False))
    print("\n=== variance risk premium ===")
    print(premium_df.to_string(index=False))
    print("\n=== encompassing regressions ===")
    print(encompassing_df.to_string(index=False))
    print("\n=== implied vol validation ===")
    print(validation_df.to_string(index=False))
    print(f"\nfigures -> {FIGURES}")


if __name__ == "__main__":
    main()
