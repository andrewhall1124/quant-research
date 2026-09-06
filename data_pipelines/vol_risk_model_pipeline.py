"""The volatility risk model, estimated once and stored.

Four tables, all point-in-time, from the option chains already on disk:

* `vol_reference_returns/<SYMBOL>.parquet` — the daily P&L per dollar of
  vega of one long delta-hedged ATM straddle in each name (and in SPX),
  produced by running voltium's backtester on the chain. This is the
  "return" every other table is estimated on.
* `vol_factor_returns.parquet` — market factor (the SPX reference straddle)
  and one factor per GICS sector, the sector series orthogonalised to the
  market with a trailing 250-session beta.
* `vol_factor_loadings.parquet`, `vol_idio_vol.parquet` — per name and
  session, a trailing 250-session regression on the market and its own
  sector; the residual std is the idio vol.
* `vol_factor_covariances.parquet` — per session, the Ledoit-Wolf-shrunk
  covariance of the factor series over the same window.

The reference step is the expensive one (~0.25 s per symbol-year) and is
resumable at symbol granularity; the estimation steps take a minute or two
and are rerun every time, because they depend on every symbol's file.

    uv run python -m data_pipelines.cli vol-risk-model
    uv run python -m data_pipelines.cli vol-risk-model --start 2023-01-01 --limit 20
"""

import argparse
import time
from datetime import date

import polars as pl

from data_access_layer import paths
from data_pipelines.utils import universe_symbols, write_atomic
from voltium.instruments.straddle import DeltaHedgedStraddle, StraddleConfig
from voltium.loaders import DataPaths, scan_options, scan_sectors
from voltium.providers.calendar import TradingCalendar
from voltium.providers.chain import ChainBand
from voltium.providers.straddle_returns import REFERENCE_SCHEMA, compute_reference_path
from voltium.risk_model.factor import (
    build_factor_returns,
    estimate_rolling_factor_covariances,
    estimate_rolling_loadings,
)
from voltium.risk_model.spec import FactorSpec

HISTORY_START = date(2017, 1, 1)
HISTORY_END = date(2025, 12, 31)
MARKET_ROOT = "SPX"


def build_reference_returns(
    data_paths: DataPaths,
    symbols: list[str],
    start: date,
    end: date,
    force: bool,
    instrument: DeltaHedgedStraddle,
    calendar: TradingCalendar,
) -> None:
    band = ChainBand()
    output_dir = paths.VOL_REFERENCE_RETURNS_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    written = skipped = 0
    for count, symbol in enumerate([*symbols, MARKET_ROOT], 1):
        output_path = output_dir / f"{symbol}.parquet"
        if output_path.exists() and not force:
            skipped += 1
            continue
        is_index = symbol == MARKET_ROOT
        try:
            chain_df = (
                scan_options(data_paths, [symbol], start, end, index=is_index, with_oi=False)
                .filter(
                    (pl.col("expiration") - pl.col("date")).dt.total_days() <= band.max_dte,
                    (pl.col("strike") / pl.col("underlying") - 1.0).abs() <= band.max_moneyness,
                )
                .collect()
            )
        except FileNotFoundError:
            continue
        path_df = compute_reference_path(chain_df, instrument, calendar, start, end)
        if path_df.is_empty():
            path_df = pl.DataFrame(schema=REFERENCE_SCHEMA)
        write_atomic(path_df, output_path)
        written += 1
        if count % 25 == 0 or count == len(symbols) + 1:
            print(f"  reference {count}/{len(symbols) + 1}: {written} written, {skipped} skipped, {time.time() - started:.0f}s", flush=True)


def estimate_tables(data_paths: DataPaths, spec: FactorSpec) -> None:
    files = sorted(paths.VOL_REFERENCE_RETURNS_DIR.glob("*.parquet"))
    all_df = pl.read_parquet(files).select("date", "symbol", "pnl_per_vega", "dollar_vega")
    market_df = all_df.filter(pl.col("symbol") == MARKET_ROOT).select("date", "pnl_per_vega")
    returns_df = all_df.filter(pl.col("symbol") != MARKET_ROOT)
    sectors_df = scan_sectors(data_paths).collect()

    started = time.time()
    factor_returns_df = build_factor_returns(returns_df, sectors_df, spec, market_df, rolling_window=spec.window)
    write_atomic(factor_returns_df, paths.VOL_FACTOR_RETURNS)
    print(f"  factor returns: {factor_returns_df.height} rows, {factor_returns_df['factor'].n_unique()} factors ({time.time() - started:.0f}s)")

    covariances_df = estimate_rolling_factor_covariances(factor_returns_df, spec)
    write_atomic(covariances_df, paths.VOL_FACTOR_COVARIANCES)
    print(f"  factor covariances: {covariances_df['date'].n_unique()} sessions ({time.time() - started:.0f}s)")

    loadings_df, idio_df = estimate_rolling_loadings(returns_df, factor_returns_df, sectors_df, spec)
    write_atomic(loadings_df, paths.VOL_FACTOR_LOADINGS)
    write_atomic(idio_df, paths.VOL_IDIO_VOL)
    print(f"  loadings: {loadings_df.height} rows, idio vol: {idio_df.height} rows ({time.time() - started:.0f}s)")


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--start", type=date.fromisoformat, default=HISTORY_START)
    parser.add_argument("--end", type=date.fromisoformat, default=HISTORY_END)
    parser.add_argument("--symbols", default=None, help="comma-separated roots (default: every universe member)")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--force", action="store_true", help="rebuild reference files that already exist")
    parser.add_argument("--skip-reference", action="store_true", help="only re-estimate the factor tables")
    parser.add_argument("--target-dte", type=int, default=60)
    parser.add_argument("--roll-dte", type=int, default=20)
    parser.add_argument("--window", type=int, default=250)


def main(args: argparse.Namespace) -> None:
    data_paths = DataPaths(root=paths.DATA_STORE)
    calendar = TradingCalendar.from_data(data_paths)
    instrument = DeltaHedgedStraddle(StraddleConfig(target_dte=args.target_dte, roll_dte=args.roll_dte))
    spec = FactorSpec(window=args.window)

    if not args.skip_reference:
        if args.symbols:
            symbols = args.symbols.split(",")
        else:
            symbols = universe_symbols("option", args.start, args.end, limit=args.limit)
        print(f"reference straddle returns for {len(symbols)} symbols, {args.start}..{args.end}")
        build_reference_returns(data_paths, symbols, args.start, args.end, args.force, instrument, calendar)

    print("estimating factor tables")
    estimate_tables(data_paths, spec)
    for name in ("VOL_FACTOR_RETURNS", "VOL_FACTOR_LOADINGS", "VOL_FACTOR_COVARIANCES", "VOL_IDIO_VOL"):
        print(f"  wrote {getattr(paths, name)}")
