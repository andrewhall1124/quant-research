"""Regenerate every figure and table in REPORT.md from the cached panel.

    uv run python -m research.iv_zscore_reversion.panel     # once, ~25s
    uv run python -m research.iv_zscore_reversion.analysis
"""

from __future__ import annotations

from pathlib import Path

import matplotlib
import numpy as np
import polars as pl

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from research.iv_zscore_reversion import portfolio as pf  # noqa: E402
from research.iv_zscore_reversion.panel import load_panel  # noqa: E402

STUDY_DIR = Path(__file__).resolve().parent
FIGURES_DIR = STUDY_DIR / "figures"
RESULTS_DIR = STUDY_DIR / "results"

INK = "#1b2430"
LONG_COLOUR = "#2c7fb8"
SHORT_COLOUR = "#d95f02"
NET_COLOUR = "#7a7f87"
ACCENT = "#4d9221"


def style_axes(ax, title: str, xlabel: str = "", ylabel: str = "") -> None:
    ax.set_title(title, fontsize=11, color=INK, loc="left", pad=10)
    ax.set_xlabel(xlabel, fontsize=9, color=INK)
    ax.set_ylabel(ylabel, fontsize=9, color=INK)
    ax.tick_params(labelsize=8, colors=INK)
    ax.grid(alpha=0.25, linewidth=0.6)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)


def save(fig, name: str) -> None:
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / name, dpi=150, facecolor="white")
    plt.close(fig)
    print(f"  figures/{name}")


def write_csv(frame_df: pl.DataFrame, name: str) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    frame_df.write_csv(RESULTS_DIR / name)
    print(f"  results/{name}")


def daily_mean_tstat(frame_df: pl.DataFrame, value: str, by: list[str]) -> pl.DataFrame:
    """Average a per-contract quantity, with a t-statistic that is not a lie.

    ~400 names share every date and every market shock, so pooling contracts
    and taking a naive standard error overstates significance by a large
    factor. Collapsing to a daily cross-sectional mean first, then testing that
    time series with HAC errors, prices the cross-sectional dependence the same
    way a Driscoll-Kraay correction does.
    """
    daily = (
        frame_df.group_by([*by, "date"])
        .agg(pl.col(value).mean().alias("daily_mean"), pl.len().alias("n"))
        .sort([*by, "date"])
    )
    rows = []
    for keys, group in daily.group_by(by, maintain_order=True):
        mean, tstat = pf.newey_west_tstat(group["daily_mean"].to_numpy())
        rows.append(
            dict(zip(by, keys))
            | {
                "mean": mean,
                "tstat": tstat,
                "days": group.height,
                "observations": int(group["n"].sum()),
            }
        )
    return pl.DataFrame(rows)


# --------------------------------------------------------------------------
# Figures
# --------------------------------------------------------------------------


def plot_equity_curves(daily_by_lag: dict[int, pl.DataFrame]) -> None:
    """Gross and net on separate scales.

    Sharing one axis would be honest but unreadable: the cost of crossing the
    spread is two orders of magnitude larger than the edge, so a single panel
    shows a flat line and a cliff.
    """
    fig, (gross_ax, net_ax, dd_ax) = plt.subplots(
        3, 1, figsize=(10, 9), sharex=True, height_ratios=[2, 2, 1.2]
    )
    same_day, lagged = daily_by_lag[0], daily_by_lag[1]

    gross_ax.plot(
        same_day["date"], same_day["gross_pnl"].cum_sum() / 1e6,
        color=LONG_COLOUR, lw=1.6, label="Same-close execution (lag 0)",
    )
    gross_ax.plot(
        lagged["date"], lagged["gross_pnl"].cum_sum() / 1e6,
        color=ACCENT, lw=1.6, label="One-session implementation lag (lag 1)",
    )
    gross_ax.axhline(0, color=INK, lw=0.8)
    style_axes(
        gross_ax,
        "Cumulative GROSS P&L, $10,000 of vega per side\n"
        "Nearly all of the same-close result is the bid-ask bounce in the print that made the signal",
        ylabel="Cumulative P&L ($m)",
    )
    gross_ax.legend(fontsize=8, frameon=False, loc="upper left")

    for daily, lag, style in ((same_day, 0, "-"), (lagged, 1, "--")):
        for column, fraction, colour in (
            ("net_pnl_25", 25, SHORT_COLOUR), ("net_pnl_50", 50, NET_COLOUR)
        ):
            net_ax.plot(
                daily["date"], daily[column].cum_sum() / 1e6,
                color=colour, lw=1.2, ls=style,
                label=f"Net of {fraction}% of quoted spread (lag {lag})",
            )
    net_ax.axhline(0, color=INK, lw=0.8)
    style_axes(
        net_ax,
        "Cumulative NET P&L — note the axis is 40x the panel above",
        ylabel="Cumulative P&L ($m)",
    )
    net_ax.legend(fontsize=8, frameon=False, loc="lower left")

    for daily, colour, label in (
        (same_day, LONG_COLOUR, "lag 0"), (lagged, ACCENT, "lag 1")
    ):
        running = daily["gross_pnl"].cum_sum().to_numpy()
        drawdown = running - np.maximum.accumulate(running)
        # Scaled by each curve's own terminal P&L, because the two differ by a
        # factor of 27 in size and a dollar axis would hide the smaller one.
        dd_ax.plot(
            daily["date"], drawdown / running[-1] * 100,
            color=colour, lw=1.0, label=f"Gross drawdown, {label}",
        )
    style_axes(
        dd_ax,
        "Gross drawdown, as a share of each curve's own total P&L",
        xlabel="Date", ylabel="Drawdown (% of total)",
    )
    dd_ax.legend(fontsize=8, frameon=False, loc="lower left")
    save(fig, "fig01_equity_curves.png")


