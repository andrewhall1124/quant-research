"""The SPX volatility horse race, re-run on 500 single names.

    uv run python -m research.single_name_vol.panel --refresh   # ~20 min, once
    uv run python -m research.single_name_vol.analysis          # seconds

Same four forecasts (trailing RV, ARCH(5), GARCH(1,1), ATM implied vol), same
two horizons, same four-layer scoring as `research/volatility/`: calibration by
Mincer-Zarnowitz, accuracy by RMSE and QLIKE, pairwise significance by
Diebold-Mariano, information by an encompassing regression.

Two things change when the sample becomes a cross-section:

* **Standard errors.** 500 names on the same 250 dates are nothing like
  125,000 independent observations — every name loads on the same market
  factor, so residuals are correlated across the panel on any given day. Plain
  Newey-West sees only the time dimension and is wildly overconfident here. Every
  pooled test below uses Driscoll-Kraay, and `compare_covariances` reports what
  the other two estimators would have claimed.
* **The cross-section is a result, not a nuisance.** Pooling answers "does IV
  win on average"; the per-name sweep answers "does it win *everywhere*", which
  is the question a single index can never ask.
"""

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
    driscoll_kraay_kwargs,
    hac_kwargs,
    mincer_zarnowitz,
    qlike_loss,
)
from research.single_name_vol.iv_validation import compare_measures, summarize
from research.single_name_vol.panel import HORIZONS, load_panel

HERE = Path(__file__).parent
FIGURES = HERE / "figures"
RESULTS = HERE / "results"

MODELS = ["RV", "ARCH", "GARCH", "IV"]
BENCHMARK = "MEAN"
TARGETS = {"target_rv": "forward realized vol", "target_iv": "forward implied vol"}
PALETTE = {
    "RV": "#4C72B0",
    "ARCH": "#DD8452",
    "GARCH": "#937860",
    "IV": "#C44E52",
    "MEAN": "#8C8C8C",
}
# The index study's numbers, for the comparisons the report keeps making.
SPX_QLIKE = {(5, "IV"): 0.468, (21, "IV"): 0.500}
SPX_PREMIUM_POINTS = {5: 3.64, 21: 3.31}


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


def add_benchmark(panel_df: pl.DataFrame) -> pl.DataFrame:
    """The level guess: each name's own expanding-mean trailing vol.

    Per symbol, so a low-vol utility is benchmarked against its own history
    rather than against the cross-sectional average — the harder test. The mean
    runs through `t` inclusive, which uses only information already known.
    """
    return panel_df.sort("symbol", "horizon", "date").with_columns(
        (
            pl.col("RV").cum_sum().over("symbol", "horizon")
            / pl.int_range(1, pl.len() + 1).over("symbol", "horizon")
        ).alias(BENCHMARK)
    )


def slice_for(panel_df: pl.DataFrame, horizon: int) -> pd.DataFrame:
    """One horizon as pandas, with the integer date index Driscoll-Kraay needs."""
    frame = panel_df.filter(pl.col("horizon") == horizon).sort("date", "symbol").to_pandas()
    frame["period"] = frame["date"].rank(method="dense").astype(int) - 1
    return frame


def covariances(frame: pd.DataFrame, horizon: int) -> dict:
    """The estimator every pooled test uses: HAC in time, summed by date."""
    return driscoll_kraay_kwargs(horizon, frame["period"].to_numpy())


def score(frame: pd.DataFrame, target: str, horizon: int) -> pd.DataFrame:
    """Both robust losses, the descriptive metrics and the MZ regression."""
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
                **mincer_zarnowitz(actual, forecast, covariances(frame, horizon)),
            }
        )
    scored_df = pd.DataFrame(rows)
    scored_df["rmse_vs_best"] = scored_df["rmse"] / scored_df["rmse"].min()
    scored_df["qlike_vs_best"] = scored_df["qlike"] / scored_df["qlike"].min()
    return scored_df


