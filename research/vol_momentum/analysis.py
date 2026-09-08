"""Vol momentum: sort on trailing reference-straddle P&L, then trade it.

For each formation window the signal is the sum of a name's per-vega
reference P&L over `window` sessions ending `skip` sessions ago, z-scored
across names each day, with the sign set so a high past return is *cheap*
(bought). Each window is scored three ways on the seven-year panel:

* the 60-session decile table of the long reference straddle;
* a rank-weighted, net-vega-neutral book at $20k gross vega;
* the voltium mean-variance book (stored factor risk model, IC-scaled
  alphas, turnover penalty, net-vega and gross-vega constraints),

both books weekly on the panel engine, no costs. `research.vol_reversal`
imports the machinery here and runs it with the sign flipped on short
windows.

Run from the project root, after `panel.py`:

    uv run python -m research.vol_momentum.analysis
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from pathlib import Path

import matplotlib
import numpy as np
import polars as pl

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from research.vol_momentum.panel import RESULTS_DIR, STUDY_DIR  # noqa: E402
from voltium.loaders import DataPaths  # noqa: E402
from voltium.optimizer.constraints import GrossVegaCap, NetVegaNeutral  # noqa: E402
from voltium.optimizer.mvo import MVO  # noqa: E402
from voltium.optimizer.objectives import MaxUtility, TurnoverPenalty  # noqa: E402
from voltium.optimizer.strategy import OptimizationStrategy  # noqa: E402
from voltium.panel_backtester import PanelBacktestConfig, PanelBacktester  # noqa: E402
from voltium.providers.alphas import AlphaConfig, ICScaledAlpha  # noqa: E402
from voltium.providers.base import PanelProvider  # noqa: E402
from voltium.providers.calendar import TradingCalendar  # noqa: E402
from voltium.providers.signals import PastReturnConfig, PastReturnSignal, SignalProvider  # noqa: E402
from voltium.providers.stock_features import StockFeaturesProvider  # noqa: E402
from voltium.providers.universe import PointInTimeUniverse  # noqa: E402
from voltium.results import BacktestResults  # noqa: E402
from voltium.risk_model.store import StoredFactorRiskModelConstructor  # noqa: E402
from voltium.strategy import RankWeightedStrategy, Strategy  # noqa: E402

log = logging.getLogger("vol_momentum")

GROSS_VEGA = 20_000.0
HORIZON = 60
RISK_AVERSION = 1e-5
TURNOVER_COST = 0.10
NET_VEGA_TOL = 500.0
IC = 0.04

# label, window sessions, skip sessions; the sign is the study's
MOMENTUM_WINDOWS = [("1m", 21, 0), ("3m_skip1m", 63, 21), ("6m_skip1m", 126, 21), ("12m_skip1m", 252, 21), ("12m_noskip", 252, 0)]
SUMMARY_COLUMNS = [
    "window", "spread", "spread_t_overlap_adj", "decile_1", "decile_10", "rank_gross_pnl", "rank_sharpe", "rank_max_drawdown", "rank_market_t",
    "mvo_gross_pnl", "mvo_sharpe", "mvo_max_drawdown", "mvo_market_t", "mvo_intercept_t", "rank_net_pnl_full_cost", "rank_net_sharpe_full_cost",
]


class Context:
    """Everything the books need, loaded once."""

    def __init__(self, results_dir: Path) -> None:
        meta = pl.read_parquet(results_dir / "meta.parquet")
        values = dict(zip(meta["key"], meta["value"]))
        self.start, self.end = dt.date.fromisoformat(values["start"]), dt.date.fromisoformat(values["end"])
        self.history_start, self.forward_end = dt.date.fromisoformat(values["history_start"]), dt.date.fromisoformat(values["forward_end"])
        self.reference_df = pl.read_parquet(results_dir / "reference.parquet")
        self.reference = PanelProvider(self.reference_df)
        self.features = StockFeaturesProvider(pl.read_parquet(results_dir / "features.parquet"))
        self.market_return_df = pl.read_parquet(results_dir / "market_return.parquet")
        self.paths = DataPaths.from_repo()
        self.calendar = TradingCalendar.from_data(self.paths)
        self.universe = PointInTimeUniverse(self.paths, self.history_start, self.forward_end)
        self.risk_model = StoredFactorRiskModelConstructor(self.paths, self.history_start, self.end)
        self.factor_returns_df = self.risk_model.factor_returns(self.end).filter(pl.col("date").is_between(self.start, self.end))


def build_mvo_strategy(signal: SignalProvider, ctx: Context) -> Strategy:
    alphas = ICScaledAlpha(signal, ctx.risk_model, AlphaConfig(ic=IC))
    optimizer = MVO(objectives=[MaxUtility(RISK_AVERSION), TurnoverPenalty(TURNOVER_COST)], constraints=[NetVegaNeutral(NET_VEGA_TOL), GrossVegaCap(GROSS_VEGA)])
    return OptimizationStrategy(alphas, ctx.risk_model, optimizer, universe=ctx.universe, gap_freq=ctx.features)


def run_book(strategy: Strategy, ctx: Context, cost_fraction: float = 0.0) -> tuple[dict, pl.DataFrame, pl.DataFrame]:
    engine = PanelBacktester(ctx.calendar, ctx.reference, strategy, PanelBacktestConfig(ctx.start, ctx.end, "weekly", cost_fraction=cost_fraction))
    records_df, _ = engine.run(progress=False)
    results = BacktestResults(records_df)
    regression = results.factor_regression(ctx.factor_returns_df, ctx.market_return_df)
    return results.summary(), results.book_df.select("date", "gross_pnl", "gross_vega"), regression


def spread_series(signal_df: pl.DataFrame, reference_df: pl.DataFrame, start: dt.date, end: dt.date) -> pl.DataFrame:
    """Daily decile-10 minus decile-1 forward P&L per $ vega (long reference straddle)."""
    forward = (
        reference_df.sort("symbol", "date")
        .with_columns(pl.col("pnl_per_vega").fill_null(0.0).reverse().rolling_sum(HORIZON).reverse().shift(-1).over("symbol").alias("gross_fwd"))
        .select("date", "symbol", "gross_fwd")
        .drop_nulls()
    )
    frame = (
        signal_df.filter(pl.col("date").is_between(start, end))
        .join(forward, on=["date", "symbol"])
        .with_columns(pl.col("signal").rank().over("date").alias("rank"), pl.len().over("date").alias("n"))
        .with_columns(((pl.col("rank") - 1) * 10 // pl.col("n") + 1).cast(pl.Int64).alias("decile"))
    )
    return (
        frame.filter(pl.col("decile").is_in([1, 10]))
        .group_by("date", "decile")
        .agg(pl.col("gross_fwd").mean())
        .pivot(index="date", on="decile", values="gross_fwd")
        .rename({"1": "d1", "10": "d10"})
        .with_columns((pl.col("d10") - pl.col("d1")).alias("spread"))
        .sort("date")
    )


def spread_stats(spread_df: pl.DataFrame) -> dict[str, float]:
    s = spread_df["spread"].drop_nulls().to_numpy()
    naive_t = float(s.mean() / s.std(ddof=1) * np.sqrt(len(s)))
    return {"spread": float(s.mean()), "spread_t_naive": naive_t, "spread_t_overlap_adj": naive_t / np.sqrt(HORIZON), "decile_1": float(spread_df["d1"].mean()), "decile_10": float(spread_df["d10"].mean())}


def market_loading(regression_df: pl.DataFrame) -> tuple[float, float]:
    row = regression_df.filter(pl.col("regressor") == "market")
    return (float(row["coefficient"][0]), float(row["t_stat"][0])) if row.height else (float("nan"), float("nan"))


def run_study(
    windows: list[tuple[str, int, int]], sign: float, ctx: Context, results_dir: Path, figures_dir: Path, figure_name: str, title: str,
    signal_reference_df: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """`signal_reference_df` (default: the reference panel) is what the signal is built from; the books always trade the reference unit."""
    started = time.time()
    rows, decile_frames, series = [], [], []
    source_df = signal_reference_df if signal_reference_df is not None else ctx.reference_df
    for label, window, skip in windows:
        signal = PastReturnSignal(source_df, PastReturnConfig(window=window, skip=skip, sign=sign))
        deciles = BacktestResults.decile_table(signal.panel_df.filter(pl.col("date").is_between(ctx.start, ctx.end)), ctx.reference_df, horizon_days=HORIZON)
        decile_frames.append(deciles.with_columns(pl.lit(label).alias("window")))
        stats = spread_stats(spread_series(signal.panel_df, ctx.reference_df, ctx.start, ctx.end))
        row = {"window": label, "sessions": window, "skip": skip, "signal_rows": signal.panel_df.height, **stats}
        for book_name, strategy in (("rank", RankWeightedStrategy(signal, GROSS_VEGA, universe=ctx.universe)), ("mvo", build_mvo_strategy(signal, ctx))):
            summary, book_df, regression = run_book(strategy, ctx)
            loading, loading_t = market_loading(regression)
            row |= {
                f"{book_name}_gross_pnl": summary["total_gross_pnl"], f"{book_name}_sharpe": summary["sharpe"], f"{book_name}_max_drawdown": summary["max_drawdown"],
                f"{book_name}_turnover": summary["annual_turnover_over_gross_vega"], f"{book_name}_positions": summary["mean_positions"],
                f"{book_name}_market_loading": loading, f"{book_name}_market_t": loading_t,
                f"{book_name}_intercept_t": float(regression.filter(pl.col("regressor") == "const")["t_stat"][0]),
            }
            series.append(book_df.with_columns(pl.lit(label).alias("window"), pl.lit(book_name).alias("book")))
        # the same rank book paying the full half-spread on every fill and the reference path's roll and hedge costs
        costed, _, _ = run_book(RankWeightedStrategy(signal, GROSS_VEGA, universe=ctx.universe), ctx, cost_fraction=1.0)
        row |= {"rank_net_pnl_full_cost": costed["total_net_pnl"], "rank_net_sharpe_full_cost": costed["sharpe"], "rank_cost_total": costed["total_cost"]}
        rows.append(row)
        log.info("%s done (%.0fs): spread %.2f, rank sharpe %.2f, mvo sharpe %.2f", label, time.time() - started, row["spread"], row["rank_sharpe"], row["mvo_sharpe"])
    summary_df = pl.DataFrame(rows)
    summary_df.write_csv(results_dir / "summary.csv")
    pl.concat(decile_frames).write_csv(results_dir / "deciles.csv")
    pl.concat(series).write_csv(results_dir / "book_series.csv")

    figures_dir.mkdir(exist_ok=True)
    fig, axes = plt.subplots(1, 3, figsize=(16, 4.8))
    x = np.arange(len(rows))
    axes[0].bar(x, summary_df["spread"], color=["tab:red" if v > 0 else "tab:blue" for v in summary_df["spread"]])
    axes[0].set_xticks(x, summary_df["window"], rotation=20)
    axes[0].axhline(0, color="grey", lw=0.5)
    axes[0].set_title(f"decile 10 − decile 1, forward {HORIZON}-session P&L per $ vega")
    for frame in decile_frames:
        axes[1].plot(frame["decile"], frame["gross_fwd_pnl_per_vega"], marker="o", label=frame["window"][0])
    axes[1].set_title("forward P&L by signal decile (10 = richness)")
    axes[1].set_xlabel("decile")
    axes[1].legend(fontsize=8)
    best = summary_df.sort("rank_sharpe", descending=True)["window"][0]
    for frame in series:
        if frame["window"][0] == best:
            axes[2].plot(frame["date"], frame["gross_pnl"].cum_sum() / 1e3, label=f"{best} {frame['book'][0]}")
    axes[2].set_title("cumulative gross P&L ($k), $20k gross vega, best window")
    axes[2].legend()
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(figures_dir / figure_name, dpi=130)
    return summary_df


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
    pl.Config.set_tbl_cols(30)
    pl.Config.set_tbl_width_chars(260)
    pl.Config.set_float_precision(3)
    ctx = Context(RESULTS_DIR)
    summary_df = run_study(MOMENTUM_WINDOWS, sign=-1.0, ctx=ctx, results_dir=RESULTS_DIR, figures_dir=STUDY_DIR / "figures", figure_name="vol_momentum.png", title="vol momentum: high past straddle P&L bought")
    print(summary_df.select(SUMMARY_COLUMNS))


if __name__ == "__main__":
    main()
