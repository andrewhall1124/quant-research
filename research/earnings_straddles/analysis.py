"""Straddle returns around S&P 500 earnings announcements, 2017-2025.

Two trades on the same ATM straddle, from `panel.py`:

* through the print: long from the last close before the release to the
  first close after it, which is where the implied move is paid for and the
  realized move delivered;
* run-up: long from five sessions before that entry to the entry, which
  holds the pre-announcement IV rise.

Each is reported long and short, at the midpoint and inside the spread, per
event and averaged by calendar month so the t-statistics respect the fact
that events cluster in earnings seasons. The cross-section is sorted on the
implied move, on the name's own history of realized versus implied moves,
and on the pre-print IV rise.

Run from the project root, after `panel.py`:

    uv run python -m research.earnings_straddles.analysis
"""

import matplotlib
import polars as pl

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from research.earnings_straddles.panel import PANEL_PATH, RESULTS_DIR, STUDY_DIR  # noqa: E402
from research.goyal_saretto.analysis import summarize  # noqa: E402

FIGURES_DIR = STUDY_DIR / "figures"
SPREAD_FRACTIONS = (0.25, 0.5, 1.0)
HISTORY_EVENTS = 4


def compute_event_returns(panel_df: pl.DataFrame, spread_fraction: float = 0.0, side: str = "long") -> pl.DataFrame:
    """Per-event returns for the two windows, each on its own contract.

    A long buys inside the ask and sells inside the bid by `spread_fraction`
    of the half-spread; the exit spread is the entry's relative spread
    applied to the exit mark. Returns are scaled by the straddle paid.
    """
    sign = 1 if side == "long" else -1

    def window(prefix: str, exit_mark: str, spot_exit: str) -> tuple[pl.Expr, pl.Expr]:
        half_spread = (pl.col(f"{prefix}call_ask") - pl.col(f"{prefix}call_bid")
                       + pl.col(f"{prefix}put_ask") - pl.col(f"{prefix}put_bid")) / 2
        relative = half_spread / pl.col(f"{prefix}straddle_entry")
        entry_price = pl.col(f"{prefix}straddle_entry") * (1 + sign * spread_fraction * relative)
        exit_price = pl.col(exit_mark) * (1 - sign * spread_fraction * relative)
        move = pl.col(spot_exit) - pl.col(f"{prefix}spot_entry")
        straddle = sign * (exit_price / entry_price - 1)
        hedged = sign * (exit_price - entry_price - pl.col(f"{prefix}net_delta") * move) / entry_price
        return straddle, hedged

    print_straddle, print_hedged = window("", "straddle_exit", "spot_exit")
    runup_straddle, runup_hedged = window("runup_", "runup_straddle_exit", "runup_spot_exit")
    return panel_df.with_columns(
        print_straddle.alias("print_straddle"), print_hedged.alias("print_hedged"),
        runup_straddle.alias("runup_straddle"), runup_hedged.alias("runup_hedged"),
        (pl.col("straddle_entry") / pl.col("spot_entry")).alias("implied_move"),
        (pl.col("spot_exit") / pl.col("spot_entry") - 1).abs().alias("realized_move"),
        (pl.col("iv_exit") / pl.col("iv_entry") - 1).alias("iv_crush"),
        (pl.col("runup_iv_exit") / pl.col("runup_iv_entry") - 1).alias("iv_runup_rise"),
        pl.col("event_date").dt.truncate("1mo").alias("month"),
    ).with_columns((pl.col("realized_move") / pl.col("implied_move")).alias("move_ratio"))


def with_history(returns_df: pl.DataFrame) -> pl.DataFrame:
    """Each name's mean realized/implied move ratio over its previous
    `HISTORY_EVENTS` events, known before the current one."""
    ordered = returns_df.sort("symbol", "event_date")
    history = (
        pl.col("move_ratio").shift(1).rolling_mean(HISTORY_EVENTS, min_samples=HISTORY_EVENTS).over("symbol")
    )
    return ordered.with_columns(history.alias("history_ratio"))