def compare_covariances(frame: pd.DataFrame, target: str, horizon: int) -> pd.DataFrame:
    """What each standard-error assumption claims about the same coefficients.

    The MZ slope test and the IV-vs-RV loss differential, priced three ways:
    plain OLS, Newey-West, and Driscoll-Kraay. The gap between the last two is
    the cost of ignoring that every name shares the market factor.
    """
    actual = frame[target].to_numpy()
    period = frame["period"].to_numpy()
    settings = {
        "OLS": {"cov_type": "nonrobust"},
        "Newey-West": hac_kwargs(horizon),
        "Driscoll-Kraay": driscoll_kraay_kwargs(horizon, period),
    }
    rows = []
    for name, covariance in settings.items():
        row = {"horizon": horizon, "target": target, "covariance": name}
        for model in ["IV", "RV"]:
            statistics = mincer_zarnowitz(actual, frame[model].to_numpy(), covariance)
            row[f"t_beta_eq_1_{model}"] = statistics["t_beta_eq_1"]
        row["t_dm_iv_vs_rv"] = diebold_mariano_pair(
            actual, frame["IV"].to_numpy(), frame["RV"].to_numpy(), "qlike", covariance
        )["t_stat"]
        rows.append(row)
    return pd.DataFrame(rows)


def diebold_mariano(frame: pd.DataFrame, target: str, horizon: int) -> pd.DataFrame:
    """Every pair, both losses, Driscoll-Kraay errors."""
    from itertools import combinations

    actual = frame[target].to_numpy()
    covariance = covariances(frame, horizon)
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
                        actual, frame[name_a].to_numpy(), frame[name_b].to_numpy(), loss, covariance
                    ),
                }
            )
    return pd.DataFrame(rows)


def encompassing(frame: pd.DataFrame, target: str, horizon: int) -> pd.DataFrame:
    """All four forecasts jointly: who carries information the others lack?"""
    design = sm.add_constant(frame[MODELS].to_numpy())
    fit = sm.OLS(frame[target].to_numpy(), design).fit(**covariances(frame, horizon))
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


def per_symbol_scores(frame: pd.DataFrame, target: str, horizon: int) -> pd.DataFrame:
    """The horse race run separately inside every name.

    Pooling says who wins on average; this says how often, which is the
    question the cross-section exists to answer.
    """
    rows = []
    for symbol, symbol_df in frame.groupby("symbol"):
        actual = symbol_df[target].to_numpy()
        row = {"horizon": horizon, "target": target, "symbol": symbol, "origins": len(actual)}
        for model in MODELS + [BENCHMARK]:
            forecast = symbol_df[model].to_numpy()
            row[f"qlike_{model}"] = float(np.mean(qlike_loss(actual, forecast)))
            row[f"rmse_{model}"] = float(np.sqrt(np.mean((forecast - actual) ** 2)))
        # One DM test per name, IV against trailing RV, on the asymmetric loss.
        row["t_dm_iv_vs_rv"] = diebold_mariano_pair(
            actual, symbol_df["IV"].to_numpy(), symbol_df["RV"].to_numpy(),
            "qlike", hac_kwargs(horizon),
        )["t_stat"]
        row["premium_points"] = float(np.mean(symbol_df["IV"] - symbol_df["target_rv"]) * 100)
        # In points a 3-point premium means something different on a 20-vol
        # name than on a 60-vol one, so carry the ratio as well.
        row["premium_ratio"] = float(np.mean(symbol_df["IV"]) / np.mean(symbol_df["target_rv"]))
        row["mean_iv"] = float(np.mean(symbol_df["IV"]) * 100)
        rows.append(row)
    scored_df = pd.DataFrame(rows)
    for metric in ("qlike", "rmse"):
        columns = [f"{metric}_{model}" for model in MODELS + [BENCHMARK]]
        scored_df[f"best_{metric}"] = (
            scored_df[columns].idxmin(axis=1).str.removeprefix(f"{metric}_")
        )
    return scored_df