def plot_lag_decay(lag_df: pl.DataFrame) -> None:
    fig, (left, right) = plt.subplots(1, 2, figsize=(10, 4))
    left.bar(lag_df["signal_lag"], lag_df["gross_sharpe"], color=LONG_COLOUR, width=0.6)
    left.axhline(0, color=INK, lw=0.8)
    style_axes(
        left,
        "Gross Sharpe against implementation lag",
        xlabel="Sessions between signal and entry", ylabel="Annualised Sharpe (gross)",
    )
    for lag, sharpe in zip(lag_df["signal_lag"], lag_df["gross_sharpe"]):
        left.text(
            lag, sharpe, f"{sharpe:.2f}", ha="center", fontsize=8,
            va="bottom" if sharpe >= 0 else "top",
        )

    breakeven = lag_df["breakeven_spread_fraction"].to_numpy() * 100
    right.bar(lag_df["signal_lag"], breakeven, color=SHORT_COLOUR, width=0.6)
    right.axhline(0, color=INK, lw=0.8)
    for lag, value in zip(lag_df["signal_lag"], breakeven):
        right.text(
            lag, value, f"{value:.2f}%", ha="center", fontsize=8,
            va="bottom" if value >= 0 else "top",
        )
    # A linear axis, not log: half the values are negative now, and a log axis
    # would silently drop them. The 50% a real crossing costs is an order of
    # magnitude off this scale, so it is stated rather than drawn.
    style_axes(
        right,
        "Share of the quoted spread the strategy can pay\n"
        "A real crossing costs ~50%, far above this axis",
        xlabel="Sessions between signal and entry",
        ylabel="Break-even spread fraction (%)",
    )
    save(fig, "fig02_lag_decay.png")


def plot_decile_pnl(decile_df: pl.DataFrame) -> None:
    fig, (left, right) = plt.subplots(1, 2, figsize=(10, 4), sharey=True)
    # Honest specification on the left, so the reader meets the real answer
    # before the artefact.
    for ax, lag, colour, note in (
        (left, 1, ACCENT, "Lag 1 (honest): signal at t, entry at t+1"),
        (right, 0, LONG_COLOUR, "Lag 0: signal and entry at the same close"),
    ):
        subset = decile_df.filter(pl.col("signal_lag") == lag).sort("iv_decile")
        ax.bar(subset["iv_decile"], subset["mean"], color=colour, width=0.7)
        ax.axhline(0, color=INK, lw=0.8)
        style_axes(
            ax,
            note,
            xlabel="Decile of rolling 60-session IV z-score (0 = cheapest)",
            ylabel="Vol points earned per day" if lag == 1 else "",
        )
        ax.set_xticks(range(10))
    fig.suptitle(
        "Delta-hedged P&L by IV z-score decile: the gradient is entirely the "
        "same-close artefact",
        fontsize=11, color=INK, x=0.012, ha="left",
    )
    save(fig, "fig03_decile_pnl.png")


