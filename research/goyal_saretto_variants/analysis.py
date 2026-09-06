"""Modifications of the Goyal-Saretto sort, each scored the same way.

The baseline study found the log(HV/IV) sort on S&P 500 names to be mostly
an earnings-calendar sort with no return spread. The variants here attack
that from three sides: a cleaner signal, a different reference point for
"normal" IV, and a different exit. Every variant reports the 10-1 spread for
straddles and delta-hedged calls and puts at the midpoint, and the straddle
spread again at an effective spread of half the quoted spread.

Run from the project root, after `extras.py`:

    uv run python -m research.goyal_saretto_variants.analysis
"""

import matplotlib
import polars as pl

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from research.goyal_saretto.analysis import (  # noqa: E402
    DECILES, MONEYNESS_BAND, STRATEGIES, assign_deciles, compute_portfolio_returns, compute_returns, summarize,
)
from research.goyal_saretto.panel import PANEL_PATH  # noqa: E402
from research.goyal_saretto_variants.extras import EXTRAS_PATH, RESULTS_DIR, STUDY_DIR  # noqa: E402

FIGURES_DIR = STUDY_DIR / "figures"
COST_FRACTIONS = (0.25, 0.5, 1.0)
PLOTTED_VARIANTS = (
    "baseline", "hv 21d", "ex-earnings hv, no earnings hold", "iv vs own 12m mean",
    "baseline, zero-delta straddle", "baseline, exit after 10 sessions", "combined, ex-earnings, exit after 10",
)


def load_panel() -> pl.DataFrame:
    panel_df = pl.read_parquet(PANEL_PATH).filter((pl.col("moneyness") - 1).abs() <= MONEYNESS_BAND)
    extras_df = pl.read_parquet(EXTRAS_PATH).drop("formation_day", "expiration", "strike")
    return panel_df.join(extras_df, on=["symbol", "month"], how="left")


def make_early_exit(horizon: int):
    """Return function that closes at the midpoint `horizon` sessions after
    entry instead of at expiry.

    The exit crosses the spread too, priced at the entry day's relative
    spread since the exit-day touch was not extracted.
    """
    call_mid_exit = pl.col(f"call_mid_{horizon}")
    put_mid_exit = pl.col(f"put_mid_{horizon}")
    spot_exit = pl.col(f"spot_{horizon}")

    def compute_early_exit_returns(panel_df: pl.DataFrame, spread_fraction: float = 0.0, side: str = "long") -> pl.DataFrame:
        sign = 1 if side == "long" else -1
        call_half = (pl.col("call_ask") - pl.col("call_bid")) / 2
        put_half = (pl.col("put_ask") - pl.col("put_bid")) / 2
        call_price = pl.col("call_mid") + sign * spread_fraction * call_half
        put_price = pl.col("put_mid") + sign * spread_fraction * put_half
        call_exit = call_mid_exit - sign * spread_fraction * call_half * call_mid_exit / pl.col("call_mid")
        put_exit = put_mid_exit - sign * spread_fraction * put_half * put_mid_exit / pl.col("put_mid")
        stock_move = spot_exit - pl.col("spot_entry")
        call_gain = call_exit - call_price - pl.col("call_delta") * stock_move
        put_gain = put_exit - put_price - pl.col("put_delta") * stock_move
        return panel_df.filter(call_mid_exit.is_not_null(), put_mid_exit.is_not_null()).with_columns(
            ((call_exit + put_exit) / (call_price + put_price) - 1).alias("straddle"),
            (call_gain / (pl.col("call_delta") * pl.col("spot_entry") - call_price).abs()).alias("call_hedged"),
            (put_gain / (put_price - pl.col("put_delta") * pl.col("spot_entry")).abs()).alias("put_hedged"),
        )

    return compute_early_exit_returns


def compute_zero_delta_returns(panel_df: pl.DataFrame, spread_fraction: float = 0.0, side: str = "long") -> pl.DataFrame:
    """The paper's footnote-10 zero-delta straddle: hedge the net delta of
    the pair with stock at entry, scaled by the straddle price."""
    returns_df = compute_returns(panel_df, spread_fraction, side)
    sign = 1 if side == "long" else -1
    call_price = pl.col("call_mid") + sign * spread_fraction * (pl.col("call_ask") - pl.col("call_bid")) / 2
    put_price = pl.col("put_mid") + sign * spread_fraction * (pl.col("put_ask") - pl.col("put_bid")) / 2
    net_delta = pl.col("call_delta") + pl.col("put_delta")
    hedge = net_delta * (pl.col("spot_at_expiry") - pl.col("spot_entry"))
    return returns_df.with_columns(
        ((pl.col("call_payoff") + pl.col("put_payoff") - hedge) / (call_price + put_price) - 1).alias("straddle")
    )