def plot_universe(panel_df: pl.DataFrame) -> None:
    """What the cross-section looks like: 500 names' implied vol through 2025."""
    frame = panel_df.filter(pl.col("horizon") == 21).to_pandas()
    daily = frame.groupby("date")["IV"].describe(percentiles=[0.1, 0.5, 0.9])
    vix = dal.load_index_closes(["VIX"], frame["date"].min(), frame["date"].max()).to_pandas()

    figure, axes = plt.subplots(1, 2, figsize=(12, 4.3))
    axes[0].fill_between(daily.index, daily["10%"] * 100, daily["90%"] * 100,
                         color="#4C72B0", alpha=0.18, label="10th-90th percentile")
    axes[0].plot(daily.index, daily["50%"] * 100, color="#4C72B0", lw=1.6, label="median single name")
    axes[0].plot(vix["date"], vix["VIX"], color=PALETTE["IV"], lw=1.4, label="VIX")
    axes[0].set_ylabel("30-day ATM implied vol (%)")
    axes[0].set_title("Single-name implied vol against VIX, panel window", fontsize=10, loc="left")
    axes[0].legend(frameon=False, fontsize=9)

    per_name = frame.groupby("symbol")["IV"].mean() * 100
    sns.histplot(per_name, bins=45, color="#4C72B0", alpha=0.8, ax=axes[1])
    axes[1].axvline(vix["VIX"].mean(), color=PALETTE["IV"], lw=1.5, ls="--",
                    label=f"VIX mean {vix['VIX'].mean():.1f}")
    axes[1].set_xlabel("mean implied vol over the window (%)")
    axes[1].set_title(f"{len(per_name)} names, by average level", fontsize=10, loc="left")
    axes[1].legend(frameon=False, fontsize=9)
    figure.suptitle("The single-name cross-section", x=0.07, ha="left", weight="bold")
    figure.tight_layout()
    figure.savefig(FIGURES / "01_cross_section.png")
    plt.close(figure)


def plot_scoreboard(scores_df: pd.DataFrame) -> None:
    """Pooled accuracy and explanatory power, both targets, both horizons."""
    figure, axes = plt.subplots(2, 3, figsize=(15, 6.8))
    for row, (target, label) in enumerate(TARGETS.items()):
        subset_df = scores_df[scores_df["target"] == target]
        for column, (metric, title) in enumerate(
            [("rmse", "RMSE (vol points)"), ("qlike", "QLIKE loss"), ("r2", "Mincer-Zarnowitz R²")]
        ):
            axis = axes[row, column]
            plot_df = subset_df.copy()
            if metric == "rmse":
                plot_df[metric] = plot_df[metric] * 100
            sns.barplot(data=plot_df, x="model", y=metric, hue="horizon",
                        palette=["#8FA9C9", "#3B6394"], ax=axis)
            axis.set_title(f"{title} — {label}", fontsize=10, loc="left")
            axis.set_xlabel("")
            axis.set_ylabel("")
            axis.legend(title="horizon (d)", fontsize=8, title_fontsize=8, frameon=False)
    figure.suptitle(
        "Pooled scoreboard: lower RMSE and QLIKE are better (MEAN = per-name level guess)",
        x=0.06, ha="left", weight="bold",
    )
    figure.tight_layout()
    figure.savefig(FIGURES / "02_scoreboard.png")
    plt.close(figure)


def plot_covariance_comparison(comparison_df: pd.DataFrame) -> None:
    """The panel's own lesson: what each standard-error assumption claims."""
    plot_df = comparison_df[comparison_df["target"] == "target_rv"].melt(
        id_vars=["horizon", "covariance"],
        value_vars=["t_beta_eq_1_IV", "t_beta_eq_1_RV", "t_dm_iv_vs_rv"],
        var_name="test", value_name="t_stat",
    )
    names = {
        "t_beta_eq_1_IV": "MZ t(β=1), IV",
        "t_beta_eq_1_RV": "MZ t(β=1), RV",
        "t_dm_iv_vs_rv": "DM t, IV vs RV (QLIKE)",
    }
    plot_df["test"] = plot_df["test"].map(names)

    # Every one of these t-statistics is negative and they span two orders of
    # magnitude, so plot the magnitude on a log axis: the bar heights are then
    # the overstatement itself.
    plot_df["abs_t"] = plot_df["t_stat"].abs()
    figure, axes = plt.subplots(1, len(HORIZONS), figsize=(11.5, 4.2), sharey=True)
    for axis, horizon in zip(axes, HORIZONS):
        sns.barplot(
            data=plot_df[plot_df["horizon"] == horizon], x="test", y="abs_t", hue="covariance",
            palette=["#C44E52", "#DD8452", "#4C72B0"], ax=axis,
        )
        axis.axhline(1.96, color="#2A2A2A", lw=1, ls="--")
        axis.text(0.01, 2.1, "5% bar", fontsize=8, color="#2A2A2A")
        axis.set_yscale("log")
        axis.set_title(f"h = {horizon} days", fontsize=10, loc="left")
        axis.set_xlabel("")
        axis.set_ylabel("|t-statistic|, log scale")
        axis.tick_params(axis="x", labelsize=8)
        axis.legend(frameon=False, fontsize=8, loc="upper left")
    figure.suptitle(
        "Same coefficients, three standard errors — forward realized vol, pooled",
        x=0.07, ha="left", weight="bold",
    )
    figure.tight_layout()
    figure.savefig(FIGURES / "03_standard_errors.png")
    plt.close(figure)


