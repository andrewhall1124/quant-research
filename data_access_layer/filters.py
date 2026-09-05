"""The filtering vocabulary every loader shares.

`in_window` and `only_symbols` take and return LazyFrames, so a loader can
compose them and let polars push the predicates down into the parquet scan —
asking for one symbol out of a 1.5 GB chain directory reads only that symbol's
row groups.
"""

from datetime import date

import polars as pl

from data_access_layer.errors import MissingDataset


def require(path, pipeline: str):
    if not path.exists():
        raise MissingDataset(
            f"{path} not found. Create it with:\n  uv run python -m {pipeline}"
        )
    return path


def in_window(frame: pl.LazyFrame, start: date | None, end: date | None) -> pl.LazyFrame:
    if start is not None:
        frame = frame.filter(pl.col("date") >= start)
    if end is not None:
        frame = frame.filter(pl.col("date") <= end)
    return frame


def only_symbols(
    frame: pl.LazyFrame, symbols: str | list[str] | None, column: str = "symbol"
) -> pl.LazyFrame:
    if symbols is None:
        return frame
    wanted = [symbols] if isinstance(symbols, str) else list(symbols)
    return frame.filter(pl.col(column).is_in(wanted))
