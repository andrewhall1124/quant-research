"""Earnings and the cross-sectional variance premium.

Three questions, on the panels `panel.py` wrote:

1. **Is the raw signal a calendar?** Bucket every (date, symbol) by days to
   the next print and report the raw premium, the signal z-score, the share
   of the top and bottom decile, and the forward 60-session P&L of the long
   reference straddle.
2. **What does the term structure say the print is worth?** The implied
   move `sqrt(J)` by estimation method and by year.
3. **Does stripping it change the signal?** Three versions of the VRP
   signal — raw; IV-adjusted (jump out of the surface, forecast unchanged);
   fully adjusted (jump out of the surface, event sessions out of the
   realized vol and its forecast) — each scored on the decile table and on
   a rank-weighted, net-vega-neutral book at $20k gross vega on the panel
   engine, no costs.

Run from the project root, after `panel.py`:

    uv run python -m research.earnings_adjusted_vrp.analysis
"""

from __future__ import annotations

import datetime as dt

import matplotlib
import numpy as np
import polars as pl

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from research.earnings_adjusted_vrp.panel import RESULTS_DIR, STUDY_DIR  # noqa: E402
from voltium.loaders import DataPaths  # noqa: E402
from voltium.panel_backtester import PanelBacktestConfig, PanelBacktester  # noqa: E402
from voltium.providers.base import PanelProvider  # noqa: E402
from voltium.providers.calendar import TradingCalendar  # noqa: E402
from voltium.providers.signals import CrossSectionalVRPSignal, SignalConfig, compute_vrp  # noqa: E402
from voltium.providers.universe import PointInTimeUniverse  # noqa: E402
from voltium.results import BacktestResults  # noqa: E402
from voltium.risk_model.spec import FactorSpec  # noqa: E402
from voltium.strategy import RankWeightedStrategy  # noqa: E402

FIGURES_DIR = STUDY_DIR / "figures"
GROSS_VEGA = 20_000.0
HORIZON = 60
BUCKETS = [(0, 7, "0-7"), (8, 30, "8-30"), (31, 60, "31-60"), (61, 90, "61-90"), (91, 10_000, "91+")]


def load(name: str) -> pl.DataFrame:
    return pl.read_parquet(RESULTS_DIR / f"{name}.parquet")


def bucket_expr() -> pl.Expr:
    expr = pl.when(pl.col("days_to_event").is_null()).then(pl.lit("no event"))
    for lo, hi, label in BUCKETS:
        expr = expr.when(pl.col("days_to_event").is_between(lo, hi)).then(pl.lit(label))
    return expr.otherwise(pl.lit("no event")).alias("bucket")


def spx_vrp(spx_surface_df: pl.DataFrame, spx_forecast_df: pl.DataFrame) -> pl.DataFrame:
    return (
        spx_surface_df.join(spx_forecast_df, on=["date", "symbol"], how="inner")
        .filter(pl.col("iv60") > 0, pl.col("rv_fcst") > 0)
        .select("date", (pl.col("iv60").log() - pl.col("rv_fcst").log()).alias("spx_vrp"))
    )


def build_signal(surface_df, forecast_df, features_df, sectors_df, spx_vrp_df) -> CrossSectionalVRPSignal:
    spec = FactorSpec(market_source="spx", use_sectors=True, window=250, min_observations=120)
    return CrossSectionalVRPSignal(surface_df, forecast_df, features_df, sectors_df, spec, SignalConfig(controls=("idio_vol",)), spx_vrp_df)


def forward_pnl(reference_df: pl.DataFrame) -> pl.DataFrame:
    """`(date, symbol, gross_fwd)`: the next `HORIZON` sessions of the long reference straddle per $ vega."""
    return (
        reference_df.sort("symbol", "date")
        .with_columns(pl.col("pnl_per_vega").fill_null(0.0).reverse().rolling_sum(HORIZON).reverse().shift(-1).over("symbol").alias("gross_fwd"))
        .select("date", "symbol", "gross_fwd")
        .drop_nulls()
    )