def plot_dm_heatmap(dm_df: pd.DataFrame, target: str, filename: str, label: str) -> None:
    """Pairwise DM t-statistics, blue where the row model loses less."""
    names = MODELS + [BENCHMARK]
    subset_df = dm_df[dm_df["target"] == target]
    figure, axes = plt.subplots(len(LOSSES), len(HORIZONS), figsize=(11.5, 8.6))
    for row, loss in enumerate(LOSSES):
        for column, horizon in enumerate(HORIZONS):
            cell_df = subset_df[(subset_df["loss"] == loss) & (subset_df["horizon"] == horizon)]
            matrix = pd.DataFrame(np.nan, index=names, columns=names)
            for entry in cell_df.itertuples():
                matrix.loc[entry.model_a, entry.model_b] = entry.t_stat
                matrix.loc[entry.model_b, entry.model_a] = -entry.t_stat
            axis = axes[row, column]
            sns.heatmap(matrix, annot=True, fmt=".2f", center=0, vmin=-6, vmax=6,
                        cmap="RdBu_r", linewidths=0.5, cbar=False,
                        annot_kws={"fontsize": 9}, ax=axis)
            axis.set_title(f"{loss.upper()}, h = {horizon}d", fontsize=10, loc="left")
            axis.set_ylabel("row model" if column == 0 else "")
    figure.suptitle(
        f"Diebold-Mariano t-statistics, {label} — Driscoll-Kraay, blue: row beats column",
        x=0.07, ha="left", weight="bold",
    )
    figure.tight_layout()
    figure.savefig(FIGURES / filename)
    plt.close(figure)


def plot_cross_section(symbol_scores_df: pd.DataFrame) -> None:
    """Does IV win everywhere, or only on average?"""
    figure, axes = plt.subplots(1, 3, figsize=(14.5, 4.4))

    realized_df = symbol_scores_df[symbol_scores_df["target"] == "target_rv"]
    share_df = (
        realized_df.groupby(["horizon", "best_qlike"]).size()
        .rename("names").reset_index()
    )
    sns.barplot(data=share_df, x="best_qlike", y="names", hue="horizon",
                palette=["#8FA9C9", "#3B6394"], ax=axes[0],
                order=[m for m in MODELS + [BENCHMARK] if m in set(share_df["best_qlike"])])
    axes[0].set_title("Lowest QLIKE, by name", fontsize=10, loc="left")
    axes[0].set_xlabel("")
    axes[0].set_ylabel("names")
    axes[0].legend(title="horizon (d)", frameon=False, fontsize=8, title_fontsize=8)

    for horizon, colour in zip(HORIZONS, ["#8FA9C9", "#3B6394"]):
        subset = realized_df[realized_df["horizon"] == horizon]
        sns.histplot(subset["t_dm_iv_vs_rv"], bins=40, color=colour, alpha=0.6,
                     label=f"h = {horizon}d", ax=axes[1])
    axes[1].axvline(-1.96, color="#2A2A2A", lw=1, ls="--")
    axes[1].axvline(0, color="#999999", lw=1)
    axes[1].set_xlabel("per-name DM t, IV vs RV (QLIKE)")
    axes[1].set_title("Negative = IV wins in that name", fontsize=10, loc="left")
    axes[1].legend(frameon=False, fontsize=9)

    premium_df = realized_df[realized_df["horizon"] == 21]
    sns.histplot(premium_df["premium_points"], bins=45, color=PALETTE["IV"], alpha=0.8, ax=axes[2])
    axes[2].axvline(0, color="#999999", lw=1)
    axes[2].axvline(premium_df["premium_points"].mean(), color="#2A2A2A", lw=1.5, ls="--",
                    label=f"mean {premium_df['premium_points'].mean():+.1f} pts")
    axes[2].axvline(SPX_PREMIUM_POINTS[21], color="#4C72B0", lw=1.5, ls=":",
                    label=f"SPX {SPX_PREMIUM_POINTS[21]:+.1f} pts")
    axes[2].set_xlabel("mean IV − subsequent realized vol (pts)")
    axes[2].set_title("Variance risk premium, per name (h=21)", fontsize=10, loc="left")
    axes[2].legend(frameon=False, fontsize=9)

    figure.suptitle("The cross-section: averages hide the spread", x=0.06, ha="left", weight="bold")
    figure.tight_layout()
    figure.savefig(FIGURES / "04_cross_section_results.png")
    plt.close(figure)


