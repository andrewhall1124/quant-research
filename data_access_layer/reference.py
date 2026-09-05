"""Index levels, treasury yield indices and the overnight rate.

Cheap to re-pull in full, so each is one unstamped table rather than the
year-stamped directories the per-symbol option pulls use.
"""

from datetime import date

import polars as pl

from data_access_layer import paths
from data_access_layer.filters import in_window, only_symbols, require


def load_indices(
    symbols: str | list[str] | None = None,
    start: date | None = None,
    end: date | None = None,
) -> pl.DataFrame:
    """EOD index levels: SPX, RUT, OEX, XSP and the VIX complex."""
    frame = pl.scan_parquet(require(paths.INDICES, "data_pipelines.reference"))
    return in_window(only_symbols(frame, symbols), start, end).sort("date", "symbol").collect()


def load_index_closes(
    symbols: list[str],
    start: date | None = None,
    end: date | None = None,
) -> pl.DataFrame:
    """Index closes pivoted wide — one column per symbol, one row per date.

    This is the shape most time-series work wants (e.g. SPX beside VIX).
    """
    long_df = load_indices(symbols, start, end)
    return long_df.pivot(on="symbol", index="date", values="close").sort("date")


def load_yields(
    tenors: str | list[str] | None = None,
    start: date | None = None,
    end: date | None = None,
) -> pl.DataFrame:
    """CBOE treasury yield indices (13w, 5y, 10y, 30y) as decimal yields."""
    frame = pl.scan_parquet(require(paths.YIELDS, "data_pipelines.reference"))
    return in_window(only_symbols(frame, tenors, "tenor"), start, end).sort("date", "tenor").collect()



def load_rates(start: date | None = None, end: date | None = None) -> pl.DataFrame:
    """Overnight SOFR as a decimal rate. Tier-capped at 2024; see `load_yields`
    for a curve that reaches the whole option history."""
    frame = pl.scan_parquet(require(paths.RATES, "data_pipelines.reference"))
    return in_window(frame, start, end).sort("date", "symbol").collect()


