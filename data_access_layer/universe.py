"""Point-in-time S&P 500 membership."""

from datetime import date

import polars as pl

from data_access_layer import paths
from data_access_layer.filters import require


def load_universe(
    as_of: date | None = None, with_history: bool = False
) -> pl.DataFrame:
    """Point-in-time S&P 500 membership, one row per (date, ticker).

    `with_history` prepends `universe_history.parquet`, the reconstruction for
    the backfill years. It is a separate file because membership that far back
    is walked further through Wikipedia's changes table and carries more
    accumulated error than the 2025 sample.
    """
    frame = pl.scan_parquet(require(paths.UNIVERSE, "data_pipelines.universe"))
    if with_history:
        history_path = require(
            paths.UNIVERSE_HISTORY, "data_pipelines.universe --history"
        )
        frame = pl.concat(
            [pl.scan_parquet(history_path), frame], how="vertical_relaxed"
        ).unique(["date", "ticker"])
    if as_of is not None:
        frame = frame.filter(pl.col("date") == as_of)
    return frame.sort("date", "ticker").collect()