def plot_iv_validation(comparison_df: pl.DataFrame, summary_df: pl.DataFrame) -> None:
    """The study's inverted IV against the vendor's own, where both exist."""
    frame = comparison_df.to_pandas()
    summary = summary_df.to_pandas()

    figure, axes = plt.subplots(1, 3, figsize=(14.5, 4.3))
    limit = float(np.nanpercentile(frame[["study_iv", "vendor_iv"]].to_numpy().ravel(), 99.5) * 100)
    axes[0].hexbin(frame["vendor_iv"] * 100, frame["study_iv"] * 100, gridsize=45,
                   bins="log", cmap="Blues", extent=(0, limit, 0, limit))
    axes[0].plot([0, limit], [0, limit], color="#C44E52", lw=1, ls="--")
    pooled = frame["study_iv"].corr(frame["vendor_iv"])
    axes[0].set_xlabel("vendor 30-day ATM IV (%)")
    axes[0].set_ylabel("inverted 30-day ATM IV (%)")
    axes[0].set_title(f"{len(frame):,} name-days, corr {pooled:.3f}", fontsize=10, loc="left")

    sns.histplot(frame["difference_points"], bins=60, color="#4C72B0", alpha=0.8, ax=axes[1])
    axes[1].axvline(0, color="#999999", lw=1)
    axes[1].set_xlim(-2, 2)
    axes[1].set_xlabel("inverted − vendor (vol points)")
    axes[1].set_title(
        f"median |difference| {frame['difference_points'].abs().median():.2f} pts",
        fontsize=10, loc="left",
    )

    sns.histplot(summary["corr"], bins=30, color="#4C72B0", alpha=0.8, ax=axes[2])
    axes[2].set_xlabel("per-name correlation")
    axes[2].set_title(f"{len(summary)} names with vendor IV", fontsize=10, loc="left")

    figure.suptitle(
        "Is the inverted implied vol the same number the vendor computes?",
        x=0.06, ha="left", weight="bold",
    )
    figure.tight_layout()
    figure.savefig(FIGURES / "07_iv_validation.png")
    plt.close(figure)


def plot_scatter(frames: dict[int, pd.DataFrame], target: str, filename: str, label: str) -> None:
    """Forecast against outcome, pooled — 100k points, so hexbin not scatter."""
    figure, axes = plt.subplots(len(HORIZONS), len(MODELS), figsize=(13, 6.4))
    for row, horizon in enumerate(HORIZONS):
        frame = frames[horizon]
        actual = frame[target].to_numpy() * 100
        # One scale per row, so the panels are comparable at a glance.
        limit = float(
            np.nanpercentile(np.concatenate([frame[MODELS].to_numpy().ravel() * 100, actual]), 99)
        )
        for column, model in enumerate(MODELS):
            axis = axes[row, column]
            forecast = frame[model].to_numpy() * 100
            axis.hexbin(forecast, actual, gridsize=45, bins="log", cmap="Blues",
                        extent=(0, limit, 0, limit))
            axis.plot([0, limit], [0, limit], color="#C44E52", lw=1, ls="--")
            statistics = mincer_zarnowitz(actual, forecast, hac_kwargs(horizon))
            axis.set_title(f"{model}, h={horizon}\nR²={statistics['r2']:.2f}  β={statistics['beta']:.2f}",
                           fontsize=9.5)
            if column == 0:
                axis.set_ylabel(f"{label} (%)")
            if row == len(HORIZONS) - 1:
                axis.set_xlabel("forecast (%)")
    figure.suptitle(f"Pooled forecast vs {label}", x=0.07, ha="left", weight="bold")
    figure.tight_layout()
    figure.savefig(FIGURES / filename)
    plt.close(figure)