def plot_iv_reversion(reversion_df: pl.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(7, 4.2))
    subset = reversion_df.sort("iv_decile")
    ax.bar(subset["iv_decile"], subset["mean"], color=SHORT_COLOUR, width=0.7)
    ax.axhline(0, color=INK, lw=0.8)
    style_axes(
        ax,
        "Next-session change in the straddle's implied vol, by z-score decile\n"
        "Cheap vol rises and rich vol falls — the mean reversion the strategy is named for",
        xlabel="Decile of rolling 60-session IV z-score (0 = cheapest)",
        ylabel="Mean next-day change in IV (vol points)",
    )
    ax.set_xticks(range(10))
    save(fig, "fig04_iv_reversion.png")


def plot_spread_quintiles(quintile_df: pl.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(7.5, 4.2))
    width = 0.38
    positions = np.arange(5)
    for offset, lag, colour, label in (
        (-width / 2, 0, LONG_COLOUR, "Lag 0 (same close)"),
        (width / 2, 1, ACCENT, "Lag 1 (next close)"),
    ):
        subset = quintile_df.filter(pl.col("signal_lag") == lag).sort("spread_quintile")
        ax.bar(
            positions + offset, subset["long_short"], width=width,
            color=colour, label=label,
        )
    ax.axhline(0, color=INK, lw=0.8)
    style_axes(
        ax,
        "Long-minus-short P&L by how wide the straddle is quoted\n"
        "The same-close result grows with the spread; the lagged one does not",
        xlabel="Quintile of relative bid-ask spread (0 = tightest)",
        ylabel="Vol points per day, decile 0 minus decile 9",
    )
    ax.set_xticks(positions)
    ax.legend(fontsize=8, frameon=False)
    save(fig, "fig05_spread_quintiles.png")


def plot_costs(annual_df: pl.DataFrame) -> None:
    fig, (left, right) = plt.subplots(1, 2, figsize=(10, 4))
    years = annual_df["year"].to_numpy()
    width = 0.38
    left.bar(
        years - width / 2, annual_df["gross_pnl_per_day"] / 1e3, width=width,
        color=LONG_COLOUR, label="Gross P&L per day",
    )
    left.bar(
        years + width / 2, annual_df["spread_cost_full_per_day"] / 1e3, width=width,
        color=SHORT_COLOUR, label="Round-trip quoted spread per day",
    )
    left.set_yscale("log")
    style_axes(
        left,
        "What the book earns against what it must cross (lag 0)",
        xlabel="Year", ylabel="$ thousands per day (log scale)",
    )
    left.legend(fontsize=8, frameon=False)

    right.bar(
        years, annual_df["breakeven_spread_fraction"] * 100,
        color=NET_COLOUR, width=0.7,
    )
    right.axhline(50, color=SHORT_COLOUR, lw=1.2, ls="--")
    right.text(
        years.max(), 52, "half the quoted spread", fontsize=8,
        color=SHORT_COLOUR, ha="right", va="bottom",
    )
    right.set_yscale("log")
    style_axes(
        right,
        "Break-even spread fraction by year (lag 0)",
        xlabel="Year", ylabel="Break-even fraction of quoted spread (%)",
    )
    save(fig, "fig06_costs.png")


def plot_annual_sharpe(annual_by_lag: pl.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(8, 4.2))
    years = sorted(annual_by_lag["year"].unique().to_list())
    width = 0.38
    for offset, lag, colour, label in (
        (-width / 2, 0, LONG_COLOUR, "Lag 0, gross"),
        (width / 2, 1, ACCENT, "Lag 1, gross"),
    ):
        subset = annual_by_lag.filter(pl.col("signal_lag") == lag).sort("year")
        ax.bar(
            np.array(subset["year"]) + offset, subset["gross_sharpe"],
            width=width, color=colour, label=label,
        )
    ax.axhline(0, color=INK, lw=0.8)
    style_axes(
        ax,
        "Gross Sharpe by calendar year",
        xlabel="Year", ylabel="Annualised Sharpe (gross)",
    )
    ax.set_xticks(years)
    ax.legend(fontsize=8, frameon=False)
    save(fig, "fig07_annual_sharpe.png")