def monthly_means(returns_df: pl.DataFrame, columns: tuple[str, ...]) -> pl.DataFrame:
    return returns_df.group_by("month").agg(pl.len().alias("events"), *[pl.col(column).mean() for column in columns]).sort("month")


def summarize_series(returns_df: pl.DataFrame, columns: tuple[str, ...], label: dict) -> list[dict]:
    monthly = monthly_means(returns_df, columns)
    rows = []
    for column in columns:
        stats = summarize(monthly[column])
        rows.append(label | {"series": column, "events": returns_df.height, "months": monthly.height,
                             "event_mean": float(returns_df[column].mean()),
                             "event_median": float(returns_df[column].median()),
                             "share_positive": float((returns_df[column] > 0).mean())} | stats)
    return rows


def build_summary(panel_df: pl.DataFrame) -> pl.DataFrame:
    rows = []
    columns = ("print_straddle", "print_hedged", "runup_straddle", "runup_hedged")
    for side in ("long", "short"):
        for fraction in (0.0, *SPREAD_FRACTIONS):
            returns_df = compute_event_returns(panel_df, fraction, side)
            rows += summarize_series(returns_df.filter(pl.col("runup_straddle_entry").is_not_null()), columns,
                                     {"side": side, "effective_spread": fraction})
    return pl.DataFrame(rows)


def build_moves(returns_df: pl.DataFrame) -> pl.DataFrame:
    rows = []
    for column in ("implied_move", "realized_move", "move_ratio", "iv_crush", "iv_runup_rise"):
        clean = returns_df[column].drop_nulls()
        rows.append({"series": column, "mean": float(clean.mean()), "median": float(clean.median()),
                     "p25": float(clean.quantile(0.25)), "p75": float(clean.quantile(0.75)),
                     "share_above_one" if column == "move_ratio" else "share_positive": float((clean > (1 if column == "move_ratio" else 0)).mean())})
    return pl.DataFrame(rows)


def build_sorts(returns_df: pl.DataFrame) -> pl.DataFrame:
    """Long through-print returns by tercile of each sort variable, terciles
    cut within calendar month."""
    rows = []
    sorts = {
        "implied move": "implied_move",
        "history ratio (past 4 events)": "history_ratio",
        "iv rise into print": "iv_runup_rise",
        "iv level at entry": "iv_entry",
    }
    for name, column in sorts.items():
        subset = returns_df.filter(pl.col(column).is_not_null())
        tercile = (pl.col(column).rank().over("month") * 3 / pl.len().over("month")).ceil().clip(1, 3)
        labelled = subset.with_columns(tercile.alias("tercile"))
        for value in (1, 2, 3):
            group = labelled.filter(pl.col("tercile") == value)
            rows += summarize_series(group, ("print_straddle", "print_hedged", "move_ratio"),
                                     {"sort": name, "tercile": value, "sort_mean": float(group[column].mean())})
        top = monthly_means(labelled.filter(pl.col("tercile") == 3), ("print_straddle", "print_hedged"))
        bottom = monthly_means(labelled.filter(pl.col("tercile") == 1), ("print_straddle", "print_hedged"))
        spread = top.join(bottom, on="month", suffix="_bottom")
        for column in ("print_straddle", "print_hedged"):
            rows.append({"sort": name, "tercile": "3-1", "sort_mean": None, "series": column,
                         "events": None, "months": spread.height, "event_mean": None, "event_median": None,
                         "share_positive": None} | summarize(spread[column] - spread[f"{column}_bottom"]))
    return pl.DataFrame(rows)


def build_sort_costs(panel_df: pl.DataFrame, returns_df: pl.DataFrame) -> pl.DataFrame:
    """The short side of the top tercile of each sort, inside the spread."""
    rows = []
    for name, column in (("implied move", "implied_move"), ("iv level at entry", "iv_entry"), ("iv rise into print", "iv_runup_rise")):
        subset = returns_df.filter(pl.col(column).is_not_null())
        tercile = (pl.col(column).rank().over("month") * 3 / pl.len().over("month")).ceil().clip(1, 3)
        keys = subset.with_columns(tercile.alias("tercile")).filter(pl.col("tercile") == 3).select("symbol", "event_date")
        for fraction in (0.0, *SPREAD_FRACTIONS):
            short_df = compute_event_returns(panel_df.join(keys, on=["symbol", "event_date"], how="semi"), fraction, "short")
            short_df = short_df.with_columns(pl.col("event_date").dt.truncate("1mo").alias("month"))
            rows += summarize_series(short_df, ("print_straddle", "print_hedged"), {"sort": name, "effective_spread": fraction})
    return pl.DataFrame(rows)