def rank_average(*columns: str) -> pl.Expr:
    return pl.sum_horizontal([pl.col(column).rank().over("month") / pl.len().over("month") for column in columns])


def build_variants(panel_df: pl.DataFrame) -> dict:
    """Name -> (panel with a `signal` column, return function)."""
    log_ratio = lambda numerator, denominator: (pl.col(numerator).log() - pl.col(denominator).log())  # noqa: E731
    no_earnings = panel_df.filter(~pl.col("earnings_in_hold"))
    with_iv_history = panel_df.filter(pl.col("iv_mean_12m").is_not_null(), pl.col("iv_std_12m") > 0)
    ex_earnings = no_earnings.filter(pl.col("hv_ex_earnings").is_not_null()).with_columns(
        log_ratio("hv_ex_earnings", "iv").alias("signal"))
    iv_history = with_iv_history.with_columns(log_ratio("iv_mean_12m", "iv").alias("signal"))
    iv_zscore = with_iv_history.with_columns(((pl.col("iv_mean_12m") - pl.col("iv")) / pl.col("iv_std_12m")).alias("signal"))
    combined = with_iv_history.with_columns(
        log_ratio("hv", "iv").alias("hv_signal"), log_ratio("iv_mean_12m", "iv").alias("iv_signal")
    ).with_columns(rank_average("hv_signal", "iv_signal").alias("signal"))
    combined_clean = (
        no_earnings.filter(pl.col("iv_mean_12m").is_not_null(), pl.col("hv_ex_earnings").is_not_null())
        .with_columns(log_ratio("hv_ex_earnings", "iv").alias("hv_signal"), log_ratio("iv_mean_12m", "iv").alias("iv_signal"))
        .with_columns(rank_average("hv_signal", "iv_signal").alias("signal"))
    )
    exit_10 = make_early_exit(10)
    return {
        "baseline": (panel_df, compute_returns),
        "hv 63d": (panel_df.filter(pl.col("hv_63").is_not_null()).with_columns(log_ratio("hv_63", "iv").alias("signal")), compute_returns),
        "hv 21d": (panel_df.filter(pl.col("hv_21").is_not_null()).with_columns(log_ratio("hv_21", "iv").alias("signal")), compute_returns),
        "ex-earnings hv, no earnings hold": (ex_earnings, compute_returns),
        "iv vs own 12m mean": (iv_history, compute_returns),
        "iv z-score vs own 12m": (iv_zscore, compute_returns),
        "rank(hv/iv) + rank(iv history)": (combined, compute_returns),
        "combined, ex-earnings": (combined_clean, compute_returns),
        "baseline, zero-delta straddle": (panel_df, compute_zero_delta_returns),
        "baseline, exit after 3 sessions": (panel_df, make_early_exit(3)),
        "baseline, exit after 5 sessions": (panel_df, make_early_exit(5)),
        "baseline, exit after 10 sessions": (panel_df, exit_10),
        "baseline, exit after 15 sessions": (panel_df, make_early_exit(15)),
        "ex-earnings, exit after 10": (ex_earnings, exit_10),
        "iv vs own 12m mean, exit after 10": (iv_history, exit_10),
        "combined, ex-earnings, exit after 10": (combined_clean, exit_10),
    }


def score_variant(name: str, variant_df: pl.DataFrame, compute) -> tuple[dict, pl.DataFrame]:
    ranked = assign_deciles(variant_df)
    portfolio_df = compute_portfolio_returns(compute(ranked))
    spread_df = portfolio_df.filter(pl.col("decile") == 0)
    row = {"variant": name, "months": spread_df.height,
           "names_per_month": float(ranked.group_by("month").len()["len"].mean())}
    for strategy in STRATEGIES:
        stats = summarize(spread_df[strategy])
        row |= {f"{strategy}_mean": stats["mean"], f"{strategy}_t": stats["t_stat"], f"{strategy}_sharpe": stats["sharpe"]}
    decile_means = portfolio_df.filter(pl.col("decile") > 0).group_by("decile").agg(pl.col("straddle").mean()).sort("decile")
    row["straddle_decile_rank_corr"] = decile_means.select(
        pl.corr(pl.col("decile").cast(pl.Float64), pl.col("straddle"), method="spearman")
    ).item()
    for fraction in COST_FRACTIONS:
        legs = compute_costed_legs(ranked, compute, fraction)
        tag = f"cost{int(fraction * 100)}"
        stats = summarize(legs["long"] - legs["short"])
        row |= {f"straddle_{tag}_mean": stats["mean"], f"straddle_{tag}_t": stats["t_stat"]}
        stats = summarize(-legs["short"])
        row |= {f"short_d1_{tag}_mean": stats["mean"], f"short_d1_{tag}_t": stats["t_stat"]}
    return row, spread_df.select("month", *STRATEGIES).with_columns(pl.lit(name).alias("variant"))


