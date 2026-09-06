"""Decile portfolios sorted on log(HV/IV), after Goyal and Saretto (2008).

Reads the stock-month panel that `panel.py` builds and reproduces the paper's
core tables on this repo's S&P 500 option history:

* Table 2 — formation-period characteristics by decile.
* Table 3 — post-formation returns of straddles, delta-hedged calls and
  delta-hedged puts by decile, plus the 10-1 long-short.
* Table 8 — the long-short straddle at effective spreads of 50/75/100% of the
  quoted spread, and split by option liquidity.
* Robustness — sub-periods, the wider 0.95-1.05 moneyness band, call-only and
  put-only IV, and dropping months with an earnings release in the hold.

Run from the project root, after `panel.py`:

    uv run python -m research.goyal_saretto.analysis
"""

import argparse
import math
from datetime import date

import matplotlib
import polars as pl

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from research.goyal_saretto.panel import PANEL_PATH, RESULTS_DIR, STUDY_DIR  # noqa: E402

FIGURES_DIR = STUDY_DIR / "figures"
DECILES = 10
MONEYNESS_BAND = 0.025
WIDE_MONEYNESS_BAND = 0.05
SPREAD_FRACTIONS = (0.5, 0.75, 1.0)
STRATEGIES = ("straddle", "call_hedged", "put_hedged")


def compute_returns(panel_df: pl.DataFrame, spread_fraction: float = 0.0, side: str = "long") -> pl.DataFrame:
    """Hold-to-expiry returns for the three positions the paper studies.

    Entry is at the midpoint, or `spread_fraction` of the half-spread inside
    the touch: a long pays more, a short receives less. Only the entry pays
    the spread since expiry settles at intrinsic value.

    The straddle is one call plus one put. A delta-hedged call is long the
    call and short delta shares; the gain is scaled by the absolute initial
    investment |delta * S - C|, which is what makes its return the size the
    paper reports. The put is long the put and long |delta| shares.
    """
    sign = 1 if side == "long" else -1
    call_price = pl.col("call_mid") + sign * spread_fraction * (pl.col("call_ask") - pl.col("call_bid")) / 2
    put_price = pl.col("put_mid") + sign * spread_fraction * (pl.col("put_ask") - pl.col("put_bid")) / 2
    stock_move = pl.col("spot_at_expiry") - pl.col("spot_entry")
    call_hedged_gain = pl.col("call_payoff") - call_price - pl.col("call_delta") * stock_move
    call_hedged_investment = (pl.col("call_delta") * pl.col("spot_entry") - call_price).abs()
    put_hedged_gain = pl.col("put_payoff") - put_price - pl.col("put_delta") * stock_move
    put_hedged_investment = (put_price - pl.col("put_delta") * pl.col("spot_entry")).abs()
    return panel_df.with_columns(
        ((pl.col("call_payoff") + pl.col("put_payoff")) / (call_price + put_price) - 1).alias("straddle"),
        (call_hedged_gain / call_hedged_investment).alias("call_hedged"),
        (put_hedged_gain / put_hedged_investment).alias("put_hedged"),
        (pl.col("call_payoff") / call_price - 1).alias("call_naked"),
        (pl.col("put_payoff") / put_price - 1).alias("put_naked"),
        (pl.col("spot_at_expiry") / pl.col("spot_entry") - 1).alias("stock"),
    )


