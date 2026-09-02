"""Cheap against expensive implied vol, in the cross-section of single names.

Every trading day, rank the S&P 500 on how expensive each name's implied
volatility is relative to its *own* recent history, buy the cheapest decile
against the richest, weight both sides equally by vega, delta-hedge daily, and
hold each position until its contract expires.

The bet is that implied volatility mean-reverts: a name trading well above its
own recent level tends to come back down, and one trading below tends to come
back up, relative to peers. `run_attribution` confirms that is where the money
comes from — two thirds of the option P&L is vega, the channel that pays when
implied vol moves, against a third from gamma and theta, the channel that pays
when realized volatility undershoots what was implied.

That distinction is why this study is not called a variance-risk-premium
strategy. A delta-hedged straddle held to expiry *would* harvest the variance
premium, and the premium is real — `research/single_name_vol/` measures it as
positive in 86-97% of these names. But the position is deliberately
vega-neutral, which nets the premium's level out, and the sort that works is
not a premium estimate: the textbook `IV - E[RV]` ranks worst of the four
signals tried (§3). What is being traded is the mean reversion, not the
premium.

    uv run python -m tools.backtest.panel --refresh
    uv run python -m research.single_name_iv_reversion.analysis --forecast-start 2023-06-01

Everything mechanical lives in `tools/backtest/`. This module specifies the
strategy, runs the experiments that justify each of its choices, and writes the
figures and tables `REPORT.md` refers to.

## The strategy in one paragraph

Implied volatility is, on average, higher than the volatility that
subsequently gets realized — sellers of options charge a premium for bearing
variance risk. That premium is not constant across names, and the dispersion is
what this trades: a name whose implied vol is high relative to its own recent
level is charging more than usual, and one whose implied vol is low is charging
less. Buying the cheap and selling the rich is a bet that the premium
mean-reverts, held vega-neutral so the position is a bet on relative richness
rather than on the level of volatility, and delta-hedged so it is a bet on
volatility rather than on direction.

## Why each choice is what it is

Every parameter below is set by an experiment in this module rather than by
convention, and each is reported in `REPORT.md`:

* **The signal** is implied vol against the name's own 60-day history, not the
  textbook `IV - E[RV]`. `run_signal_race` shows the forecast-based definition
  ranks mostly on forecast error, and that sorting on the raw level of implied
  vol loses money outright.
* **The tenor** is 60 days, not the 30 that a "30-day implied vol" signal
  suggests. `run_tenor_grid` shows gross performance roughly doubling from 30
  to 60 days, and `cost_efficiency.py` shows why: vega grows as the square root
  of time while quoted spreads do not, so a longer contract buys the same
  exposure for less spread.
* **The liquidity floor** is 250 contracts of open interest. Same grid: it
  costs almost nothing in gross terms at this tenor, and buys much tighter
  quotes.
* **Earnings are excluded** when the announcement falls inside the contract's
  life. `run_earnings_evidence` shows a short-dated sort is largely ranking the
  earnings calendar, and that the returns come from the names without an
  announcement.
* **Positions are held to expiry** rather than sold. `run_exit_rule` shows this
  halves the bid-ask bill, because a settled position crosses the spread once
  instead of twice. It is the single largest improvement in the study.
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
    ExcludeEarningsBeforeExpiry,
    MinOpenInterest,
    MinStructureVega,
    MinUnderlyingPrice,
)
from tools.backtest.portfolio import assign_quantiles
from tools.backtest.signals import IvLevelSignal, IvZScoreSignal, VrpSignal
from tools.backtest.structures import AtmStraddle, summarize_positions

HERE = Path(__file__).resolve().parent
FIGURES = HERE / "figures"
RESULTS = HERE / "results"

# --- the strategy -----------------------------------------------------------

TENOR = 60          # days to expiration at formation
TENOR_TOLERANCE = 12  # how far the listed calendar may be from that target
OI_FLOOR = 250      # contracts of open interest required on the thinner leg
IV_WINDOW = 60      # sessions of implied-vol history the z-score is measured against
IV_MIN_PERIODS = 40
N_QUANTILES = 10
GROSS_VEGA_PER_SIDE = 10_000.0
COST_FRACTION = 0.5  # half the quoted spread, the ordinary execution assumption

# Screens that are hygiene rather than strategy. A structure carrying almost no
# vega needs an absurd number of contracts to fill a vega budget, and a
# low-priced name has a strike grid too coarse to sit near the money.
HYGIENE = (MinStructureVega(5.0), MinUnderlyingPrice(10.0))

# Axes the supporting experiments sweep.
TENOR_GRID = [(30, 7), (60, 12), (90, 20), (120, 20)]
OI_GRID = [0, 25, 100, 250, 1000]
COST_GRID = [0.0, 0.25, 0.5, 0.75, 1.0]

PALETTE = {
    "long": "#55A868",
    "short": "#C44E52",
    "total": "#4C72B0",
    "hedge": "#937860",
    30: "#C44E52",
    60: "#DD8452",
    90: "#55A868",
    120: "#4C72B0",
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


def straddle(target_dte: int = TENOR, tolerance: int = TENOR_TOLERANCE) -> AtmStraddle:
    return AtmStraddle(
        target_dte=target_dte, max_dte_error=tolerance, max_moneyness=0.05
    )


def strategy(**overrides) -> BacktestConfig:
    """The strategy. Every experiment below is this with one thing changed."""
    settings = dict(
        signal=IvZScoreSignal(window=IV_WINDOW, min_periods=IV_MIN_PERIODS),
        structure=straddle(),
        eligibility=HYGIENE + (MinOpenInterest(OI_FLOOR), ExcludeEarningsBeforeExpiry()),
        n_quantiles=N_QUANTILES,
        gross_vega_per_side=GROSS_VEGA_PER_SIDE,
        hold_to_expiry=True,
        spread_cost_fraction=COST_FRACTION,
        label="strategy",
    )
    settings.update(overrides)
    return BacktestConfig(**settings)


# --- supporting experiments -------------------------------------------------


def run_signal_race(context) -> tuple[pl.DataFrame, list]:
    """Why the signal is a z-score and not the textbook variance risk premium.

    Four sorts through identical machinery. `iv_level` is the control that
    matters: it ranks on the raw level of implied vol, so if it did as well as
    the z-score the strategy would just be a bet that low-vol names outperform
    high-vol ones, and the "relative to its own history" part would be
    decoration. It loses money, which is the evidence that the normalisation is
    doing the work.

    `vrp_garch` and `vrp_rv` are the textbook definition, implied vol minus a
    forecast of realized vol. They rank worse because the forecast is the weak
    part: across deciles a GARCH forecast spans about 36 volatility points where
    implied vol spans 13, so the sort ends up ranking forecast error.
    """
    signals = [
        ("iv_zscore", IvZScoreSignal(window=IV_WINDOW, min_periods=IV_MIN_PERIODS)),
        ("vrp_garch", VrpSignal(forecast="GARCH", horizon=21)),
        ("vrp_rv", VrpSignal(forecast="RV", horizon=21)),
        ("iv_level", IvLevelSignal()),
    ]
    results = []
    for label, signal in signals:
        # Gross, so the comparison is about the sort rather than about costs.
        config = strategy(signal=signal, spread_cost_fraction=0.0, label=label)
        results.append(run(config, context))
        print(f"  {label}: {results[-1].diagnostics['formation_days']} days", flush=True)
    return metrics.compare(results), results


def run_tenor_grid(context) -> tuple[pl.DataFrame, list]:
    """Why 60 days, and why a real liquidity floor is affordable.

    Two findings live in this grid. Gross performance roughly doubles going
    from a 30-day contract to a 60-day one, which is the opposite of the
    intuition that a shorter contract is closer to the volatility being
    forecast. And at longer tenors the strategy stops caring about the
    open-interest floor: at 30 days demanding 1,000 contracts of open interest
    destroys the signal, at 120 days it barely dents it.

    Run gross, because the point is what happens to the signal. What happens to
    the cost is `cost_efficiency.py` and `run_exit_rule`.
    """
    results = []
    for target_dte, tolerance in TENOR_GRID:
        for floor in OI_GRID:
            filters = HYGIENE + ((MinOpenInterest(floor),) if floor else ())
            config = strategy(
                structure=straddle(target_dte, tolerance),
                eligibility=filters + (ExcludeEarningsBeforeExpiry(),),
                spread_cost_fraction=0.0,
                label=f"{target_dte}d oi>={floor}",
            )
            try:
                results.append(run(config, context))
            except ValueError:
                # Long tenor plus a hard floor can leave too few names to cut
                # into deciles on any day. That is a fact about the
                # cross-section, not a failure.
                print(f"  {config.label}: too thin", flush=True)
                continue
            print(f"  {config.label}: {results[-1].diagnostics['formation_days']} days", flush=True)
    return metrics.compare(results), results


def earnings_profile(context) -> pl.DataFrame:
    """How much of the sort is the earnings calendar rather than the premium.

    For each tenor, the fraction of selected contracts whose life contains an
    announcement, by decile. A column that climbs across deciles means the sort
    is partly ranking the earnings calendar: "expensive" mostly means "an
    announcement lands before this contract expires", which is true but is not
    the premium the strategy is trying to harvest.
    """
    base = IvZScoreSignal(window=IV_WINDOW, min_periods=IV_MIN_PERIODS)
    frames = []
    for target_dte, tolerance in TENOR_GRID:
        legs_df = straddle(target_dte, tolerance).select(context.selection_df)
        positions_df = summarize_positions(legs_df).join(
            context.earnings_df, on=["symbol", "date"], how="left"
        ).with_columns(
            (pl.col("days_to_earnings") <= pl.col("dte")).alias("earnings_before_expiry")
        )
        ranked = assign_quantiles(base.attach(positions_df, context.forecasts_df), N_QUANTILES)
        frames.append(
            ranked.group_by("quantile")
            .agg(
                pl.col("earnings_before_expiry").fill_null(False).mean().alias("frac_earnings_in_life"),
                pl.col("days_to_earnings").median().alias("median_days_to_earnings"),
            )
            .sort("quantile")
            .with_columns(pl.lit(target_dte).alias("tenor"))
        )
    return pl.concat(frames).select("tenor", "quantile", "frac_earnings_in_life",
                                    "median_days_to_earnings")


def run_earnings_evidence(context) -> tuple[pl.DataFrame, list]:
    """Why names with an announcement inside the contract are excluded.

    Partitioning the universe and running each half as its own strategy. If
    the P&L were an earnings trade — selling the volatility that gets crushed
    after an announcement — the half *with* earnings would carry it. It does
    not: the signal is stronger with those names removed. Earnings is noise in
    the ranking rather than the source of the returns, which is why it is
    excluded rather than traded.
    """
    results = []
    for target_dte, tolerance in ((30, 7), (60, 12)):
        for extra, tag in ((HYGIENE + (MinOpenInterest(OI_FLOOR),), "all names"),
                           (HYGIENE + (MinOpenInterest(OI_FLOOR), ExcludeEarningsBeforeExpiry()),
                            "no earnings in life")):
            config = strategy(
                structure=straddle(target_dte, tolerance),
                eligibility=extra,
                spread_cost_fraction=0.0,
                label=f"{target_dte}d {tag}",
            )
            try:
                results.append(run(config, context))
            except ValueError:
                continue
            print(f"  {config.label}: {results[-1].diagnostics['formation_days']} days", flush=True)
    return metrics.compare(results), results


def run_exit_rule(context) -> tuple[pl.DataFrame, list]:
    """Why positions are held to expiry rather than sold to close.

    Selling a position crosses the quoted spread a second time. Holding it to
    expiration does not — the contract settles at intrinsic value, and nobody
    is paid a spread for that. So the bid-ask bill halves, which on a strategy
    whose binding constraint is spread is worth more than any signal
    improvement in this study.

    The cost is that the holding period becomes the tenor. A position held 60
    days rather than 21 earns less per day, because the signal decays. The grid
    reports both so the trade is visible.
    """
    results = []
    for hold_to_expiry in (False, True):
        for target_dte, tolerance in ((30, 7), (60, 12), (120, 20)):
            config = strategy(
                structure=straddle(target_dte, tolerance),
                holding_days=21,
                hold_to_expiry=hold_to_expiry,
                label=f"{target_dte}d {'expiry' if hold_to_expiry else 'sold at 21d'}",
            )
            try:
                results.append(run(config, context))
            except ValueError:
                continue
            print(f"  {config.label}: {results[-1].diagnostics['formation_days']} days", flush=True)
    return metrics.compare(results), results


def run_attribution(gross_result) -> pl.DataFrame:
    """Split the option P&L into the channels that could have produced it.

    A first-order greek attribution, and the experiment that names the
    strategy. Each day, each leg:

    * **vega** = yesterday's vega x the change in implied vol. Money made
      because the implied vol *level* moved.
    * **theta** = yesterday's theta x days elapsed. The premium decaying.
    * **gamma** = half yesterday's gamma x the squared move in the underlying.
      Realized variance being captured.

    Theta and gamma together are the variance risk premium: you pay theta for
    the variance the option implies and earn gamma on the variance that
    actually arrives, so their sum is realized-minus-implied. Vega is
    something else entirely — it pays when the market re-prices volatility,
    whatever subsequently gets realized.

    Whichever dominates is what the strategy is. Here vega does, by a wide
    margin, which is why "variance risk premium" would be the wrong label.

    The attribution is first order, so a residual is expected; it is reported
    rather than hidden, and at about a tenth of the option leg it is far too
    small to disturb the ranking.
    """
    daily = gross_result.daily_pnl
    total = lambda column: float(daily[column].sum())
    option = total("option_pnl")
    vega, theta, gamma = total("vega_pnl"), total("theta_pnl"), total("gamma_pnl")
    rows = [
        {"channel": "vega (implied vol moves)", "pnl": vega},
        {"channel": "theta (premium decay)", "pnl": theta},
        {"channel": "gamma (realized variance)", "pnl": gamma},
        {"channel": "residual (higher order)", "pnl": option - vega - theta - gamma},
        {"channel": "= option leg", "pnl": option},
        {"channel": "delta hedge", "pnl": total("hedge_pnl")},
        {"channel": "= gross total", "pnl": total("gross_pnl")},
        {"channel": "mean reversion channel (vega)", "pnl": vega},
        {"channel": "variance premium channel (gamma+theta)", "pnl": gamma + theta},
    ]
    return pl.DataFrame(rows).with_columns(
        (pl.col("pnl") / option * 100).round(1).alias("pct_of_option_leg")
    )


def run_cost_curve(context) -> tuple[pl.DataFrame, list]:
    """What the strategy earns as it is charged more of the quoted spread.

    The x-axis is the fraction of the quoted bid-ask crossed on entry, and on
    exit where there is one. 0.5 is the ordinary assumption: you meet the mid
    on the way in and on the way out. Where the curve crosses zero is the
    break-even, and it is the number that decides whether any of the rest
    matters.
    """
    results = []
    for fraction in COST_GRID:
        config = strategy(spread_cost_fraction=fraction, label=f"cost={fraction:g}")
        results.append(run(config, context))
    return metrics.compare(results), results


# --- figures ----------------------------------------------------------------


def plot_equity(result, filename: str) -> None:
    """Cumulative P&L, split by leg and by side.

    The left panel separates the options from the delta hedge. That split is
    the honest check on whether this is a volatility strategy: if the P&L came
    from the hedge, the position would be making money on direction rather
    than on the premium.
    """
    daily = result.daily_pnl.sort("date")
    dates = daily["date"].to_list()
    figure, axes = plt.subplots(1, 2, figsize=(12.5, 4.3))

    axes[0].plot(dates, np.cumsum(daily["total_pnl"]), color=PALETTE["total"], lw=1.8, label="net of costs")
    axes[0].plot(dates, np.cumsum(daily["gross_pnl"]), color="#8C8C8C", lw=1.2, ls="--", label="gross")
    axes[0].plot(dates, np.cumsum(daily["option_pnl"]), color=PALETTE["long"], lw=1.2, label="options")
    axes[0].plot(dates, np.cumsum(daily["hedge_pnl"]), color=PALETTE["hedge"], lw=1.2, label="delta hedge")
    axes[0].set_title("Cumulative P&L, decomposed")
    axes[0].set_ylabel("dollars")

    axes[1].plot(dates, np.cumsum(daily["long_pnl"]), color=PALETTE["long"], lw=1.6,
                 label="long (cheapest decile)")
    axes[1].plot(dates, np.cumsum(daily["short_pnl"]), color=PALETTE["short"], lw=1.6,
                 label="short (richest decile)")
    axes[1].set_title("Cumulative P&L by side")

    for axis in axes:
        axis.axhline(0, color="#999999", lw=1)
        axis.legend(frameon=False, fontsize=8)
        axis.tick_params(axis="x", rotation=30)
    figure.tight_layout()
    figure.savefig(FIGURES / filename)
    plt.close(figure)


def plot_deciles(result, filename: str) -> None:
    """Mean daily P&L across all ten deciles, every one held long, gross.

    The check that separates a real cross-sectional signal from two lucky
    tails: if cheapness predicts return, performance should grade across the
    whole sort rather than appearing only at the extremes the strategy trades.

    The levels are mostly negative and that is expected — every decile here is
    held long, and buying volatility loses money on average, which is precisely
    the premium being harvested. What matters is the slope from decile 0 to
    decile 9, not where the bars sit relative to zero.
    """
    table = metrics.decile_table(result.decile_pnl, metrics.effective_lag(result))
    if table.height == 0:
        return
    figure, axis = plt.subplots(figsize=(7.5, 4.2))
    means = table["mean_daily_pnl"].to_numpy()
    axis.bar(table["quantile"].to_list(), means,
             color=[PALETTE["long"] if m > 0 else PALETTE["short"] for m in means], alpha=0.85)
    axis.axhline(0, color="#2A2A2A", lw=1)
    axis.set_xlabel("richness decile  (0 = cheapest, 9 = richest)")
    axis.set_ylabel("mean daily P&L, dollars")
    axis.set_title("Every decile held long — does the sort grade?")
    axis.set_xticks(range(N_QUANTILES))
    figure.tight_layout()
    figure.savefig(FIGURES / filename)
    plt.close(figure)


def plot_tenor(grid_df: pl.DataFrame, filename: str) -> None:
    """Gross Sharpe across tenor and liquidity floor: why 60 days and oi>=250."""
    parsed = grid_df.with_columns(
        pl.col("label").str.extract(r"^(\d+)d").cast(pl.Int32).alias("tenor"),
        pl.col("label").str.extract(r"oi>=(\d+)").cast(pl.Int32).alias("oi"),
    )
    positions = list(range(len(OI_GRID)))
    figure, axis = plt.subplots(figsize=(7.8, 4.4))
    for tenor, _ in TENOR_GRID:
        row = parsed.filter(pl.col("tenor") == tenor).sort("oi")
        lookup = dict(zip(row["oi"].to_list(), row["sharpe_annual"].to_list()))
        axis.plot(positions, [lookup.get(f) for f in OI_GRID], marker="o",
                  color=PALETTE[tenor], lw=1.8, label=f"{tenor}-day")
    axis.axhline(0, color="#2A2A2A", lw=1)
    axis.set_xticks(positions)
    axis.set_xticklabels([str(f) for f in OI_GRID])
    axis.set_xlabel("minimum open interest")
    axis.set_ylabel("gross annualized Sharpe")
    axis.set_title("Longer contracts survive a liquidity screen; short ones do not")
    axis.legend(frameon=False, fontsize=8, title="option tenor", title_fontsize=8)
    figure.tight_layout()
    figure.savefig(FIGURES / filename)
    plt.close(figure)


def plot_earnings(profile_df: pl.DataFrame, filename: str) -> None:
    """What the sort is ranking, by tenor."""
    figure, axis = plt.subplots(figsize=(7.8, 4.4))
    for tenor, _ in TENOR_GRID:
        subset = profile_df.filter(pl.col("tenor") == tenor).sort("quantile")
        axis.plot(subset["quantile"], subset["frac_earnings_in_life"], marker="o",
                  color=PALETTE[tenor], lw=1.8, label=f"{tenor}-day")
    axis.set_ylim(0, 1.05)
    axis.set_xticks(range(N_QUANTILES))
    axis.set_xlabel("richness decile  (0 = cheapest, 9 = richest)")
    axis.set_ylabel("fraction with earnings before expiry")
    axis.set_title("A short-dated sort is largely ranking the earnings calendar")
    axis.legend(frameon=False, fontsize=8, title="option tenor", title_fontsize=8)
    figure.tight_layout()
    figure.savefig(FIGURES / filename)
    plt.close(figure)


def plot_costs(cost_df: pl.DataFrame, exit_df: pl.DataFrame, filename: str) -> None:
    """What execution costs do, and what holding to expiry saves."""
    figure, axes = plt.subplots(1, 2, figsize=(12.5, 4.3))

    fractions = [float(label.split("=")[1]) for label in cost_df["label"]]
    axes[0].plot(fractions, cost_df["total_pnl"].to_numpy(), marker="o",
                 color=PALETTE["total"], lw=1.8)
    axes[0].axhline(0, color="#2A2A2A", lw=1)
    axes[0].axvline(0.5, color="#999999", lw=1.2, ls="--")
    axes[0].text(0.51, 0.05, "half-spread each way", fontsize=8, color="#666666",
                 transform=axes[0].get_xaxis_transform())
    axes[0].set_xlabel("fraction of the quoted spread paid per crossing")
    axes[0].set_ylabel("net P&L, dollars")
    axes[0].set_title("The strategy against its own execution cost")

    parsed = exit_df.with_columns(
        pl.col("label").str.extract(r"^(\d+)d").cast(pl.Int32).alias("tenor"),
        pl.col("label").str.contains("expiry").alias("expiry"),
    )
    tenors = sorted(set(parsed["tenor"].to_list()))
    width = 0.38
    for offset, (expiry, colour, name) in enumerate(
        ((False, "#8C8C8C", "sold after 21 days"), (True, PALETTE["total"], "held to expiry"))
    ):
        subset = parsed.filter(pl.col("expiry") == expiry).sort("tenor")
        lookup = dict(zip(subset["tenor"].to_list(), subset["break_even_spread_frac"].to_list()))
        axes[1].bar([t + (offset - 0.5) * width for t in range(len(tenors))],
                    [lookup.get(t, 0.0) for t in tenors], width=width,
                    color=colour, alpha=0.9, label=name)
    axes[1].axhline(0.5, color="#2A2A2A", lw=1.2, ls="--")
    axes[1].text(0.02, 0.52, "tradeable above this line", fontsize=8, color="#666666",
                 transform=axes[1].get_yaxis_transform())
    axes[1].set_xticks(range(len(tenors)))
    axes[1].set_xticklabels([f"{t}-day" for t in tenors])
    axes[1].set_xlabel("option tenor")
    axes[1].set_ylabel("break-even fraction of quoted spread")
    axes[1].set_title("Holding to expiry halves the bid-ask bill")
    axes[1].legend(frameon=False, fontsize=8)
    figure.tight_layout()
    figure.savefig(FIGURES / filename)
    plt.close(figure)


# --- entry point ------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="refit the vol forecasts")
    parser.add_argument("--forecast-start", type=date.fromisoformat, default=None,
                        help="how far back returns reach, so the model burn-in "
                             "completes before the option sample opens")
    args = parser.parse_args()

    FIGURES.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    setup_style()

    context = build_context(forecast_start=args.forecast_start, refresh=args.refresh)

    print("the strategy")
    headline = run(strategy(), context)
    gross = run(strategy(spread_cost_fraction=0.0, label="strategy gross"), context)
    lag = metrics.effective_lag(headline)
    print("  ", headline.diagnostics)
    print("   net  ", metrics.summarize(headline.daily_pnl, lag))
    print("   gross", metrics.summarize(gross.daily_pnl, lag))

    print("signal race")
    race_df, _ = run_signal_race(context)
    print("tenor x liquidity grid")
    tenor_df, _ = run_tenor_grid(context)
    print("earnings evidence")
    earnings_df, _ = run_earnings_evidence(context)
    profile_df = earnings_profile(context)
    print("exit rule")
    exit_df, _ = run_exit_rule(context)
    print("cost curve")
    cost_df, _ = run_cost_curve(context)
    attribution_df = run_attribution(gross)

    headline_df = metrics.compare([gross, headline])
    # Graded gross, not net. Every decile here is held *long*, and buying
    # options loses money on average — that is the premium the strategy is
    # harvesting. Charging costs on top would make all ten negative and hide
    # the thing this diagnostic exists to show, which is whether cheapness
    # orders the cross-section.
    decile_df = metrics.decile_table(gross.decile_pnl, lag)

    headline_df.write_csv(RESULTS / "strategy.csv")
    headline.daily_pnl.write_csv(RESULTS / "strategy_daily_pnl.csv")
    decile_df.write_csv(RESULTS / "decile_monotonicity.csv")
    race_df.write_csv(RESULTS / "signal_race.csv")
    tenor_df.write_csv(RESULTS / "tenor_grid.csv")
    earnings_df.write_csv(RESULTS / "earnings_partition.csv")
    profile_df.write_csv(RESULTS / "earnings_profile.csv")
    exit_df.write_csv(RESULTS / "exit_rule.csv")
    cost_df.write_csv(RESULTS / "cost_curve.csv")
    attribution_df.write_csv(RESULTS / "attribution.csv")

    plot_equity(headline, "01_strategy.png")
    plot_deciles(gross, "02_deciles.png")
    plot_tenor(tenor_df, "03_tenor.png")
    plot_earnings(profile_df, "04_earnings.png")
    plot_costs(cost_df, exit_df, "05_costs.png")

    for name, table in (("strategy", headline_df), ("attribution", attribution_df),
                        ("signal race", race_df),
                        ("earnings partition", earnings_df), ("exit rule", exit_df),
                        ("cost curve", cost_df), ("deciles", decile_df)):
        print(f"\n=== {name} ===")
        print(table)


if __name__ == "__main__":
    main()
