"""Panels for the earnings-adjusted variance premium.

Reuses the demo's panel build for everything that is not earnings-aware
(raw surface, close-to-close realized vol, HAR forecast, stock features,
sectors, reference returns), then adds:

* the **adjusted surface** — ATM pillars with `n_events × J` of total
  variance removed from every expiration that spans an announcement,
  interpolated to 30/60/90 days as usual — with the jump estimate and its
  method per (date, symbol);
* **ex-earnings realized vol** — close-to-close with the event sessions'
  squared returns left out of every window — and a HAR forecast fit on it.

Run from the project root:

    uv run python -m research.earnings_adjusted_vrp.panel [--start 2018-07-02 --end 2025-06-30 --refresh]
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import logging
import sys
import time
from pathlib import Path

import polars as pl

from voltium.loaders import scan_options, scan_spot_from_chains
from voltium.providers.base import materialize_by_symbol
from voltium.providers.earnings import TermStructureEarningsAdjuster, load_earnings_events
from voltium.providers.realized_vol import HARConfig, HARForecaster, compute_close_to_close_panel
from voltium.providers.surface import SurfaceConfig, build_surface_panel

STUDY_DIR = Path(__file__).resolve().parent
RESULTS_DIR = STUDY_DIR / "results"
REPO_DIR = STUDY_DIR.parents[1]
DEMO_PATH = REPO_DIR / "voltium" / "demos" / "level_book.py"
CACHE_DIR = REPO_DIR / "voltium" / ".cache"

log = logging.getLogger("earnings_adjusted_vrp")


def load_demo_module():
    spec = importlib.util.spec_from_file_location("level_book", DEMO_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses in the module resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


def demo_args(start: dt.date, end: dt.date, refresh: bool) -> argparse.Namespace:
    return argparse.Namespace(start=start, end=end, rebalance="weekly", refresh=refresh, limit=None, in_process=False, rv_source="close")


def build_adjusted_surface(paths, symbol: str, events_df: pl.DataFrame, start: dt.date, end: dt.date) -> pl.LazyFrame:
    """`(date, symbol, iv30, iv60, iv90, next_event, days_to_event, jump_var, method)` for one symbol."""
    adjuster = TermStructureEarningsAdjuster(events_df.filter(pl.col("symbol") == symbol))
    surface_df = build_surface_panel(scan_options(paths, [symbol], start, end, with_oi=False), SurfaceConfig(), adjuster).collect()
    if adjuster.jump_df is None:
        return surface_df.lazy()
    return surface_df.join(adjuster.jump_df.drop("symbol"), on="date", how="left").lazy()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--start", type=dt.date.fromisoformat, default=dt.date(2018, 7, 2))
    parser.add_argument("--end", type=dt.date.fromisoformat, default=dt.date(2025, 6, 30))
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    RESULTS_DIR.mkdir(exist_ok=True)
    started = time.time()

    demo = load_demo_module()
    panels = demo.build_panels(demo_args(args.start, args.end, args.refresh))
    paths, calendar, symbols = panels.paths, panels.calendar, panels.symbols
    history_start, forward_end = panels.history_start, panels.forward_end
    spot_start = calendar.sessions[0]

    events_df = load_earnings_events(paths, calendar)
    covered = sorted(set(symbols) & set(events_df["symbol"].to_list()))
    log.info("%d of %d symbols have earnings dates", len(covered), len(symbols))

    surface_ex_df = materialize_by_symbol(
        lambda symbol: build_adjusted_surface(paths, symbol, events_df, history_start, forward_end),
        covered, CACHE_DIR / "surface_ex", tag=f"{history_start}_{forward_end}", refresh=args.refresh,
    ).sort("date", "symbol")
    log.info("adjusted surface: %d rows (%.0fs)", surface_ex_df.height, time.time() - started)

    exclude_df = events_df.select(pl.col("event_session").alias("date"), "symbol")
    rv_ex_df = materialize_by_symbol(
        lambda symbol: compute_close_to_close_panel(
            scan_spot_from_chains(paths, [symbol], spot_start, forward_end), exclude_df=exclude_df.filter(pl.col("symbol") == symbol)
        ),
        covered, CACHE_DIR / "rv_close_ex", tag=f"{spot_start}_{forward_end}", refresh=args.refresh,
    ).sort("symbol", "date")
    spx_rv_df = compute_close_to_close_panel(scan_spot_from_chains(paths, ["SPX"], spot_start, forward_end, index=True, with_splits=False)).collect()
    forecaster = HARForecaster(HARConfig(forward_window=60))
    forecast_ex_df = forecaster.forecast(rv_ex_df, apply_df=spx_rv_df).filter(pl.col("symbol") != "SPX")
    log.info("ex-earnings HAR: %d forecasts, last fit %s (%.0fs)", forecast_ex_df.height, forecaster.coefficients_df.tail(1).to_dicts(), time.time() - started)

    surface_ex_df.write_parquet(RESULTS_DIR / "surface_ex.parquet")
    forecast_ex_df.write_parquet(RESULTS_DIR / "forecast_ex.parquet")
    events_df.write_parquet(RESULTS_DIR / "events.parquet")
    panels.surface_df.filter(pl.col("symbol").is_in(covered)).write_parquet(RESULTS_DIR / "surface_raw.parquet")
    panels.forecast_df.filter(pl.col("symbol").is_in(covered)).write_parquet(RESULTS_DIR / "forecast_raw.parquet")
    panels.spx_surface_df.write_parquet(RESULTS_DIR / "spx_surface.parquet")
    panels.spx_forecast_df.write_parquet(RESULTS_DIR / "spx_forecast.parquet")
    panels.features.panel_df.filter(pl.col("symbol").is_in(covered)).write_parquet(RESULTS_DIR / "features.parquet")
    panels.sectors_df.write_parquet(RESULTS_DIR / "sectors.parquet")
    panels.reference.panel_df.filter(pl.col("symbol").is_in(covered)).write_parquet(RESULTS_DIR / "reference.parquet")
    pl.DataFrame({"key": ["start", "end", "history_start", "forward_end"], "value": [str(args.start), str(args.end), str(history_start), str(forward_end)]}).write_parquet(RESULTS_DIR / "meta.parquet")
    log.info("panels written to %s (%.0fs)", RESULTS_DIR, time.time() - started)


if __name__ == "__main__":
    main()