def assign_deciles(panel_df: pl.DataFrame, signal: str = "signal") -> pl.DataFrame:
    """Rank within each formation month; decile 10 holds the largest HV-IV."""
    rank = pl.col(signal).rank(method="ordinal").over("month")
    count = pl.len().over("month")
    return panel_df.with_columns(
        ((rank - 1) * DECILES // count + 1).cast(pl.Int32).alias("decile")
    )


def compute_portfolio_returns(panel_df: pl.DataFrame, columns: tuple[str, ...] = STRATEGIES) -> pl.DataFrame:
    """Equal-weighted monthly return per decile, plus the 10-1 spread."""
    by_decile = (
        panel_df.group_by("month", "decile")
        .agg(pl.len().alias("names"), *[pl.col(column).mean() for column in columns])
        .sort("month", "decile")
    )
    top = by_decile.filter(pl.col("decile") == DECILES)
    bottom = by_decile.filter(pl.col("decile") == 1)
    spread = top.join(bottom, on="month", suffix="_bottom").select(
        "month", pl.lit(0).cast(pl.Int32).alias("decile"),
        (pl.col("names") + pl.col("names_bottom")).alias("names"),
        *[(pl.col(column) - pl.col(f"{column}_bottom")).alias(column) for column in columns],
    )
    return pl.concat([by_decile, spread]).sort("month", "decile")


def compute_certainty_equivalent(returns: pl.Series, gamma: float) -> float:
    gross = 1 + returns
    if (gross <= 0).any():
        return math.nan
    return float((gross ** (1 - gamma)).mean() ** (1 / (1 - gamma)) - 1)


def summarize(returns: pl.Series) -> dict:
    clean = returns.drop_nulls()
    count = clean.len()
    mean = float(clean.mean())
    std = float(clean.std())
    return {
        "months": count,
        "mean": mean,
        "std": std,
        "t_stat": mean / std * math.sqrt(count) if std > 0 else math.nan,
        "min": float(clean.min()),
        "max": float(clean.max()),
        "sharpe": mean / std if std > 0 else math.nan,
        "ce_gamma3": compute_certainty_equivalent(clean, 3),
        "ce_gamma7": compute_certainty_equivalent(clean, 7),
    }


def label_decile(decile: int) -> str:
    return "10-1" if decile == 0 else str(decile)


def build_returns_table(portfolio_df: pl.DataFrame, columns: tuple[str, ...] = STRATEGIES) -> pl.DataFrame:
    """Table 3: one row per (strategy, decile)."""
    rows = []
    for column in columns:
        for decile in range(1, DECILES + 1):
            rows.append({"strategy": column, "decile": label_decile(decile)}
                        | summarize(portfolio_df.filter(pl.col("decile") == decile)[column]))
        rows.append({"strategy": column, "decile": "10-1"}
                    | summarize(portfolio_df.filter(pl.col("decile") == 0)[column]))
    return pl.DataFrame(rows)


def build_formation_table(panel_df: pl.DataFrame) -> pl.DataFrame:
    """Table 2: characteristics averaged within decile-month, then over months."""
    characteristics = {
        "hv_minus_iv": pl.col("hv") - pl.col("iv"),
        "hv": pl.col("hv"),
        "iv": pl.col("iv"),
        "signal": pl.col("signal"),
        "call_delta": pl.col("call_delta"),
        "put_delta": pl.col("put_delta"),
        "call_over_spot": pl.col("call_mid_formation") / pl.col("spot_formation"),
        "put_over_spot": pl.col("put_mid_formation") / pl.col("spot_formation"),
        "moneyness": pl.col("moneyness"),
        "return_1m": pl.col("return_1m"),
        "quoted_spread": (
            (pl.col("call_ask") - pl.col("call_bid") + pl.col("put_ask") - pl.col("put_bid"))
            / (pl.col("call_mid") + pl.col("put_mid"))
        ),
        "earnings_in_hold": pl.col("earnings_in_hold").cast(pl.Float64),
    }
    monthly = panel_df.group_by("month", "decile").agg(
        pl.len().alias("names"), *[expr.mean().alias(name) for name, expr in characteristics.items()]
    )
    return (
        monthly.group_by("decile")
        .agg(pl.col("names").mean(), *[pl.col(name).mean() for name in characteristics])
        .sort("decile")
    )


def build_cost_table(panel_df: pl.DataFrame) -> pl.DataFrame:
    """Table 8 panel A: the long-short straddle and the two legs, at midpoint
    and at three effective spreads. Decile 10 is bought, decile 1 is sold."""
    rows = []
    for fraction in (0.0, *SPREAD_FRACTIONS):
        long_df = compute_returns(panel_df, fraction, "long").filter(pl.col("decile") == DECILES)
        short_df = compute_returns(panel_df, fraction, "short").filter(pl.col("decile") == 1)
        legs = long_df.group_by("month").agg(pl.col("straddle").mean().alias("long")).join(
            short_df.group_by("month").agg(pl.col("straddle").mean().alias("short")), on="month"
        ).with_columns((pl.col("long") - pl.col("short")).alias("long_short"))
        for leg in ("long", "short", "long_short"):
            rows.append({"effective_spread": fraction, "leg": leg} | summarize(legs[leg]))
    return pl.DataFrame(rows)


def build_liquidity_table(panel_df: pl.DataFrame) -> pl.DataFrame:
    """Table 8 by option liquidity: split each month at the median relative
    quoted spread of the traded straddle, re-sort within each half."""
    spread = (
        (pl.col("call_ask") - pl.col("call_bid") + pl.col("put_ask") - pl.col("put_bid"))
        / (pl.col("call_mid") + pl.col("put_mid"))
    )
    halves = panel_df.with_columns(
        pl.when(spread <= spread.median().over("month")).then(pl.lit("liquid")).otherwise(pl.lit("illiquid")).alias("liquidity")
    )
    rows = []
    for group in ("liquid", "illiquid"):
        subset = assign_deciles(halves.filter(pl.col("liquidity") == group))
        for fraction in (0.0, *SPREAD_FRACTIONS):
            long_df = compute_returns(subset, fraction, "long").filter(pl.col("decile") == DECILES)
            short_df = compute_returns(subset, fraction, "short").filter(pl.col("decile") == 1)
            legs = long_df.group_by("month").agg(pl.col("straddle").mean().alias("long")).join(
                short_df.group_by("month").agg(pl.col("straddle").mean().alias("short")), on="month"
            ).with_columns((pl.col("long") - pl.col("short")).alias("long_short"))
            rows.append({"liquidity": group, "effective_spread": fraction} | summarize(legs["long_short"]))
    return pl.DataFrame(rows)


def build_robustness_table(panel_df: pl.DataFrame, base_df: pl.DataFrame) -> pl.DataFrame:
    """The paper's section 5.1 checks, each as the 10-1 row for all three
    strategies. `panel_df` is the unscreened panel (wide moneyness band);
    `base_df` is the baseline sample after the 0.975-1.025 screen."""
    variants = {
        "baseline": base_df,
        "2017-2021": base_df.filter(pl.col("month") < date(2022, 1, 1)),
        "2022-2025": base_df.filter(pl.col("month") >= date(2022, 1, 1)),
        "moneyness 0.95-1.05": panel_df,
        "no earnings in hold": base_df.filter(~pl.col("earnings_in_hold")),
        "iv from call only": base_df.with_columns(
            (pl.col("hv").log() - pl.col("call_iv_formation").log()).alias("signal")),
        "iv from put only": base_df.with_columns(
            (pl.col("hv").log() - pl.col("put_iv_formation").log()).alias("signal")),
        "sort on hv level": base_df.with_columns(pl.col("hv").alias("signal")),
        "sort on -iv level": base_df.with_columns((-pl.col("iv")).alias("signal")),
        "quintiles": None,
    }
    rows = []
    for name, variant_df in variants.items():
        if name == "quintiles":
            ranked = base_df.with_columns(
                ((pl.col("signal").rank(method="ordinal").over("month") - 1) * 5 // pl.len().over("month") + 1)
                .cast(pl.Int32).alias("decile")
            )
            portfolio = compute_portfolio_returns(
                compute_returns(ranked).with_columns(
                    pl.when(pl.col("decile") == 5).then(DECILES).otherwise(pl.col("decile")).alias("decile"))
            )
        else:
            portfolio = compute_portfolio_returns(compute_returns(assign_deciles(variant_df)))
        spread_df = portfolio.filter(pl.col("decile") == 0)
        for strategy in STRATEGIES:
            rows.append({"variant": name, "strategy": strategy} | summarize(spread_df[strategy]))
    return pl.DataFrame(rows)


def build_annual_table(portfolio_df: pl.DataFrame) -> pl.DataFrame:
    return (
        portfolio_df.filter(pl.col("decile") == 0)
        .group_by(pl.col("month").dt.year().alias("year"))
        .agg(pl.len().alias("months"), *[pl.col(column).mean() for column in STRATEGIES],
             pl.col("straddle").std().alias("straddle_std"))
        .sort("year")
    )


def plot_deciles(table_df: pl.DataFrame) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(13, 4), sharey=False)
    titles = {"straddle": "Straddles", "call_hedged": "Delta-hedged calls", "put_hedged": "Delta-hedged puts"}
    for axis, strategy in zip(axes, STRATEGIES):
        rows = table_df.filter(pl.col("strategy") == strategy, pl.col("decile") != "10-1")
        axis.bar(rows["decile"].to_list(), (rows["mean"] * 100).to_list(), color="#4c72b0")
        axis.axhline(0, color="black", linewidth=0.8)
        axis.set_title(titles[strategy])
        axis.set_xlabel("HV-IV decile (10 = cheapest options)")
        axis.set_ylabel("mean monthly return, %")
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "decile_returns.png", dpi=150)
    plt.close(fig)


