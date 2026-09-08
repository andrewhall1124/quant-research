"""VRP MVO book on the panel engine at several risk aversions and with the per-name cap.

    uv run python voltium/demos/risk_aversion_sweep.py

Seven years, --rv-source close. Writes demos/risk_aversion_sweep.csv; the numbers are in docs/experiments.md.
"""
import argparse, datetime as dt, sys, time
from pathlib import Path
import polars as pl
sys.path.insert(0, str(Path(__file__).resolve().parent))
import level_book as lb
from voltium.optimizer.constraints import GrossVegaCap, NetVegaNeutral, PerNameVegaCap
from voltium.optimizer.mvo import MVO
from voltium.optimizer.objectives import MaxUtility, TurnoverPenalty
from voltium.optimizer.strategy import OptimizationStrategy
from voltium.panel_backtester import PanelBacktestConfig, PanelBacktester
from voltium.providers.alphas import AlphaConfig, ICScaledAlpha
from voltium.results import BacktestResults

args = argparse.Namespace(start=dt.date(2018, 7, 2), end=dt.date(2025, 6, 30), rebalance="weekly", refresh=False, limit=None, in_process=False, rv_source="close")
panels = lb.build_panels(args)
signal = lb.build_vrp_signal(panels)
alphas = ICScaledAlpha(signal, panels.risk_model_constructor, AlphaConfig(ic=0.04))
rows = []
for label, lam, cap in [("1e-5", 1e-5, None), ("1e-4", 1e-4, None), ("1e-3", 1e-3, None), ("1e-2", 1e-2, None), ("1e-5 cap 2k", 1e-5, 2000.0), ("1e-4 cap 2k", 1e-4, 2000.0)]:
    t0 = time.time()
    constraints = [NetVegaNeutral(500.0), GrossVegaCap(20_000.0)] + ([PerNameVegaCap(cap)] if cap else [])
    optimizer = MVO(objectives=[MaxUtility(lam), TurnoverPenalty(0.10)], constraints=constraints)
    strategy = OptimizationStrategy(alphas, panels.risk_model_constructor, optimizer, universe=panels.universe, gap_freq=panels.features)
    records_df, _ = PanelBacktester(panels.calendar, panels.reference, strategy, PanelBacktestConfig(args.start, args.end, "weekly")).run(progress=False)
    held = records_df.filter(pl.col("contracts") != 0).with_columns((pl.col("dollar_vega").abs() / pl.col("dollar_vega").abs().sum().over("date")).alias("share"))
    eff = held.group_by("date").agg((1.0 / (pl.col("share") ** 2).sum()).alias("eff_n"))["eff_n"].mean()
    s = BacktestResults(records_df).summary()
    rows.append(dict(risk_aversion=label, gross_pnl=s["total_gross_pnl"], sharpe=s["sharpe"], max_drawdown=s["max_drawdown"], gross_vega=s["mean_gross_vega"], turnover=s["annual_turnover_over_gross_vega"], effective_names=eff, seconds=time.time() - t0))
    print(rows[-1], flush=True)
pl.Config.set_tbl_width_chars(200); pl.Config.set_float_precision(2)
out = pl.DataFrame(rows); print(out); out.write_csv(Path(__file__).resolve().parent / "risk_aversion_sweep.csv")