def plot_earnings(earnings_df: pl.DataFrame, exclusion_df: pl.DataFrame) -> None:
    fig, (left, right) = plt.subplots(1, 2, figsize=(10, 4))
    subset = earnings_df.sort("iv_decile")
    left.bar(subset["iv_decile"], subset["mean"], color=SHORT_COLOUR, width=0.7)
    style_axes(
        left,
        "Days to the next earnings announcement, by decile\n"
        "The rich-vol decile is the pre-earnings decile",
        xlabel="Decile of IV z-score (0 = cheapest)",
        ylabel="Mean calendar days to next report",
    )
    left.set_xticks(range(10))

    labels = exclusion_df["label"].to_list()
    positions = np.arange(len(labels))
    right.bar(positions, exclusion_df["gross_sharpe"], color=ACCENT, width=0.6)
    right.axhline(0, color=INK, lw=0.8)
    style_axes(
        right,
        "Lag-1 gross Sharpe with pre-earnings names removed",
        ylabel="Annualised Sharpe (gross)",
    )
    right.set_xticks(positions)
    right.set_xticklabels(labels, fontsize=8)
    save(fig, "fig08_earnings.png")


def plot_robustness(grid_df: pl.DataFrame) -> None:
    windows = sorted(grid_df["window"].unique().to_list())
    deciles = sorted(grid_df["decile"].unique().to_list())
    matrix = np.full((len(deciles), len(windows)), np.nan)
    for row in grid_df.iter_rows(named=True):
        matrix[deciles.index(row["decile"]), windows.index(row["window"])] = (
            row["gross_sharpe"]
        )
    fig, ax = plt.subplots(figsize=(7, 4))
    # Sequential from zero, not diverging: every cell is positive, and a
    # diverging map centred on zero would read as if some cells were negative.
    top = np.nanmax(matrix)
    image = ax.imshow(matrix, cmap="YlOrBr", vmin=0, vmax=top, aspect="auto")
    for row in range(matrix.shape[0]):
        for column in range(matrix.shape[1]):
            value = matrix[row, column]
            ax.text(
                column, row, f"{value:.2f}",
                ha="center", va="center", fontsize=9,
                color="white" if value > 0.7 * top else INK,
            )
    ax.set_xticks(range(len(windows)), [str(window) for window in windows])
    ax.set_yticks(range(len(deciles)), [f"{int(d * 100)}%" for d in deciles])
    style_axes(
        ax,
        "Lag-1 gross Sharpe across z-score window and portfolio cut",
        xlabel="Rolling window (sessions)", ylabel="Fraction taken on each side",
    )
    ax.grid(False)
    fig.colorbar(image, ax=ax, shrink=0.8, label="Gross Sharpe")
    save(fig, "fig09_robustness.png")


def plot_coverage(coverage_df: pl.DataFrame) -> None:
    fig, (top, bottom) = plt.subplots(2, 1, figsize=(9, 5.5), sharex=True)
    top.plot(coverage_df["date"], coverage_df["names_today"], color=LONG_COLOUR, lw=1.0)
    style_axes(
        top,
        "Names carrying a tradable ~30-dte ATM straddle and a 60-session z-score",
        ylabel="Names in the cross-section",
    )
    bottom.plot(
        coverage_df["date"], coverage_df["mean_relative_spread"] * 100,
        color=SHORT_COLOUR, lw=1.0,
    )
    style_axes(
        bottom,
        "Mean quoted straddle spread as a share of mid",
        xlabel="Date", ylabel="Relative spread (%)",
    )
    save(fig, "fig10_coverage.png")


# --------------------------------------------------------------------------
# Runs
# --------------------------------------------------------------------------


def run_lag_sweep(panel_df: pl.DataFrame) -> tuple[dict, dict, pl.DataFrame]:
    daily_by_lag, prepared_by_lag, rows = {}, {}, []
    for lag in range(6):
        config = pf.Config(signal_lag=lag, label=f"lag{lag}")
        prepared = pf.prepare(panel_df, config)
        _, daily, stats = pf.run(panel_df, config, prepared)
        prepared_by_lag[lag] = prepared
        daily_by_lag[lag] = daily
        rows.append({"signal_lag": lag} | stats)
    return daily_by_lag, prepared_by_lag, pl.DataFrame(rows)


def annual_stats(panel_df: pl.DataFrame, prepared_by_lag: dict) -> pl.DataFrame:
    rows = []
    years = sorted(prepared_by_lag[0]["date"].dt.year().unique().to_list())
    for lag in (0, 1):
        for year in years:
            config = pf.Config(signal_lag=lag, years=(year,), label=f"lag{lag}-{year}")
            _, _, stats = pf.run(panel_df, config, prepared_by_lag[lag])
            rows.append({"signal_lag": lag, "year": year} | stats)
    return pl.DataFrame(rows)


