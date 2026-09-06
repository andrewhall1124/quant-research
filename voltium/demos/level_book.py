"""End-to-end level book: six months of 2025 on the S&P 500 universe.

    uv run python voltium/demos/level_book.py [--start 2025-01-02 --end 2025-06-30]

Reads the risk model and the reference straddle returns from the store
(`data_pipelines.cli vol-risk-model`), builds the surface, forecast and
signal panels (surface cached under `voltium/.cache/`), runs the
weekly-rebalanced book, and prints `summary()`, the factor regression and
the decile table. Figures land in `voltium/demos/figures/`.

`--in-process` estimates the risk model on the fly from reference paths it
builds itself instead of reading the stored tables; that is the path for an
instrument that has not been pipelined.

The dates default to the first half of 2025 because that is where every
input exists at once: the stock tier (and so realized vol) starts mid-2023,
the HAR needs a year of fits behind it, and the risk model needs 250
sessions of reference straddle P&L before the first rebalance.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import time
from pathlib import Path

import polars as pl

from voltium.backtester import BacktestConfig, Backtester
from voltium.costs import HalfSpreadCost, PerShareCost
from voltium.instruments.straddle import DeltaHedgedStraddle, StraddleConfig
from voltium.loaders import DataPaths
from voltium.optimizer.constraints import (
    FactorNeutral,
    GrossShortVegaCap,
    GrossVegaCap,
    NetVegaNeutral,
    PerNameVegaCap,
)
from voltium.optimizer.mvo import MVO
from voltium.optimizer.objectives import JumpPenalty, MaxUtility, TurnoverPenalty
from voltium.optimizer.strategy import OptimizationStrategy
from voltium.providers.alphas import AlphaConfig, ICScaledAlpha
from voltium.providers.base import materialize
from voltium.providers.calendar import TradingCalendar
from voltium.providers.chain import StoreChainProvider
from voltium.providers.realized_vol import (
    HARConfig,
    HARForecaster,
    RealizedVolConfig,
    compute_realized_vol_panel,
)
from voltium.providers.signals import CrossSectionalVRPSignal, SignalConfig
from voltium.providers.stock_features import StockFeaturesProvider, load_spx_closes
from voltium.providers.straddle_returns import StoredStraddleReturns, StraddleReturnsProvider
from voltium.providers.surface import SurfaceConfig, build_surface_panel
from voltium.providers.universe import PointInTimeUniverse, StaticSectorProvider
from voltium.results import BacktestResults
from voltium.risk_model.constructor import FactorRiskModelConstructor
from voltium.risk_model.spec import FactorSpec
from voltium.risk_model.store import StoredFactorRiskModelConstructor
from voltium.trade_generator import TradeGenerator, TradeGeneratorConfig
from voltium.loaders import scan_options, scan_stocks

log = logging.getLogger("level_book")

DEMO_DIR = Path(__file__).resolve().parent
CACHE_DIR = DEMO_DIR.parent / ".cache"
FIGURES_DIR = DEMO_DIR / "figures"

GROSS_VEGA = 20_000.0  # dollars per vol point across the book
PER_NAME_CAP = 2_000.0
GROSS_SHORT_CAP = 10_000.0
NET_VEGA_TOL = 500.0
FACTOR_EPS = 1_000.0
RISK_AVERSION = 1e-5
# One-way cost per $ vega traded, amortised: the median straddle half-spread
# is ~1.3-1.7 per $ vega, so a round trip is ~3; over a ~30-session holding
# period that is ~0.1 per session, the scale of a daily alpha. The optimizer
# is myopic, so the penalty has to be quoted per session, not per trade.
TURNOVER_COST = 0.10
JUMP_PENALTY = 1.0


def build_spx_indices_rv(paths: DataPaths, start: dt.date, end: dt.date) -> pl.DataFrame:
    """SPX OHLC from `indices.parquet` (2024-) in the canonical stock schema."""
    frame = (
        pl.scan_parquet(paths.root / "indices.parquet")
        .filter(pl.col("symbol") == "SPX")
        .select("date", "symbol", "open", "high", "low", "close", pl.lit(0, dtype=pl.Int64).alias("volume"))
        .filter(pl.col("date").is_between(start, end))
    )
    return compute_realized_vol_panel(frame, RealizedVolConfig()).collect()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--start", type=dt.date.fromisoformat, default=dt.date(2025, 1, 2))
    parser.add_argument("--end", type=dt.date.fromisoformat, default=dt.date(2025, 6, 30))
    parser.add_argument("--rebalance", default="weekly", choices=["daily", "weekly"])
    parser.add_argument("--refresh", action="store_true", help="rebuild cached panels")
    parser.add_argument("--limit", type=int, default=None, help="use only the first N symbols (smoke test)")
    parser.add_argument("--in-process", action="store_true", help="estimate the risk model here instead of reading the store")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
    pl.Config.set_tbl_rows(30)
    pl.Config.set_tbl_width_chars(160)
    pl.Config.set_float_precision(4)
    FIGURES_DIR.mkdir(exist_ok=True)
    started = time.time()

    paths = DataPaths.from_repo()
    calendar = TradingCalendar.from_data(paths)
    history_start = calendar.shift(args.start, -300)  # risk model window + slack
    forward_end = min(calendar.shift(args.end, 70), calendar.sessions[-1])  # decile horizon
    stock_start = dt.date(2023, 6, 1)

    universe = PointInTimeUniverse(paths, history_start, forward_end)
    symbols = universe.symbols(history_start, forward_end)
    if args.limit:
        symbols = symbols[: args.limit]
    log.info("%d symbols, history from %s, forward to %s", len(symbols), history_start, forward_end)
    sectors_df = StaticSectorProvider(paths).get()

    # --- panels -----------------------------------------------------------
    instrument = DeltaHedgedStraddle(StraddleConfig(target_dte=60, roll_dte=20))
    if args.in_process:
        reference = StraddleReturnsProvider.from_store(
            paths, symbols, instrument, calendar, history_start, forward_end, cache_dir=CACHE_DIR / "reference", refresh=args.refresh
        )
        spx_reference = StraddleReturnsProvider.from_store(
            paths, ["SPX"], instrument, calendar, history_start, forward_end, index=True, cache_dir=CACHE_DIR / "reference", refresh=args.refresh
        )
    else:
        reference = StoredStraddleReturns(paths, symbols, history_start, forward_end)
        spx_reference = StoredStraddleReturns(paths, ["SPX"], history_start, forward_end)
    log.info("reference paths: %d rows (%.0fs)", reference.panel_df.height, time.time() - started)

    surface_df = materialize(
        build_surface_panel(scan_options(paths, symbols, history_start, forward_end, with_oi=False), SurfaceConfig()),
        CACHE_DIR / f"surface_{history_start}_{forward_end}.parquet",
        refresh=args.refresh,
    )
    spx_surface_df = build_surface_panel(
        scan_options(paths, ["SPX"], history_start, forward_end, index=True, with_oi=False), SurfaceConfig()
    ).collect()
    log.info("surface: %d rows (%.0fs)", surface_df.height, time.time() - started)

    rv_df = compute_realized_vol_panel(scan_stocks(paths, stock_start, forward_end, with_splits=True).filter(pl.col("symbol").is_in(symbols))).collect()
    spx_rv_df = build_spx_indices_rv(paths, stock_start, forward_end)
    forecaster = HARForecaster(HARConfig(forward_window=60))
    forecast_df = forecaster.forecast(rv_df, apply_df=spx_rv_df)
    spx_forecast_df = forecast_df.filter(pl.col("symbol") == "SPX")
    forecast_df = forecast_df.filter(pl.col("symbol") != "SPX")
    log.info("HAR: %d forecasts, last fit %s (%.0fs)", forecast_df.height, forecaster.coefficients_df.tail(1).to_dicts(), time.time() - started)

    features = StockFeaturesProvider.from_store(paths, symbols, stock_start, forward_end)
    market_return_df = load_spx_closes(paths, history_start, forward_end)

    spx_vrp_df = (
        spx_surface_df.join(spx_forecast_df, on=["date", "symbol"], how="inner")
        .filter(pl.col("iv60") > 0, pl.col("rv_fcst") > 0)
        .select("date", (pl.col("iv60").log() - pl.col("rv_fcst").log()).alias("spx_vrp"))
    )
    if spx_vrp_df.is_empty():
        log.warning("no SPX variance premium available; falling back to the universe mean")
        spx_vrp_df = None

    spec = FactorSpec(market_source="spx", use_sectors=True, window=250, min_observations=120)
    signal = CrossSectionalVRPSignal(surface_df, forecast_df, features.panel_df, sectors_df, spec, SignalConfig(), spx_vrp_df)
    log.info("signal: %d rows, spx factor used: %s (%.0fs)", signal.panel_df.height, signal.used_spx, time.time() - started)

    if args.in_process:
        risk_model_constructor = FactorRiskModelConstructor(
            reference.panel_df, sectors_df, spec, spx_reference.panel_df.select("date", "pnl_per_vega")
        )
    else:
        risk_model_constructor = StoredFactorRiskModelConstructor(paths, history_start, args.end)
    alphas = ICScaledAlpha(signal, risk_model_constructor, AlphaConfig(ic=0.04))

    # --- book ---------------------------------------------------------------
    optimizer = MVO(
        objectives=[MaxUtility(RISK_AVERSION), TurnoverPenalty(TURNOVER_COST), JumpPenalty(JUMP_PENALTY)],
        constraints=[
            NetVegaNeutral(NET_VEGA_TOL),
            FactorNeutral(FACTOR_EPS),
            PerNameVegaCap(PER_NAME_CAP),
            GrossShortVegaCap(GROSS_SHORT_CAP),
            GrossVegaCap(GROSS_VEGA),
        ],
    )
    strategy = OptimizationStrategy(alphas, risk_model_constructor, optimizer, universe=universe, gap_freq=features)
    backtester = Backtester(
        calendar=calendar,
        chain=StoreChainProvider(paths, symbols),
        instrument=instrument,
        strategy=strategy,
        trade_generator=TradeGenerator(instrument, TradeGeneratorConfig(max_spread=0.08, min_oi=100, band=0.10)),
        config=BacktestConfig(start=args.start, end=args.end, rebalance=args.rebalance),
        option_cost=HalfSpreadCost(fraction=1.0),
        hedge_cost=PerShareCost(0.005),
    )
    records_df, portfolio = backtester.run()
    records_df.write_parquet(DEMO_DIR / "records.parquet")
    portfolio.save(DEMO_DIR / "portfolio.json")
    log.info("backtest done (%.0fs)", time.time() - started)

    # --- results ------------------------------------------------------------
    results = BacktestResults(records_df)
    print("\n=== summary ===")
    for key, value in results.summary().items():
        print(f"{key:>36}: {value:,.4f}" if isinstance(value, float) else f"{key:>36}: {value}")

    print("\n=== factor regression (net P&L per $ gross vega on the risk factors) ===")
    factor_returns_df = risk_model_constructor.factor_returns(args.end)
    factor_returns_df = factor_returns_df.filter(pl.col("date").is_between(args.start, args.end))
    print(results.factor_regression(factor_returns_df, market_return_df))

    print("\n=== decile table (forward 60-session P&L per $ vega, long reference straddle) ===")
    decile_df = BacktestResults.decile_table(
        signal.panel_df.filter(pl.col("date").is_between(args.start, args.end)), reference.panel_df, horizon_days=60
    )
    print(decile_df)

    results.plot_equity_curve(FIGURES_DIR / "equity_curve.png")
    BacktestResults.plot_deciles(decile_df, FIGURES_DIR / "deciles.png")
    print(f"\nfigures in {FIGURES_DIR}  ({time.time() - started:.0f}s)")


if __name__ == "__main__":
    main()
