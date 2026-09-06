"""The provider pattern, carried over from atium.

atium's providers are `Protocol`s with `get(date_) -> DataFrame`. voltium keeps
the same call shape but makes the base an ABC so the concrete providers have
one place to share the "hold a panel, filter by date" implementation.

Every provider returns a DataFrame with at least a `date` column and, for
cross-sectional data, a `symbol` column. Column names beyond that are fixed
per subclass and documented there.
"""

from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod
from pathlib import Path

import polars as pl


class Provider(ABC):
    """Anything that answers `get(date)` with a cross-section for that date."""

    @abstractmethod
    def get(self, date_: dt.date) -> pl.DataFrame: ...


class PanelProvider(Provider):
    """A provider backed by an in-memory panel with a `date` column.

    Panels here are small: one row per (date, symbol), never per contract.
    Subclasses build the panel however they like (usually a lazy pipeline
    collected once) and hand it to `__init__`.
    """

    def __init__(self, panel_df: pl.DataFrame) -> None:
        if "date" not in panel_df.columns:
            raise ValueError("panel needs a `date` column")
        self.panel_df = panel_df.sort("date")

    def get(self, date_: dt.date) -> pl.DataFrame:
        return self.panel_df.filter(pl.col("date") == date_)

    def dates(self) -> list[dt.date]:
        return self.panel_df["date"].unique().sort().to_list()


def materialize(frame: pl.LazyFrame, cache_path: Path | None = None, refresh: bool = False) -> pl.DataFrame:
    """Collect a lazy panel, optionally through a parquet cache.

    The panel builders in `providers/` are lazy end to end; this is the one
    place a plan is executed. With a `cache_path`, a later call with the same
    path reads the file instead of re-running the scan.
    """
    if cache_path is not None and cache_path.exists() and not refresh:
        return pl.read_parquet(cache_path)
    panel_df = frame.collect(engine="streaming")
    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        panel_df.write_parquet(cache_path)
    return panel_df
