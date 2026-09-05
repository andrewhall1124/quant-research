"""Index levels, treasury yield indices and the overnight rate.

Cheap to re-pull in full, so each is one unstamped table rather than the
year-stamped directories the per-symbol option pulls use.
"""

from datetime import date

import polars as pl

from data_access_layer import paths
from data_access_layer.filters import deliver, in_window, require


def load_indices(
    start: date | None = None, end: date | None = None, lazy: bool = False
) -> pl.LazyFrame | pl.DataFrame:
    """EOD index levels, long: SPX, RUT, OEX, XSP and the VIX complex."""
    frame = pl.scan_parquet(require(paths.INDICES, "data_pipelines.reference")).sort(
        "date", "symbol"
    )
    return deliver(in_window(frame, start, end), lazy)


def load_yields(
    start: date | None = None, end: date | None = None, lazy: bool = False
) -> pl.LazyFrame | pl.DataFrame:
    """CBOE treasury yield indices (13w, 5y, 10y, 30y) as decimal yields."""
    frame = pl.scan_parquet(require(paths.YIELDS, "data_pipelines.reference")).sort(
        "date", "tenor"
    )
    return deliver(in_window(frame, start, end), lazy)


def load_rates(
    start: date | None = None, end: date | None = None, lazy: bool = False
) -> pl.LazyFrame | pl.DataFrame:
    """Overnight SOFR as a decimal rate.

    Tier-capped at 2024; see `load_yields` for a curve that reaches the whole
    option history.
    """
    frame = pl.scan_parquet(require(paths.RATES, "data_pipelines.reference")).sort(
        "date", "symbol"
    )
    return deliver(in_window(frame, start, end), lazy)