def plot_long_short(portfolio_df: pl.DataFrame) -> None:
    spread_df = portfolio_df.filter(pl.col("decile") == 0).sort("month")
    fig, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True)
    months = spread_df["month"].to_list()
    axes[0].bar(months, (spread_df["straddle"] * 100).to_list(), width=20, color="#4c72b0")
    axes[0].axhline(0, color="black", linewidth=0.8)
    axes[0].set_ylabel("10-1 straddle return, %")
    axes[0].set_title("Long-short straddle portfolio, monthly")
    for strategy, color in zip(STRATEGIES, ("#4c72b0", "#dd8452", "#55a868")):
        axes[1].plot(months, (spread_df[strategy].cum_sum() * 100).to_list(), label=strategy, color=color)
    axes[1].axhline(0, color="black", linewidth=0.8)
    axes[1].set_ylabel("cumulative sum of monthly returns, %")
    axes[1].set_title("Cumulative 10-1 returns, constant notional each month")
    axes[1].legend()
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "long_short.png", dpi=150)
    plt.close(fig)


def plot_signal(panel_df: pl.DataFrame) -> None:
    monthly = panel_df.group_by("month").agg(
        pl.col("hv").mean(), pl.col("iv").mean(), pl.len().alias("names")).sort("month")
    fig, axis = plt.subplots(figsize=(11, 4))
    months = monthly["month"].to_list()
    axis.plot(months, (monthly["hv"] * 100).to_list(), label="mean HV (trailing 12m)", color="#4c72b0")
    axis.plot(months, (monthly["iv"] * 100).to_list(), label="mean ATM IV (next monthly)", color="#dd8452")
    axis.set_ylabel("annualized vol, %")
    axis.set_title("Cross-sectional average HV and IV on formation days")
    axis.legend()
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "hv_iv.png", dpi=150)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--band", type=float, default=MONEYNESS_BAND, help="max |strike/spot - 1| at formation")
    args = parser.parse_args()
    FIGURES_DIR.mkdir(exist_ok=True)

    panel_df = pl.read_parquet(PANEL_PATH).filter((pl.col("moneyness") - 1).abs() <= WIDE_MONEYNESS_BAND)
    base_df = assign_deciles(panel_df.filter((pl.col("moneyness") - 1).abs() <= args.band))
    returns_df = compute_returns(base_df)
    portfolio_df = compute_portfolio_returns(returns_df, (*STRATEGIES, "call_naked", "put_naked", "stock"))

    returns_df.write_csv(RESULTS_DIR / "stock_months.csv")
    portfolio_df.write_csv(RESULTS_DIR / "portfolio_monthly.csv")
    build_returns_table(portfolio_df, (*STRATEGIES, "call_naked", "put_naked", "stock")).write_csv(RESULTS_DIR / "table3_returns.csv")
    build_formation_table(returns_df).write_csv(RESULTS_DIR / "table2_formation.csv")
    build_cost_table(base_df).write_csv(RESULTS_DIR / "table8_costs.csv")
    build_liquidity_table(base_df.drop("decile")).write_csv(RESULTS_DIR / "table8_liquidity.csv")
    build_robustness_table(panel_df, base_df.drop("decile")).write_csv(RESULTS_DIR / "robustness.csv")
    build_annual_table(portfolio_df).write_csv(RESULTS_DIR / "annual.csv")
    (
        base_df.group_by("month").agg(pl.len().alias("names"), pl.col("symbol").n_unique().alias("symbols"))
        .sort("month").write_csv(RESULTS_DIR / "coverage.csv")
    )

    table_df = build_returns_table(portfolio_df)
    plot_deciles(table_df)
    plot_long_short(portfolio_df)
    plot_signal(base_df)

    pl.Config.set_tbl_rows(40)
    pl.Config.set_float_precision(4)
    print(f"{base_df.height} stock-months, {base_df['symbol'].n_unique()} symbols, "
          f"{base_df['month'].n_unique()} months, "
          f"{base_df.group_by('month').len()['len'].mean():.0f} names per month")
    print(table_df.select("strategy", "decile", "mean", "std", "t_stat", "sharpe", "ce_gamma3", "min", "max"))


if __name__ == "__main__":
    main()
