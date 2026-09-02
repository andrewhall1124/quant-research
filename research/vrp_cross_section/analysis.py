"""The VRP cross-section: long the cheapest options, short the richest.

Every day, rank the S&P 500 names on how rich their 30-day ATM straddle is,
sort into deciles, and buy the bottom decile against the top, each side
equal-weighted by vega and delta-hedged daily.

**The headline sort is the IV z-score**: each name's implied vol against its
own trailing 60-day IV history. It measures the same thing a variance risk
premium does — is this option expensive relative to what it should cost — but
it does it without a realized-vol forecast, and that is why it leads.

The obvious alternative, `IV - E[RV]` with a GARCH forecast, turned out to be
a sort on forecast error rather than on option richness. Across deciles, the
GARCH forecast spans about 36 vol points while implied vol spans 13, so the
"cheapest" decile is really the decile where the model is extrapolating
hardest — a 66% annualized forecast against a 38% market IV, mostly the April
2025 shock echoing through the conditional variance. `single_name_vol` had
already found GARCH badly calibrated on these names (MZ slope 0.23-0.45); this
is what that miscalibration looks like when a strategy is built on it. It is
kept as a variant in the signal race, not as the strategy.

One cost of the switch is worth stating. The GARCH burn-in ran on *returns*,
so pulling 2023-2024 underlying history bought it back cheaply. The z-score
burns in on *implied vol*, which only exists where the option store does, so
its first ~40 trading days of 2025 are unavoidably lost until more option
history is pulled.

Run it:

    uv run python -m tools.backtest.panel --refresh      # ~minutes, cached
    uv run python -m research.vrp_cross_section.analysis    # the study

Everything mechanical lives in `tools/backtest/`; this module only chooses
configurations and scores them. The two axes the study sweeps are the ones the
strategy is genuinely uncertain about: how much open interest to demand, and
how long to hold.
"""

import argparse
from datetime import date
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import polars as pl
import seaborn as sns

from tools.backtest import BacktestConfig, build_context, run
from tools.backtest import metrics
from tools.backtest.eligibility import (
    MaxRelativeSpread,
    MinOpenInterest,
    MinStructureVega,
    MinUnderlyingPrice,
)
from tools.backtest.signals import IvLevelSignal, IvZScoreSignal, VrpSignal
from tools.backtest.structures import AtmStraddle

HERE = Path(__file__).resolve().parent
FIGURES = HERE / "figures"
RESULTS = HERE / "results"

HORIZON = 21
HOLDING_GRID = [5, 10, 21, 42]
# Calibrated against what the selected straddles actually carry, which is far
# less than an index-level intuition suggests: the median ATM 30-day straddle
# in this universe has only 19 contracts of open interest on its thinner leg,
# and the 75th percentile is 119. A floor of 500 already leaves ~40 names a
# day, and 10,000 leaves fewer than 5 — not a cross-section you can cut into
# deciles. Where the grid runs out is itself a result.
OI_GRID = [0, 25, 50, 100, 250, 500]

# The z-score window. 60 days is long enough for a stable location and scale
# and short enough to leave most of 2025 usable; `min_periods` sets how much of
# January is spent burning in.
IV_WINDOW = 60
IV_MIN_PERIODS = 40

# The liquidity screen the holding-period grid and the signal race are run at.
# It is deliberately loose. A decile sort needs a deep cross-section, and this
# universe does not have one once open interest is demanded: at a floor of 500
# the median day offers 15 eligible names, which is one or two per decile, and
# `min_names_per_side` then drops most of the calendar. A floor of 25 still
# leaves ~156 names on the median day — deciles of ~15 — across 197 of 250
# dates, so the two grids vary their own axis rather than quietly varying the
# sample as well.
BASELINE_OI = 25

# The screens that are not the point of the study but are needed for the
# sizing to be sane at all. See `MinStructureVega` for why.
BASE_FILTERS = (MinStructureVega(5.0), MinUnderlyingPrice(10.0))