def decile_stats(prepared_by_lag: dict) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Per-decile P&L, IV reversion and earnings distance across the full cross-section."""
    frames = []
    for lag, prepared in prepared_by_lag.items():
        if lag > 1:
            continue
        with_decile = prepared.with_columns(
            (
                (pl.col("signal").rank("ordinal").over("date") - 1)
                / pl.len().over("date") * 10
            ).floor().clip(0, 9).cast(pl.Int32).alias("iv_decile")
        ).with_columns(pl.lit(lag).alias("signal_lag"))
        frames.append(with_decile)
    pooled = pl.concat(frames, how="vertical_relaxed")

    pnl_df = daily_mean_tstat(pooled, "pnl_per_vega", ["signal_lag", "iv_decile"])
    reversion_df = daily_mean_tstat(
        pooled.filter(pl.col("signal_lag") == 0), "iv_change_points", ["iv_decile"]
    )
    earnings_df = daily_mean_tstat(
        pooled.filter(pl.col("signal_lag") == 0, pl.col("days_to_earnings").is_not_null()),
        "days_to_earnings", ["iv_decile"],
    )
    return pnl_df, reversion_df, earnings_df


def spread_quintile_stats(prepared_by_lag: dict) -> pl.DataFrame:
    """Is the same-close edge just the width of the quote it is measured in?"""
    rows = []
    for lag in (0, 1):
        prepared = prepared_by_lag[lag]
        with_buckets = prepared.with_columns(
            (
                (pl.col("relative_spread").rank("ordinal").over("date") - 1)
                / pl.len().over("date") * 5
            ).floor().clip(0, 4).cast(pl.Int32).alias("spread_quintile"),
            (
                (pl.col("signal").rank("ordinal").over("date") - 1)
                / pl.len().over("date") * 10
            ).floor().clip(0, 9).cast(pl.Int32).alias("iv_decile"),
        ).filter(pl.col("iv_decile").is_in([0, 9]))
        daily = (
            with_buckets.group_by("spread_quintile", "iv_decile", "date")
            .agg(pl.col("pnl_per_vega").mean().alias("bucket_mean"))
            .pivot(on="iv_decile", index=["spread_quintile", "date"], values="bucket_mean")
            .drop_nulls()
            .with_columns((pl.col("0") - pl.col("9")).alias("long_short"))
        )
        for quintile in range(5):
            series = daily.filter(pl.col("spread_quintile") == quintile)["long_short"]
            mean, tstat = pf.newey_west_tstat(series.to_numpy())
            rows.append(
                {
                    "signal_lag": lag, "spread_quintile": quintile,
                    "long_short": mean, "tstat": tstat, "days": series.len(),
                }
            )
    return pl.DataFrame(rows)


def earnings_exclusion_stats(panel_df: pl.DataFrame, prepared_by_lag: dict) -> pl.DataFrame:
    rows = []
    for exclude, label in ((None, "all names"), (7, "no report ≤7d"), (35, "no report ≤35d")):
        config = pf.Config(
            signal_lag=1, exclude_earnings_within=exclude, label=label, min_names=50
        )
        _, _, stats = pf.run(panel_df, config, prepared_by_lag[1])
        rows.append(stats)
    return pl.DataFrame(rows)


AUDIT_VARIANTS = [
    ("Honest — as reported", {}),
    ("Require the contract to still be quoted tomorrow", {"require_next_two_sided": True}),
    ("Z-score against the full-sample moments", {"full_sample_zscore": True}),
    ("Today's index membership, applied to all history", {"static_universe": True}),
    (
        "All three defects together",
        {"require_next_two_sided": True, "full_sample_zscore": True,
         "static_universe": True},
    ),
]


def lookahead_audit(panel_df: pl.DataFrame) -> pl.DataFrame:
    """What each plausible look-ahead would have been worth, one at a time.

    Every row after the first is a mistake this study could have made and in
    one case did make. The point of running them is that all of them turn a
    t-statistic under 1 into one over 2, which is the whole distance between
    "nothing here" and a publishable result.
    """
    rows = []
    for label, flags in AUDIT_VARIANTS:
        config = pf.Config(signal_lag=1, label=label, **flags)
        _, _, stats = pf.run(panel_df, config)
        rows.append(stats)
    return pl.DataFrame(rows)


def plot_lookahead_audit(audit_df: pl.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(9, 4.4))
    labels = audit_df["label"].to_list()
    values = audit_df["gross_tstat"].to_list()
    positions = np.arange(len(labels))[::-1]
    colours = [ACCENT] + [SHORT_COLOUR] * (len(labels) - 1)
    ax.barh(positions, values, color=colours, height=0.6)
    ax.axvline(2, color=INK, lw=1.0, ls="--")
    ax.text(2.05, positions.max() + 0.45, "t = 2", fontsize=8, color=INK)
    for position, value in zip(positions, values):
        ax.text(value + 0.05, position, f"{value:.2f}", va="center", fontsize=8.5)
    style_axes(
        ax,
        "What a look-ahead is worth: lag-1 gross t-statistic under each defect\n"
        "Every one of them clears the bar the honest specification misses",
        xlabel="Gross t-statistic (Newey-West, 5 lags)",
    )
    ax.set_yticks(positions)
    ax.set_yticklabels([label.replace(" — ", "\n") for label in labels], fontsize=8)
    ax.set_xlim(0, max(values) * 1.18)
    save(fig, "fig11_lookahead_audit.png")


def robustness_grid(panel_df: pl.DataFrame) -> pl.DataFrame:
    rows = []
    for window in (20, 60, 120, 252):
        prepared = pf.prepare(panel_df, pf.Config(signal_lag=1, window=window))
        for decile in (0.05, 0.10, 0.20):
            config = pf.Config(
                signal_lag=1, window=window, decile=decile,
                label=f"w{window}-d{int(decile * 100)}",
            )
            _, _, stats = pf.run(panel_df, config, prepared)
            rows.append({"window": window, "decile": decile} | stats)
    return pl.DataFrame(rows)


def main() -> None:
    panel_df = load_panel()
    print(f"panel: {panel_df.height:,} symbol-days, {panel_df['symbol'].n_unique()} symbols")

    print("lag sweep")
    daily_by_lag, prepared_by_lag, lag_df = run_lag_sweep(panel_df)
    write_csv(lag_df, "lag_sweep.csv")

    print("annual")
    annual_df = annual_stats(panel_df, prepared_by_lag)
    write_csv(annual_df, "annual_stats.csv")

    print("deciles")
    pnl_df, reversion_df, earnings_df = decile_stats(prepared_by_lag)
    write_csv(pnl_df, "decile_pnl.csv")
    write_csv(reversion_df, "decile_iv_reversion.csv")
    write_csv(earnings_df, "decile_earnings_distance.csv")

    print("spread quintiles")
    quintile_df = spread_quintile_stats(prepared_by_lag)
    write_csv(quintile_df, "spread_quintiles.csv")

    print("earnings exclusion")
    exclusion_df = earnings_exclusion_stats(panel_df, prepared_by_lag)
    write_csv(exclusion_df, "earnings_exclusion.csv")

    print("look-ahead audit")
    audit_df = lookahead_audit(panel_df)
    write_csv(audit_df, "lookahead_audit.csv")

    print("robustness grid")
    grid_df = robustness_grid(panel_df)
    write_csv(grid_df, "robustness_grid.csv")

    headline = pl.DataFrame(
        [
            {"signal_lag": lag}
            | pf.summarize(daily_by_lag[lag], pf.Config(signal_lag=lag, label=f"lag{lag}"))
            for lag in (0, 1)
        ]
    )
    write_csv(headline, "headline_stats.csv")
    for lag in (0, 1):
        write_csv(daily_by_lag[lag], f"daily_pnl_lag{lag}.csv")

    print("figures")
    plot_equity_curves(daily_by_lag)
    plot_lag_decay(lag_df)
    plot_decile_pnl(pnl_df)
    plot_iv_reversion(reversion_df)
    plot_spread_quintiles(quintile_df)
    plot_costs(annual_df.filter(pl.col("signal_lag") == 0))
    plot_annual_sharpe(annual_df)
    plot_earnings(earnings_df, exclusion_df)
    plot_robustness(grid_df)
    plot_coverage(daily_by_lag[0])
    plot_lookahead_audit(audit_df)

    print("\nheadline")
    with pl.Config(tbl_cols=-1, tbl_width_chars=200):
        print(headline.select(
            "signal_lag", "days", "gross_sharpe", "gross_tstat",
            "gross_pnl_per_day", "breakeven_spread_fraction",
            "net_25_sharpe", "net_50_sharpe",
        ))


if __name__ == "__main__":
    main()