def main() -> None:
    setup_style()
    FIGURES.mkdir(exist_ok=True)
    RESULTS.mkdir(exist_ok=True)

    panel_df = add_benchmark(load_panel())
    print(
        f"panel: {panel_df.height} rows, {panel_df['symbol'].n_unique()} symbols, "
        f"{panel_df['date'].n_unique()} dates"
    )

    frames = {horizon: slice_for(panel_df, horizon) for horizon in HORIZONS}
    for horizon, frame in frames.items():
        print(f"  h={horizon}: {len(frame)} origins, {frame['symbol'].nunique()} symbols")

    combinations_list = [(horizon, target) for horizon in HORIZONS for target in TARGETS]
    scores_df = pd.concat(
        [score(frames[horizon], target, horizon) for horizon, target in combinations_list],
        ignore_index=True,
    )
    comparison_df = pd.concat(
        [compare_covariances(frames[horizon], target, horizon) for horizon, target in combinations_list],
        ignore_index=True,
    )
    dm_df = pd.concat(
        [diebold_mariano(frames[horizon], target, horizon) for horizon, target in combinations_list],
        ignore_index=True,
    )
    encompassing_df = pd.concat(
        [encompassing(frames[horizon], target, horizon) for horizon, target in combinations_list],
        ignore_index=True,
    )
    symbol_scores_df = pd.concat(
        [per_symbol_scores(frames[horizon], target, horizon) for horizon, target in combinations_list],
        ignore_index=True,
    )

    iv_comparison_df = compare_measures(panel_df)
    validation_df = summarize(iv_comparison_df)
    print(
        f"IV validation: {iv_comparison_df.height} name-days across "
        f"{validation_df.height} names with vendor greeks"
    )

    plot_universe(panel_df)
    if iv_comparison_df.height:
        plot_iv_validation(iv_comparison_df, validation_df)
    plot_scoreboard(scores_df)
    plot_covariance_comparison(comparison_df)
    plot_dm_heatmap(dm_df, "target_rv", "05a_dm_forward_realized.png", "forward realized vol")
    plot_dm_heatmap(dm_df, "target_iv", "05b_dm_forward_implied.png", "forward implied vol")
    plot_cross_section(symbol_scores_df)
    plot_scatter(frames, "target_rv", "06a_scatter_forward_realized.png", "forward realized vol")
    plot_scatter(frames, "target_iv", "06b_scatter_forward_implied.png", "forward implied vol")

    scores_df.to_csv(RESULTS / "scores.csv", index=False)
    comparison_df.to_csv(RESULTS / "standard_errors.csv", index=False)
    dm_df.to_csv(RESULTS / "diebold_mariano.csv", index=False)
    encompassing_df.to_csv(RESULTS / "encompassing.csv", index=False)
    symbol_scores_df.to_csv(RESULTS / "per_symbol.csv", index=False)
    validation_df.write_csv(RESULTS / "iv_validation.csv")

    pd.set_option("display.width", 200, "display.float_format", "{:.3f}".format)
    print("\n=== pooled scores ===")
    print(scores_df.drop(columns=["mz_joint_f", "rmse_vs_best", "qlike_vs_best"]).to_string(index=False))
    print("\n=== what each standard error claims ===")
    print(comparison_df.to_string(index=False))
    print("\n=== Diebold-Mariano, Driscoll-Kraay ===")
    print(dm_df.to_string(index=False))
    print("\n=== encompassing ===")
    print(encompassing_df.to_string(index=False))
    print("\n=== cross-section: who wins each name (QLIKE) ===")
    realized_df = symbol_scores_df[symbol_scores_df["target"] == "target_rv"]
    print(realized_df.groupby(["horizon", "best_qlike"]).size().rename("names").reset_index().to_string(index=False))
    print("\n=== per-name DM (IV vs RV, QLIKE) and premium ===")
    print(
        realized_df.groupby("horizon")[["t_dm_iv_vs_rv", "premium_points", "premium_ratio", "mean_iv"]]
        .describe(percentiles=[0.1, 0.5, 0.9]).to_string()
    )
    if iv_comparison_df.height:
        print("\n=== inverted IV vs vendor IV ===")
        print(
            f"pooled corr {iv_comparison_df.select(pl.corr('study_iv', 'vendor_iv')).item():.4f}, "
            f"mean difference {iv_comparison_df['difference_points'].mean():+.3f} pts, "
            f"median |difference| {iv_comparison_df['difference_points'].abs().median():.3f} pts, "
            f"worst name corr {validation_df['corr'].min():.3f}"
        )
    print(f"\nfigures -> {FIGURES}")


if __name__ == "__main__":
    main()
