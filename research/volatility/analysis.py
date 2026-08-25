"""Do RV, ARCH, GARCH and IV forecast forward realized and forward implied vol?

Regenerates every figure and table in REPORT.md from `data_store/`:

    uv run python -m research.volatility.analysis

Design of the horse race, in one place so the report can stay short:

* Sample — SPX daily closes, 2024-01-02 .. 2025-12-31 (the free-tier index
  history floor). Rows where SPX is null are market holidays on which VIX still
  prints, and are dropped before differencing.
* Horizons — 5 and 21 trading days, each paired with the implied-vol index that
  actually spans it: VIX9D for a week, VIX for a month.
* Forecasts — nine models, all producing annualized decimal vol for `t+1..t+h`
  from information through `t` only. ARCH/GARCH/GJR are estimated on an
  expanding window with a 250-day burn-in; HAR and IV-adj are expanding-window
  regressions that may only train on origins whose target had already been
  realized. Every number is out-of-sample.
* Targets — forward realized vol over `t+1..t+h`, the implied-vol index `h`
  days ahead, and (as a robustness check) forward vol measured with the
  Garman-Klass-Yang-Zhang range estimator instead of close-to-close.
* Inference — horizons overlap, so every regression uses Newey-West standard
  errors with `h` lags. The point estimates are unbiased regardless; the
  t-statistics would be badly overstated without it.
"""

from datetime import date
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import polars as pl
import seaborn as sns
import statsmodels.api as sm

import data_access_layer as dal
from research.volatility.implied_vol import build_atm_iv_series
from research.volatility.volatility_models import (
    ewma_vol,
    fit_and_forecast,
    fit_regression_forecast,
    forward_range_vol,
    forward_realized_vol,
    garman_klass_variance,
    garman_klass_yang_zhang_variance,
    har_features,
    parkinson_variance,
    rogers_satchell_variance,
    trailing_range_vol,
    trailing_realized_vol,
)

HERE = Path(__file__).parent
FIGURES = HERE / "figures"
RESULTS = HERE / "results"

SAMPLE_START = date(2024, 1, 1)
SAMPLE_END = date(2025, 12, 31)
BURN_IN = 250
HORIZONS = {5: "VIX9D", 21: "VIX"}

# Ordered slowest-moving to fastest-adapting, with the two fitted models last.
MODELS = ["RV", "RANGE", "EWMA", "ARCH", "GARCH", "GJR", "IV", "HAR", "IV-adj"]
UNFITTED = ["RV", "RANGE", "EWMA", "ARCH", "GARCH", "GJR", "IV"]
FITTED = ["HAR", "IV-adj"]

# Nine collinear forecasts in one regression estimate nothing. The encompassing
# test uses one representative per family: naive, conditional-variance, fitted
# return-based, and the option market.
ENCOMPASSING_MODELS = ["RV", "GJR", "HAR", "IV"]
PATH_MODELS = ["RV", "GJR", "HAR", "IV"]
SCATTER_MODELS = ["RV", "GJR", "HAR", "IV", "IV-adj"]

TARGETS = {
    "target_rv": "forward realized vol",
    "target_iv": "forward implied vol",
    "target_rv_range": "forward realized vol (range-based)",
}
HEADLINE_TARGETS = ["target_rv", "target_iv"]

