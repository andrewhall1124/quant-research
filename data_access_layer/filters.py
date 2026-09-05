"""The three things every loader does: check, window, deliver.

A loader is a pure read. It resolves the files, filters to `[start, end]`, and
hands back a LazyFrame when `lazy=True` or a collected DataFrame otherwise —
nothing else. Screening, joining and deriving belong to the caller, or to the
named transforms next to each loader.

Keeping the filter lazy is what makes that cheap: polars pushes the date
predicate into the parquet scan, so a one-month window over a year-stamped
chain directory reads only the row groups it needs.
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


def in_window(
    frame: pl.LazyFrame, start: date | None, end: date | None, column: str = "date"
) -> pl.LazyFrame:
    """Filter to an inclusive date window. `None` on either side means open."""
    if start is not None:
        frame = frame.filter(pl.col(column) >= start)
    if end is not None:
        frame = frame.filter(pl.col(column) <= end)
    return frame


def deliver(frame: pl.LazyFrame, lazy: bool) -> pl.LazyFrame | pl.DataFrame:
    """Every loader's last line: stay lazy, or collect."""
    return frame if lazy else frame.collect()