def build_splits(returns_df: pl.DataFrame) -> pl.DataFrame:
    rows = []
    for session in ("bmo", "amc"):
        rows += summarize_series(returns_df.filter(pl.col("session") == session),
                                 ("print_straddle", "print_hedged", "runup_straddle"), {"split": f"session {session}"})
    for year in sorted(returns_df["year"].unique().to_list()):
        rows += summarize_series(returns_df.filter(pl.col("year") == year),
                                 ("print_straddle", "print_hedged", "runup_straddle"), {"split": str(year)})
    return pl.DataFrame(rows)


def plot_moves(returns_df: pl.DataFrame) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
    ratio = returns_df["move_ratio"].filter(returns_df["move_ratio"] < 4)
    axes[0].hist(ratio.to_list(), bins=80, color="#4c72b0")
    axes[0].axvline(1, color="black", linewidth=0.8)
    axes[0].set_xlabel("realized move / implied move (straddle over spot)")
    axes[0].set_ylabel("events")
    axes[0].set_title("Realized against implied, per event")
    yearly = returns_df.group_by("year").agg(
        pl.col("print_straddle").mean().alias("long_straddle"), pl.col("print_hedged").mean().alias("long_hedged"),
        pl.col("runup_straddle").mean().alias("runup")).sort("year")
    years = yearly["year"].to_list()
    width = 0.27
    axes[1].bar([year - width for year in years], (yearly["long_straddle"] * 100).to_list(), width, label="through print, straddle", color="#4c72b0")
    axes[1].bar(years, (yearly["long_hedged"] * 100).to_list(), width, label="through print, delta-hedged", color="#dd8452")
    axes[1].bar([year + width for year in years], (yearly["runup"] * 100).to_list(), width, label="run-up, straddle", color="#55a868")
    axes[1].axhline(0, color="black", linewidth=0.8)
    axes[1].set_ylabel("mean long return per event, %")
    axes[1].set_title("By year")
    axes[1].legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "earnings.png", dpi=150)
    plt.close(fig)


def main() -> None:
    FIGURES_DIR.mkdir(exist_ok=True)
    panel_df = pl.read_parquet(PANEL_PATH)
    returns_df = with_history(compute_event_returns(panel_df))
    returns_df.drop("call_bid", "call_ask", "put_bid", "put_ask", "runup_call_bid", "runup_call_ask", "runup_put_bid", "runup_put_ask").write_csv(RESULTS_DIR / "events.csv")
    summary_df = build_summary(panel_df)
    summary_df.write_csv(RESULTS_DIR / "summary.csv")
    build_moves(returns_df).write_csv(RESULTS_DIR / "moves.csv")
    build_sorts(returns_df).write_csv(RESULTS_DIR / "sorts.csv")
    build_splits(returns_df).write_csv(RESULTS_DIR / "splits.csv")
    build_sort_costs(panel_df, returns_df).write_csv(RESULTS_DIR / "sort_costs.csv")
    monthly_means(returns_df, ("print_straddle", "print_hedged", "runup_straddle", "runup_hedged", "move_ratio", "iv_crush")).write_csv(RESULTS_DIR / "monthly.csv")
    plot_moves(returns_df)

    pl.Config.set_tbl_rows(40)
    pl.Config.set_float_precision(4)
    pl.Config.set_tbl_width_chars(200)
    print(f"{returns_df.height} events, {returns_df['symbol'].n_unique()} symbols, {returns_df['month'].n_unique()} months")
    print(summary_df.filter(pl.col("side") == "long").select("effective_spread", "series", "event_mean", "event_median", "share_positive", "mean", "t_stat"))
    print(build_moves(returns_df))


if __name__ == "__main__":
    main()