def compute_costed_legs(ranked: pl.DataFrame, compute, fraction: float) -> pl.DataFrame:
    """Monthly mean straddle return of the bought decile 10 and the sold
    decile 1, each priced `fraction` of the half-spread inside the touch."""
    long_df = compute(ranked, fraction, "long").filter(pl.col("decile") == DECILES)
    short_df = compute(ranked, fraction, "short").filter(pl.col("decile") == 1)
    return long_df.group_by("month").agg(pl.col("straddle").mean().alias("long")).join(
        short_df.group_by("month").agg(pl.col("straddle").mean().alias("short")), on="month")


def build_decile_table(variants: dict) -> pl.DataFrame:
    rows = []
    for name, (variant_df, compute) in variants.items():
        portfolio_df = compute_portfolio_returns(compute(assign_deciles(variant_df)))
        for decile in range(1, DECILES + 1):
            subset = portfolio_df.filter(pl.col("decile") == decile)
            rows.append({"variant": name, "decile": decile}
                        | {strategy: float(subset[strategy].mean()) for strategy in STRATEGIES})
    return pl.DataFrame(rows)


def plot_summary(summary_df: pl.DataFrame, spreads_df: pl.DataFrame) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(15, 6), gridspec_kw={"width_ratios": [1, 1.4]})
    names = summary_df["variant"].to_list()
    positions = range(len(names))
    axes[0].barh(list(positions), (summary_df["straddle_mean"] * 100).to_list(), color="#4c72b0", label="midpoint")
    axes[0].barh(list(positions), (summary_df["straddle_cost50_mean"] * 100).to_list(), height=0.4, color="#dd8452", label="50% of quoted spread")
    axes[0].set_yticks(list(positions))
    axes[0].set_yticklabels(names, fontsize=8)
    axes[0].invert_yaxis()
    axes[0].axvline(0, color="black", linewidth=0.8)
    axes[0].set_xlabel("10-1 straddle, mean monthly return %")
    axes[0].legend(loc="lower right")
    for name in PLOTTED_VARIANTS:
        subset = spreads_df.filter(pl.col("variant") == name).sort("month")
        axes[1].plot(subset["month"].to_list(), (subset["straddle"].cum_sum() * 100).to_list(), label=name, linewidth=1.2)
    axes[1].axhline(0, color="black", linewidth=0.8)
    axes[1].set_ylabel("cumulative 10-1 straddle return, %")
    axes[1].legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "variants.png", dpi=150)
    plt.close(fig)


def main() -> None:
    FIGURES_DIR.mkdir(exist_ok=True)
    panel_df = load_panel()
    variants = build_variants(panel_df)
    rows, spreads = [], []
    for name, (variant_df, compute) in variants.items():
        row, spread_df = score_variant(name, variant_df, compute)
        rows.append(row)
        spreads.append(spread_df)
    summary_df = pl.DataFrame(rows)
    spreads_df = pl.concat(spreads)
    summary_df.write_csv(RESULTS_DIR / "variants.csv")
    spreads_df.write_csv(RESULTS_DIR / "variants_monthly.csv")
    build_decile_table(variants).write_csv(RESULTS_DIR / "variants_deciles.csv")
    plot_summary(summary_df, spreads_df)
    pl.Config.set_tbl_rows(40)
    pl.Config.set_tbl_cols(20)
    pl.Config.set_tbl_width_chars(220)
    pl.Config.set_float_precision(3)
    print(summary_df.select(
        "variant", "names_per_month", "straddle_mean", "straddle_t", "straddle_cost25_mean", "straddle_cost50_mean",
        "straddle_decile_rank_corr", "call_hedged_mean", "call_hedged_t", "put_hedged_mean", "put_hedged_t",
    ))
    print(summary_df.select(
        "variant", "short_d1_cost25_mean", "short_d1_cost25_t", "short_d1_cost50_mean", "short_d1_cost50_t",
        "short_d1_cost100_mean", "short_d1_cost100_t",
    ))


if __name__ == "__main__":
    main()