def by_days_to_event(signal_df, vrp_df, jump_df, fwd_df, start, end) -> pl.DataFrame:
    frame = (
        signal_df.join(vrp_df, on=["date", "symbol"])
        .join(jump_df.select("date", "symbol", "days_to_event"), on=["date", "symbol"], how="left")
        .join(fwd_df, on=["date", "symbol"], how="left")
        .filter(pl.col("date").is_between(start, end))
        .with_columns(bucket_expr(), pl.col("signal").rank().over("date").alias("rank"), pl.len().over("date").alias("n"))
        .with_columns(((pl.col("rank") - 1) * 10 // pl.col("n") + 1).alias("decile"))
    )
    order = {label: i for i, (_, _, label) in enumerate(BUCKETS)} | {"no event": len(BUCKETS)}
    table = (
        frame.group_by("bucket")
        .agg(
            pl.len().alias("name_days"),
            pl.col("vrp").mean().alias("mean_raw_vrp"),
            pl.col("signal").mean().alias("mean_signal_z"),
            (pl.col("decile") == 10).mean().alias("share_in_decile_10"),
            (pl.col("decile") == 1).mean().alias("share_in_decile_1"),
            pl.col("gross_fwd").mean().alias("fwd_pnl_per_vega"),
            (pl.col("gross_fwd").mean() / (pl.col("gross_fwd").std() / pl.col("gross_fwd").count().sqrt())).alias("fwd_t_naive"),
        )
        .with_columns(pl.col("bucket").replace_strict(order, return_dtype=pl.Int64).alias("order"))
        .sort("order")
        .drop("order")
    )
    return table


def jump_summary(jump_df: pl.DataFrame, start, end) -> tuple[pl.DataFrame, pl.DataFrame]:
    frame = jump_df.filter(pl.col("date").is_between(start, end)).with_columns(pl.col("jump_var").sqrt().alias("implied_move"))
    within = frame.filter(pl.col("days_to_event") <= 60)
    by_method = (
        within.group_by("method")
        .agg(pl.len().alias("name_days"), pl.col("implied_move").median().alias("median_move"), pl.col("implied_move").quantile(0.25).alias("q25"), pl.col("implied_move").quantile(0.75).alias("q75"))
        .with_columns((pl.col("name_days") / pl.col("name_days").sum()).alias("share"))
        .sort("method")
    )
    by_year = (
        within.filter(pl.col("method") == "term")
        .group_by(pl.col("date").dt.year().alias("year"))
        .agg(pl.col("implied_move").median().alias("median_move"), pl.col("implied_move").mean().alias("mean_move"), pl.len().alias("name_days"))
        .sort("year")
    )
    return by_method, by_year


def decile_table(signal_df, reference_df, start, end) -> pl.DataFrame:
    return BacktestResults.decile_table(signal_df.filter(pl.col("date").is_between(start, end)), reference_df, horizon_days=HORIZON)


def run_rank_book(signal, reference, universe, calendar, start, end) -> tuple[dict, pl.DataFrame]:
    strategy = RankWeightedStrategy(signal, GROSS_VEGA, universe=universe)
    engine = PanelBacktester(calendar, reference, strategy, PanelBacktestConfig(start, end, "weekly"))
    records_df, _ = engine.run(progress=False)
    results = BacktestResults(records_df)
    return results.summary(), results.book_df.select("date", "gross_pnl", "gross_vega")


def main() -> None:
    pl.Config.set_tbl_rows(30)
    pl.Config.set_tbl_width_chars(200)
    pl.Config.set_float_precision(4)
    FIGURES_DIR.mkdir(exist_ok=True)
    meta = dict(zip(load("meta")["key"], load("meta")["value"]))
    start, end = dt.date.fromisoformat(meta["start"]), dt.date.fromisoformat(meta["end"])
    history_start, forward_end = dt.date.fromisoformat(meta["history_start"]), dt.date.fromisoformat(meta["forward_end"])

    surface_raw_df, surface_ex_df = load("surface_raw"), load("surface_ex")
    forecast_raw_df, forecast_ex_df = load("forecast_raw"), load("forecast_ex")
    features_df, sectors_df, reference_df = load("features"), load("sectors"), load("reference")
    spx_vrp_df = spx_vrp(load("spx_surface"), load("spx_forecast"))
    jump_df = surface_ex_df.select("date", "symbol", "next_event", "days_to_event", "jump_var", "method")
    surface_ex_only_df = surface_ex_df.select("date", "symbol", "iv30", "iv60", "iv90")

    paths = DataPaths.from_repo()
    calendar = TradingCalendar.from_data(paths)
    universe = PointInTimeUniverse(paths, history_start, forward_end)
    reference = PanelProvider(reference_df)

    signals = {
        "raw": build_signal(surface_raw_df, forecast_raw_df, features_df, sectors_df, spx_vrp_df),
        "iv_adjusted": build_signal(surface_ex_only_df, forecast_raw_df, features_df, sectors_df, spx_vrp_df),
        "fully_adjusted": build_signal(surface_ex_only_df, forecast_ex_df, features_df, sectors_df, spx_vrp_df),
    }

    # 1. the calendar
    fwd_df = forward_pnl(reference_df)
    calendar_table = by_days_to_event(signals["raw"].panel_df, compute_vrp(surface_raw_df, forecast_raw_df, 60), jump_df, fwd_df, start, end)
    calendar_table_adj = by_days_to_event(signals["fully_adjusted"].panel_df, compute_vrp(surface_ex_only_df, forecast_ex_df, 60), jump_df, fwd_df, start, end)
    calendar_out = pl.concat([calendar_table.with_columns(pl.lit("raw").alias("signal_version")), calendar_table_adj.with_columns(pl.lit("fully_adjusted").alias("signal_version"))])
    calendar_out.write_csv(RESULTS_DIR / "by_days_to_event.csv")
    print("\n=== raw signal by days to next print ===")
    print(calendar_table)
    print("\n=== fully adjusted signal by days to next print ===")
    print(calendar_table_adj)

    # 2. the jump
    by_method, by_year = jump_summary(jump_df, start, end)
    pl.concat([by_method.with_columns(pl.lit(None, dtype=pl.Int32).alias("year")).select("method", "year", "name_days", "share", "median_move", "q25", "q75"),
               by_year.with_columns(pl.lit("term").alias("method"), pl.lit(None, dtype=pl.Float64).alias("share"), pl.lit(None, dtype=pl.Float64).alias("q25"), pl.lit(None, dtype=pl.Float64).alias("q75")).select("method", "year", "name_days", "share", "median_move", "q25", "q75")],
              how="vertical_relaxed").write_csv(RESULTS_DIR / "jump_estimates.csv")
    print("\n=== implied earnings move, name-days within 60 days of a print ===")
    print(by_method)
    print(by_year)

    # 3. deciles and books
    decile_frames, book_rows, series = [], [], []
    for name, signal in signals.items():
        deciles = decile_table(signal.panel_df, reference_df, start, end).with_columns(pl.lit(name).alias("signal_version"))
        decile_frames.append(deciles)
        summary, book_df = run_rank_book(signal, reference, universe, calendar, start, end)
        book_rows.append({"signal_version": name, **summary})
        series.append(book_df.with_columns(pl.lit(name).alias("signal_version")))
        print(f"\n=== {name}: deciles ===")
        print(deciles.select("decile", "gross_fwd_pnl_per_vega", "net_fwd_pnl_per_vega", "gross_t_stat", "mean_names"))
        print(f"=== {name}: rank book ===")
        print({k: (round(v, 3) if isinstance(v, float) else v) for k, v in summary.items()})
    pl.concat(decile_frames).write_csv(RESULTS_DIR / "deciles.csv")
    pl.DataFrame(book_rows).write_csv(RESULTS_DIR / "books.csv")
    pl.concat(series).write_csv(RESULTS_DIR / "book_series.csv")

    # figure
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.5))
    for frame in decile_frames:
        name = frame["signal_version"][0]
        axes[0].plot(frame["decile"], frame["gross_fwd_pnl_per_vega"], marker="o", label=name)
    axes[0].set_title(f"forward {HORIZON}-session P&L per $ vega, long reference straddle")
    axes[0].set_xlabel("signal decile (10 = implied rich)")
    axes[0].axhline(0, color="grey", lw=0.5)
    axes[0].legend()
    labels = calendar_table["bucket"].to_list()
    x = np.arange(len(labels))
    axes[1].bar(x - 0.2, calendar_table["fwd_pnl_per_vega"], width=0.4, label="forward P&L per $ vega")
    axes[1].set_xticks(x, labels)
    axes[1].set_xlabel("days to next print")
    axes[1].set_title("forward P&L by distance to the print")
    ax2 = axes[1].twinx()
    ax2.bar(x + 0.2, calendar_table["share_in_decile_10"], width=0.4, color="tab:red", alpha=0.6, label="share in decile 10 (raw)")
    ax2.axhline(0.1, color="tab:red", lw=0.5, ls="--")
    ax2.set_ylabel("share in decile 10")
    for frame in series:
        name = frame["signal_version"][0]
        axes[2].plot(frame["date"], frame["gross_pnl"].cum_sum() / 1e3, label=name)
    axes[2].set_title("rank book, cumulative gross P&L ($k), $20k gross vega")
    axes[2].legend()
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "earnings_adjusted_vrp.png", dpi=130)
    print(f"\nwrote {RESULTS_DIR} and {FIGURES_DIR}")


if __name__ == "__main__":
    main()