PALETTE = {
    "RV": "#4C72B0",
    "RANGE": "#55A868",
    "EWMA": "#8172B3",
    "ARCH": "#DD8452",
    "GARCH": "#937860",
    "GJR": "#DA8BC3",
    "HAR": "#64B5CD",
    "IV": "#C44E52",
    "IV-adj": "#8C564B",
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
    """SPX OHLC and the VIX complex on real trading days, plus log returns."""
    levels_df = dal.load_index_closes(
        ["SPX", "VIX", "VIX9D", "VIX3M"], SAMPLE_START, SAMPLE_END
    )
    ohlc_df = dal.load_indices("SPX", SAMPLE_START, SAMPLE_END).select(
        "date", "open", "high", "low"
    )
    # VIX prints on market holidays where SPX does not; those are not trading days.
    return (
        levels_df.drop_nulls("SPX")
        .join(ohlc_df, on="date", how="inner")
        .sort("date")
        .with_columns(pl.col("SPX").log().diff().alias("ret"))
        .drop_nulls("ret")
    )


def daily_range_variances(panel_df: pl.DataFrame) -> dict[str, np.ndarray]:
    """Every range estimator's daily variance series, row-aligned to the panel.

    The panel has already dropped the first calendar day (no return on it), so
    these line up with `ret` directly. GKYZ needs the previous close, so its
    first row is NaN — harmless, it sits deep inside the burn-in.
    """
    open_, high, low, close = (
        panel_df[column].to_numpy() for column in ("open", "high", "low", "SPX")
    )
    return {
        "Parkinson": parkinson_variance(high, low),
        "Garman-Klass": garman_klass_variance(open_, high, low, close),
        "Rogers-Satchell": rogers_satchell_variance(open_, high, low, close),
        "GKYZ": garman_klass_yang_zhang_variance(open_, high, low, close),
    }


def build_base(panel_df: pl.DataFrame, horizon: int, iv_symbol: str) -> pd.DataFrame:
    """Targets and the seven unfitted forecasts, full length, NaN-padded."""
    returns = panel_df["ret"].to_numpy()
    implied = panel_df[iv_symbol].to_numpy() / 100
    range_variance = daily_range_variances(panel_df)["GKYZ"]

    return pd.DataFrame(
        {
            "date": panel_df["date"].to_numpy(),
            "target_rv": forward_realized_vol(returns, horizon),
            "target_iv": np.concatenate([implied[horizon:], np.full(horizon, np.nan)]),
            "target_rv_range": forward_range_vol(range_variance, horizon),
            "RV": trailing_realized_vol(returns, horizon),
            "RANGE": trailing_range_vol(range_variance, horizon),
            "EWMA": ewma_vol(returns),
            "ARCH": fit_and_forecast(returns, horizon, "ARCH", BURN_IN),
            "GARCH": fit_and_forecast(returns, horizon, "GARCH", BURN_IN),
            "GJR": fit_and_forecast(returns, horizon, "GJR", BURN_IN),
            "IV": implied,
        }
    )


def attach_fitted(
    base_df: pd.DataFrame, panel_df: pl.DataFrame, target: str, horizon: int
) -> pd.DataFrame:
    """Add the two target-fitted models, then trim to usable out-of-sample rows.

    HAR regresses the target on trailing daily/weekly/monthly RV; IV-adj
    regresses it on implied vol alone, which is the direct test of whether the
    variance risk premium is stable enough to subtract out of sample. Both are
    refit at every origin on data whose outcome was already known then.
    """
    returns = panel_df["ret"].to_numpy()
    outcome = base_df[target].to_numpy()

    frame = base_df.copy()
    frame["HAR"] = fit_regression_forecast(har_features(returns), outcome, horizon, BURN_IN)
    frame["IV-adj"] = fit_regression_forecast(
        base_df["IV"].to_numpy().reshape(-1, 1), outcome, horizon, BURN_IN
    )
    return frame.iloc[BURN_IN:].dropna(subset=[target] + MODELS).reset_index(drop=True)


def mincer_zarnowitz(target: np.ndarray, forecast: np.ndarray, lags: int) -> dict:
    """Regress target on forecast with Newey-West errors.

    A perfect forecast has intercept 0 and slope 1. The slope says whether the
    forecast is scaled right; `t(b=1)` says whether the miss is significant
    once overlapping horizons are accounted for.
    """
    design = sm.add_constant(forecast)
    fit = sm.OLS(target, design).fit(cov_type="HAC", cov_kwds={"maxlags": lags})
    slope = fit.params[1]
    return {
        "alpha": fit.params[0],
        "beta": slope,
        "t_beta_eq_1": (slope - 1) / fit.bse[1],
        "r2": fit.rsquared,
    }


def score(frame: pd.DataFrame, target: str, horizon: int) -> pd.DataFrame:
    """RMSE, MAE, bias, correlation and the MZ regression, per model."""
    actual = frame[target].to_numpy()
    rows = []
    for model in MODELS:
        forecast = frame[model].to_numpy()
        error = forecast - actual
        rows.append(
            {
                "horizon": horizon,
                "target": target,
                "model": model,
                "rmse": float(np.sqrt(np.mean(error**2))),
                "mae": float(np.mean(np.abs(error))),
                "bias": float(np.mean(error)),
                "corr": float(np.corrcoef(forecast, actual)[0, 1]),
                **mincer_zarnowitz(actual, forecast, horizon),
            }
        )
    scored_df = pd.DataFrame(rows)
    scored_df["rmse_vs_best"] = scored_df["rmse"] / scored_df["rmse"].min()
    return scored_df


def diebold_mariano(
    frame: pd.DataFrame, target: str, horizon: int, reference: str = "IV"
) -> pd.DataFrame:
    """Is the reference model's edge bigger than the noise? One test per rival.

    Regresses the squared-error difference (reference minus rival) on a
    constant with Newey-West errors. A t-statistic below -1.96 means the
    reference is significantly more accurate at that horizon and target.
    """
    actual = frame[target].to_numpy()
    reference_error = (frame[reference].to_numpy() - actual) ** 2
    rows = []
    for model in [name for name in MODELS if name != reference]:
        difference = reference_error - (frame[model].to_numpy() - actual) ** 2
        fit = sm.OLS(difference, np.ones(len(difference))).fit(
            cov_type="HAC", cov_kwds={"maxlags": horizon}
        )
        rows.append(
            {
                "horizon": horizon,
                "target": target,
                "reference": reference,
                "vs": model,
                "mean_sq_error_diff": float(fit.params[0]),
                "t_stat": float(fit.tvalues[0]),
                "reference_better": bool(fit.params[0] < 0 and fit.tvalues[0] < -1.96),
            }
        )
    return pd.DataFrame(rows)


def premium_stats(frames: dict[tuple[int, str], pd.DataFrame]) -> pd.DataFrame:
    """How often, and by how much, implied sits above subsequent realized."""
    rows = []
    for horizon in HORIZONS:
        frame = frames[(horizon, "target_rv")]
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
    """Run the four family representatives against the target jointly."""
    design = sm.add_constant(frame[ENCOMPASSING_MODELS].to_numpy())
    fit = sm.OLS(frame[target].to_numpy(), design).fit(
        cov_type="HAC", cov_kwds={"maxlags": horizon}
    )
    return pd.DataFrame(
        {
            "horizon": horizon,
            "target": target,
            "term": ["const"] + ENCOMPASSING_MODELS,
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
            "realized": trailing_realized_vol(returns, 21) * 100,
            "VIX": panel_df["VIX"].to_numpy(),
        }
    )

    figure, axes = plt.subplots(2, 1, figsize=(11, 6.5), sharex=True, height_ratios=[1, 1.3])
    axes[0].plot(plot_df["date"], plot_df["SPX"], color="#2A2A2A", lw=1.2)
    axes[0].set_ylabel("SPX")
    axes[0].set_title("SPX and volatility, 2024-2025", loc="left")

    axes[1].plot(plot_df["date"], plot_df["realized"], color=PALETTE["Realized"], lw=1.3,
                 label="Realized vol (21d, trailing)")
    axes[1].plot(plot_df["date"], plot_df["VIX"], color=PALETTE["IV"], lw=1.3, label="VIX")
    axes[1].fill_between(
        plot_df["date"], plot_df["realized"], plot_df["VIX"],
        where=plot_df["VIX"] >= plot_df["realized"],
        color=PALETTE["IV"], alpha=0.12, label="Implied above realized",
    )
    axes[1].set_ylabel("annualized vol (%)")
    axes[1].legend(loc="upper left", frameon=False)
    figure.savefig(FIGURES / "01_volatility_landscape.png")
    plt.close(figure)


def plot_forecast_paths(frames: dict[tuple[int, str], pd.DataFrame]) -> None:
    """One representative per family against the thing they are trying to hit."""
    figure, axes = plt.subplots(len(HORIZONS), 1, figsize=(11, 7), sharex=True)
    for axis, horizon in zip(axes, HORIZONS):
        frame = frames[(horizon, "target_rv")]
        axis.plot(frame["date"], frame["target_rv"] * 100, color=PALETTE["Realized"],
                  lw=2.0, label=f"realized vol, next {horizon}d")
        for model in PATH_MODELS:
            axis.plot(frame["date"], frame[model] * 100, color=PALETTE[model],
                      lw=1.1, alpha=0.85, label=model)
        axis.set_ylabel("annualized vol (%)")
        axis.set_title(f"h = {horizon} trading days", loc="left", fontsize=11)
    axes[0].legend(loc="upper left", ncol=5, frameon=False, fontsize=9)
    figure.suptitle("Out-of-sample forecasts vs forward realized vol",
                    x=0.125, ha="left", weight="bold")
    figure.savefig(FIGURES / "02_forecast_paths.png")
    plt.close(figure)


def plot_scatter(
    frames: dict[tuple[int, str], pd.DataFrame], target: str, filename: str, label: str
) -> None:
    """Forecast on x, outcome on y, with the 45-degree line and the MZ fit."""
    horizons = list(HORIZONS)
    figure, axes = plt.subplots(
        len(horizons), len(SCATTER_MODELS), figsize=(15, 6.2), sharex="row", sharey="row"
    )
    for row, horizon in enumerate(horizons):
        frame = frames[(horizon, target)]
        actual = frame[target].to_numpy() * 100
        for column, model in enumerate(SCATTER_MODELS):
            axis = axes[row, column]
            forecast = frame[model].to_numpy() * 100
            axis.scatter(forecast, actual, s=9, alpha=0.45, color=PALETTE[model], edgecolor="none")

            limits = [min(forecast.min(), actual.min()), max(forecast.max(), actual.max())]
            axis.plot(limits, limits, color="#999999", lw=1, ls="--", label="45°")
            fit = np.polyfit(forecast, actual, 1)
            axis.plot(limits, np.polyval(fit, limits), color="#2A2A2A", lw=1.4, label="MZ fit")

            stats = mincer_zarnowitz(actual, forecast, horizon)
            axis.set_title(f"{model}, h={horizon}\nR²={stats['r2']:.2f}  β={stats['beta']:.2f}",
                           fontsize=9.5)
            if column == 0:
                axis.set_ylabel(f"{label} (%)")
            if row == len(horizons) - 1:
                axis.set_xlabel("forecast (%)")
    axes[0, 0].legend(fontsize=8, frameon=False, loc="upper left")
    figure.suptitle(f"Forecast vs {label}", x=0.08, ha="left", weight="bold")
    figure.tight_layout()
    figure.savefig(FIGURES / filename)
    plt.close(figure)


def plot_scoreboard(scores_df: pd.DataFrame) -> None:
    """The headline comparison: accuracy and explanatory power, both targets."""
    figure, axes = plt.subplots(2, 2, figsize=(13, 7))
    for row, target in enumerate(HEADLINE_TARGETS):
        subset_df = scores_df[scores_df["target"] == target]
        for column, (metric, title) in enumerate(
            [("rmse", "RMSE (vol points)"), ("r2", "Mincer-Zarnowitz R²")]
        ):
            axis = axes[row, column]
            plot_df = subset_df.copy()
            if metric == "rmse":
                plot_df[metric] = plot_df[metric] * 100
            sns.barplot(data=plot_df, x="model", y=metric, hue="horizon", order=MODELS,
                        palette=["#8FA9C9", "#3B6394"], ax=axis)
            axis.set_title(f"{title} — {TARGETS[target]}", fontsize=10, loc="left")
            axis.set_xlabel("")
            axis.set_ylabel("")
            axis.tick_params(axis="x", labelrotation=30)
            axis.legend(title="horizon (d)", fontsize=8, title_fontsize=8, frameon=False)
    figure.suptitle("Scoreboard: lower RMSE is better, higher R² is better",
                    x=0.07, ha="left", weight="bold")
    figure.tight_layout()
    figure.savefig(FIGURES / "05_scoreboard.png")
    plt.close(figure)


def plot_risk_premium(frames: dict[tuple[int, str], pd.DataFrame]) -> None:
    """IV minus subsequent RV — the wedge that explains most of the IV result."""
    figure, axes = plt.subplots(1, 2, figsize=(11.5, 4.2))
    frame = frames[(21, "target_rv")]
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
    axes[1].set_title(f"corr {correlation:.3f}, VIX sits {spread:+.1f} pts above ATM",
                      fontsize=10, loc="left")
    axes[1].set_xlabel("SPXW 30d ATM IV (%)")
    axes[1].set_ylabel("VIX (%)")
    figure.suptitle("Sanity check on the implied-vol measure", x=0.07, ha="left", weight="bold")
    figure.tight_layout()
    figure.savefig(FIGURES / "06_implied_vol_validation.png")
    plt.close(figure)

    return pd.DataFrame([{"days": len(merged_df), "corr": correlation, "mean_spread_points": spread}])


def plot_range_estimators(panel_df: pl.DataFrame) -> pd.DataFrame:
    """How the range estimators compare with close-to-close on the same days."""
    returns = panel_df["ret"].to_numpy()
    variances = daily_range_variances(panel_df)
    dates = panel_df["date"].to_numpy()

    figure, axes = plt.subplots(1, 2, figsize=(12, 4.3))
    axes[0].plot(dates, trailing_realized_vol(returns, 21) * 100, color=PALETTE["Realized"],
                 lw=1.6, label="close-to-close")
    colors = {"Parkinson": "#55A868", "Garman-Klass": "#DD8452",
              "Rogers-Satchell": "#8172B3", "GKYZ": "#4C72B0"}
    rows = []
    for name, variance in variances.items():
        trailing = trailing_range_vol(variance, 21)
        axes[0].plot(dates, trailing * 100, color=colors[name], lw=1.1, alpha=0.9, label=name)
        rows.append(
            {
                "estimator": name,
                "mean_vol_points": float(np.sqrt(252 * np.nanmean(variance)) * 100),
                "corr_with_close_to_close": float(
                    pd.Series(variance).corr(pd.Series(returns**2))
                ),
                "relative_efficiency": float(
                    np.nanvar(returns**2) / np.nanvar(variance)
                ),
            }
        )
    axes[0].set_ylabel("annualized vol (%)")
    axes[0].set_title("21-day trailing vol by estimator", fontsize=10, loc="left")
    axes[0].legend(frameon=False, fontsize=8, ncol=2)

    estimator_df = pd.DataFrame(rows)
    sns.barplot(data=estimator_df, x="estimator", y="relative_efficiency",
                palette=[colors[name] for name in estimator_df["estimator"]],
                hue="estimator", legend=False, ax=axes[1])
    axes[1].axhline(1, color="#999999", lw=1, ls="--")
    axes[1].set_title("variance ratio vs squared close-to-close returns\n"
                      "(higher = less noisy per day)", fontsize=10, loc="left")
    axes[1].set_xlabel("")
    axes[1].set_ylabel("")
    axes[1].tick_params(axis="x", labelrotation=20)
    figure.suptitle("Range estimators use the high and low, not just the close",
                    x=0.07, ha="left", weight="bold")
    figure.tight_layout()
    figure.savefig(FIGURES / "07_range_estimators.png")
    plt.close(figure)
    return estimator_df


def plot_coefficient_instability(panel_df: pl.DataFrame, base_df: pd.DataFrame) -> None:
    """Why the fitted models fail: their coefficients are re-learned after the event.

    Both HAR and IV-adj train on whatever has already been realized. Until
    April 2025 that is a calm sample in which forward vol barely moves, so both
    fit a nearly flat relationship — and stay flat through the one episode
    worth forecasting. The coefficients only jump once the crash is inside the
    training window, which is too late to be of any use.
    """
    horizon = 21
    returns = panel_df["ret"].to_numpy()
    outcome = base_df["target_rv"].to_numpy()
    _, har_path = fit_regression_forecast(
        har_features(returns), outcome, horizon, BURN_IN, return_coefficients=True
    )
    _, iv_path = fit_regression_forecast(
        base_df["IV"].to_numpy().reshape(-1, 1), outcome, horizon, BURN_IN,
        return_coefficients=True,
    )
    dates = base_df["date"].to_numpy()

    figure, axis = plt.subplots(figsize=(11, 4.2))
    axis.axhline(0, color="#999999", lw=1)
    axis.plot(dates, iv_path[:, 1], color=PALETTE["IV-adj"], lw=1.6,
              label="IV-adj: slope on implied vol")
    axis.plot(dates, har_path[:, 3], color=PALETTE["HAR"], lw=1.6,
              label="HAR: coefficient on monthly RV")
    shock = pd.Timestamp("2025-04-07")
    axis.axvline(shock, color="#C44E52", lw=1.2, ls="--")
    axis.annotate("April 2025 enters\nthe training window", xy=(shock, 0.75),
                  xytext=(10, 0), textcoords="offset points", fontsize=9, color="#C44E52")
    axis.set_ylabel("fitted coefficient")
    axis.set_title("The fitted models learn the shock only after it has happened (h=21)",
                   loc="left")
    axis.legend(frameon=False, fontsize=9, loc="upper left")
    figure.savefig(FIGURES / "09_coefficient_instability.png")
    plt.close(figure)


def plot_target_robustness(scores_df: pd.DataFrame) -> None:
    """Does the ranking survive measuring the target with the range estimator?"""
    subset_df = scores_df[scores_df["target"].isin(["target_rv", "target_rv_range"])].copy()
    subset_df["target"] = subset_df["target"].map(
        {"target_rv": "close-to-close", "target_rv_range": "range-based (GKYZ)"}
    )

    figure, axes = plt.subplots(1, len(HORIZONS), figsize=(12.5, 4.4), sharey=True)
    for axis, horizon in zip(axes, HORIZONS):
        sns.barplot(data=subset_df[subset_df["horizon"] == horizon], x="model", y="r2",
                    hue="target", order=MODELS, palette=["#3B6394", "#55A868"], ax=axis)
        axis.set_title(f"h = {horizon} trading days", fontsize=10, loc="left")
        axis.set_xlabel("")
        axis.set_ylabel("Mincer-Zarnowitz R²")
        axis.tick_params(axis="x", labelrotation=30)
        axis.legend(title="target measured as", fontsize=8, title_fontsize=8, frameon=False)
    figure.suptitle("A less noisy target raises every model's R², and changes no ranking",
                    x=0.07, ha="left", weight="bold")
    figure.tight_layout()
    figure.savefig(FIGURES / "08_target_robustness.png")
    plt.close(figure)


def main() -> None:
    setup_style()
    FIGURES.mkdir(exist_ok=True)
    RESULTS.mkdir(exist_ok=True)

    panel_df = load_panel()
    print(f"panel: {panel_df.height} trading days, {panel_df['date'].min()} .. {panel_df['date'].max()}")

    frames = {}
    bases = {}
    for horizon, iv_symbol in HORIZONS.items():
        print(f"fitting h={horizon} (implied vol: {iv_symbol}) ...")
        base_df = bases[horizon] = build_base(panel_df, horizon, iv_symbol)
        for target in TARGETS:
            frames[(horizon, target)] = attach_fitted(base_df, panel_df, target, horizon)
        print(f"  {len(frames[(horizon, 'target_rv')])} out-of-sample origins")

    scores_df = pd.concat(
        [score(frame, target, horizon) for (horizon, target), frame in frames.items()],
        ignore_index=True,
    )
    dm_df = pd.concat(
        [
            diebold_mariano(frames[(horizon, target)], target, horizon)
            for horizon in HORIZONS
            for target in HEADLINE_TARGETS
        ],
        ignore_index=True,
    )
    encompassing_df = pd.concat(
        [
            encompassing(frames[(horizon, target)], target, horizon)
            for horizon in HORIZONS
            for target in HEADLINE_TARGETS
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
    validation_df = plot_iv_validation(panel_df)
    estimator_df = plot_range_estimators(panel_df)
    plot_target_robustness(scores_df)
    plot_coefficient_instability(panel_df, bases[21])

    scores_df.to_csv(RESULTS / "scores.csv", index=False)
    dm_df.to_csv(RESULTS / "diebold_mariano.csv", index=False)
    encompassing_df.to_csv(RESULTS / "encompassing.csv", index=False)
    premium_df.to_csv(RESULTS / "variance_risk_premium.csv", index=False)
    validation_df.to_csv(RESULTS / "iv_validation.csv", index=False)
    estimator_df.to_csv(RESULTS / "range_estimators.csv", index=False)

    pd.set_option("display.width", 200, "display.float_format", "{:.3f}".format)
    for target in TARGETS:
        print(f"\n=== scores: {TARGETS[target]} ===")
        print(scores_df[scores_df["target"] == target].to_string(index=False))
    print("\n=== Diebold-Mariano: IV vs each rival ===")
    print(dm_df.to_string(index=False))
    print("\n=== encompassing regressions ===")
    print(encompassing_df.to_string(index=False))
    print("\n=== variance risk premium ===")
    print(premium_df.to_string(index=False))
    print("\n=== range estimators ===")
    print(estimator_df.to_string(index=False))
    print("\n=== implied vol validation ===")
    print(validation_df.to_string(index=False))
    print(f"\nfigures -> {FIGURES}")


if __name__ == "__main__":
    main()