PALETTE = {
    "long": "#55A868",
    "short": "#C44E52",
    "total": "#4C72B0",
    "hedge": "#937860",
    "grid": "#8C8C8C",
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


def baseline_config(**overrides) -> BacktestConfig:
    settings = dict(
        signal=IvZScoreSignal(window=IV_WINDOW, min_periods=IV_MIN_PERIODS),
        structure=AtmStraddle(target_dte=30, max_dte_error=7, max_moneyness=0.05),
        eligibility=BASE_FILTERS,
        holding_days=HORIZON,
        n_quantiles=10,
        label="baseline",
    )
    settings.update(overrides)
    return BacktestConfig(**settings)


def run_oi_grid(context) -> tuple[pl.DataFrame, list]:
    """Sweep the open-interest floor at the baseline holding period."""
    results = []
    for threshold in OI_GRID:
        filters = BASE_FILTERS + ((MinOpenInterest(threshold),) if threshold else ())
        config = baseline_config(eligibility=filters, label=f"oi>={threshold}")
        results.append(run(config, context))
        print(f"  oi>={threshold}: {results[-1].diagnostics}", flush=True)
    return metrics.compare(results), results


def run_holding_grid(context) -> tuple[pl.DataFrame, list]:
    """Sweep the holding period at a moderate liquidity screen."""
    filters = BASE_FILTERS + (MinOpenInterest(BASELINE_OI),)
    results = []
    for holding in HOLDING_GRID:
        config = baseline_config(
            eligibility=filters, holding_days=holding, label=f"hold={holding}d"
        )
        results.append(run(config, context))
        print(f"  hold={holding}: {results[-1].diagnostics}", flush=True)
    return metrics.compare(results), results


def run_cost_grid(context) -> tuple[pl.DataFrame, list]:
    """The same strategy charged progressively more of the quoted spread.

    This is the axis that decides whether any of the rest matters. Single-name
    30-day ATM straddles in this universe quote at a median 19.8% of mid, and
    the short holding periods that look best gross are exactly the ones that
    pay that spread most often — a 5-day hold turns the whole book over every
    week. `break_even_spread_frac` in the output says what fraction of the
    quoted spread the gross P&L could actually afford.
    """
    filters = BASE_FILTERS + (MinOpenInterest(BASELINE_OI),)
    results = []
    for holding in (5, 21):
        for fraction in (0.0, 0.25, 0.5, 1.0):
            config = baseline_config(
                eligibility=filters,
                holding_days=holding,
                spread_cost_fraction=fraction,
                label=f"hold={holding}d cost={fraction:g}",
            )
            results.append(run(config, context))
            print(f"  {config.label}: {metrics.summarize(results[-1].daily_pnl, holding)}", flush=True)
    return metrics.compare(results), results


def run_signal_race(context) -> tuple[pl.DataFrame, list]:
    """The same machinery on four different sorts.

    Two controls matter. `IvLevelSignal` sorts on raw implied vol: if it does
    as well as the z-score, then the strategy is a high-vol/low-vol tilt and
    the "relative to its own history" part is decoration. `vrp_garch` is the
    forecast-based definition the study started with, kept so the report can
    show what it does rather than just assert that it fails.

    The two z-score windows are there because the choice of window is the one
    free parameter the headline sort has, and a result that only survives at
    one window is not a result.
    """
    filters = BASE_FILTERS + (MinOpenInterest(BASELINE_OI),)
    signals = [
        ("iv_zscore_60", IvZScoreSignal(window=IV_WINDOW, min_periods=IV_MIN_PERIODS)),
        ("iv_zscore_20", IvZScoreSignal(window=20, min_periods=15)),
        ("vrp_garch", VrpSignal(forecast="GARCH", horizon=HORIZON)),
        ("vrp_rv", VrpSignal(forecast="RV", horizon=HORIZON)),
        ("iv_level", IvLevelSignal()),
    ]
    results = []
    for label, signal in signals:
        config = baseline_config(signal=signal, eligibility=filters, label=label)
        results.append(run(config, context))
        print(f"  {label}: {results[-1].diagnostics}", flush=True)
    return metrics.compare(results), results


def plot_equity(result, filename: str) -> None:
    """Cumulative dollar P&L, split into the option and hedge legs.

    The split is the point: a spread that comes from the hedge rather than the
    options is not a statement about the volatility premium.
    """
    daily = result.daily_pnl.sort("date")
    dates = daily["date"].to_list()
    figure, axes = plt.subplots(1, 2, figsize=(12.5, 4.3))

    axes[0].plot(dates, np.cumsum(daily["total_pnl"]), color=PALETTE["total"], lw=1.7, label="total")
    axes[0].plot(dates, np.cumsum(daily["option_pnl"]), color=PALETTE["long"], lw=1.2, label="option leg")
    axes[0].plot(dates, np.cumsum(daily["hedge_pnl"]), color=PALETTE["hedge"], lw=1.2, label="delta hedge")
    axes[0].axhline(0, color="#999999", lw=1)
    axes[0].set_title("Cumulative P&L, decomposed")
    axes[0].set_ylabel("dollars")
    axes[0].legend(frameon=False, fontsize=8)

    axes[1].plot(dates, np.cumsum(daily["long_pnl"]), color=PALETTE["long"], lw=1.5, label="long (cheapest decile)")
    axes[1].plot(dates, np.cumsum(daily["short_pnl"]), color=PALETTE["short"], lw=1.5, label="short (richest decile)")
    axes[1].axhline(0, color="#999999", lw=1)
    axes[1].set_title("Cumulative P&L by side")
    axes[1].legend(frameon=False, fontsize=8)

    for axis in axes:
        axis.tick_params(axis="x", rotation=30)
    figure.tight_layout()
    figure.savefig(FIGURES / filename)
    plt.close(figure)


def plot_deciles(result, filename: str) -> None:
    """Mean daily P&L across all ten deciles, with Newey-West error bars.

    A real cross-sectional signal grades monotonically across the sort. Two
    profitable tails with noise between them is what a spurious result looks
    like, and the middle deciles are the only place that shows.
    """
    table = metrics.decile_table(result.decile_pnl, result.config.holding_days)
    if table.height == 0:
        return
    figure, axis = plt.subplots(figsize=(7.5, 4.2))
    quantiles = table["quantile"].to_list()
    means = table["mean_daily_pnl"].to_numpy()
    colours = [PALETTE["long"] if m > 0 else PALETTE["short"] for m in means]
    axis.bar(quantiles, means, color=colours, alpha=0.85)
    axis.axhline(0, color="#2A2A2A", lw=1)
    axis.set_xlabel("richness decile  (0 = cheapest, 9 = richest)")
    axis.set_ylabel("mean daily P&L, dollars")
    axis.set_title("Every decile held long — is the sort monotonic?")
    axis.set_xticks(quantiles)
    figure.tight_layout()
    figure.savefig(FIGURES / filename)
    plt.close(figure)


def plot_grid(oi_df: pl.DataFrame, holding_df: pl.DataFrame, filename: str) -> None:
    """Sharpe and t-statistic across the two swept axes."""
    figure, axes = plt.subplots(1, 2, figsize=(12.5, 4.3))
    for axis, table, xlabel in (
        (axes[0], oi_df, "minimum open interest"),
        (axes[1], holding_df, "holding period (days)"),
    ):
        labels = table["label"].to_list()
        axis.bar(range(len(labels)), table["sharpe_annual"].to_numpy(),
                 color=PALETTE["total"], alpha=0.85)
        axis.set_xticks(range(len(labels)))
        axis.set_xticklabels(labels, rotation=20, fontsize=8)
        axis.axhline(0, color="#2A2A2A", lw=1)
        axis.set_xlabel(xlabel)
        axis.set_ylabel("annualized Sharpe")
        for position, tstat in enumerate(table["t_stat_nw"].to_numpy()):
            axis.text(position, 0, f"t={tstat:.1f}", ha="center", va="bottom", fontsize=7)
    axes[0].set_title("Open-interest floor")
    axes[1].set_title("Holding period")
    figure.tight_layout()
    figure.savefig(FIGURES / filename)
    plt.close(figure)


def plot_costs(cost_df: pl.DataFrame, filename: str) -> None:
    """Net P&L against the fraction of the quoted spread charged.

    The dashed line at 0.5 is the ordinary execution assumption. Where a
    strategy's curve crosses zero to the left of it, the gross result is not
    large enough to pay for its own trading.
    """
    figure, axis = plt.subplots(figsize=(7.5, 4.4))
    for holding, colour in ((5, PALETTE["short"]), (21, PALETTE["total"])):
        subset = cost_df.filter(pl.col("label").str.starts_with(f"hold={holding}d"))
        if subset.height == 0:
            continue
        fractions = [float(label.split("cost=")[1]) for label in subset["label"]]
        axis.plot(fractions, subset["total_pnl"].to_numpy(), marker="o",
                  color=colour, lw=1.7, label=f"{holding}-day hold")
    axis.axhline(0, color="#2A2A2A", lw=1)
    axis.axvline(0.5, color="#999999", lw=1.2, ls="--")
    axis.text(0.51, axis.get_ylim()[1] * 0.85, "half-spread\neach way", fontsize=8, color="#666666")
    axis.set_xlabel("fraction of the quoted spread paid, each way")
    axis.set_ylabel("net P&L, dollars")
    axis.set_title("What execution costs do to the result")
    axis.legend(frameon=False, fontsize=8)
    figure.tight_layout()
    figure.savefig(FIGURES / filename)
    plt.close(figure)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="refit the vol forecasts")
    parser.add_argument("--forecast-start", type=date.fromisoformat, default=None,
                        help="how far back returns reach, so the burn-in does not eat the sample")
    args = parser.parse_args()

    FIGURES.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    setup_style()

    context = build_context(
        horizon=HORIZON, forecast_start=args.forecast_start, refresh=args.refresh
    )

    print("baseline")
    baseline = run(baseline_config(eligibility=BASE_FILTERS + (MinOpenInterest(BASELINE_OI),)), context)
    print(" ", baseline.diagnostics)
    print(" ", metrics.summarize(baseline.daily_pnl, HORIZON))

    print("open-interest grid")
    oi_df, oi_results = run_oi_grid(context)
    print("holding-period grid")
    holding_df, holding_results = run_holding_grid(context)
    print("signal race")
    race_df, race_results = run_signal_race(context)
    print("cost grid")
    cost_df, cost_results = run_cost_grid(context)

    decile_df = metrics.decile_table(baseline.decile_pnl, HORIZON)

    oi_df.write_csv(RESULTS / "open_interest_grid.csv")
    holding_df.write_csv(RESULTS / "holding_grid.csv")
    race_df.write_csv(RESULTS / "signal_race.csv")
    cost_df.write_csv(RESULTS / "cost_grid.csv")
    decile_df.write_csv(RESULTS / "decile_monotonicity.csv")
    baseline.daily_pnl.write_csv(RESULTS / "baseline_daily_pnl.csv")

    plot_equity(baseline, "01_equity.png")
    plot_deciles(baseline, "02_deciles.png")
    plot_grid(oi_df, holding_df, "03_grids.png")
    plot_costs(cost_df, "04_costs.png")

    print("\n=== open interest ===")
    print(oi_df)
    print("\n=== holding period ===")
    print(holding_df)
    print("\n=== signal race ===")
    print(race_df)
    print("\n=== transaction costs ===")
    print(cost_df.select("label", "days", "mean_daily_pnl", "t_stat_nw", "sharpe_annual",
                         "gross_pnl", "spread_cost", "break_even_spread_frac"))
    print("\n=== decile monotonicity ===")
    print(decile_df)


if __name__ == "__main__":
    main()
