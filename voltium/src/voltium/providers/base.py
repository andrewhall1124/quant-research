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
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path

import polars as pl

log = logging.getLogger(__name__)


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


def materialize_by_symbol(
    build: Callable[[str], pl.LazyFrame],
    symbols: list[str],
    cache_dir: Path | None = None,
    tag: str = "",
    refresh: bool = False,
    schema: dict | None = None,
) -> pl.DataFrame:
    """Collect a per-symbol lazy panel one symbol at a time and concatenate.

    One query over 500 symbol-year files by eight years is a single
    streaming plan that can hold tens of gigabytes in flight; one symbol's
    chain is ~100k rows a year. Each symbol is collected on its own (and
    cached under `cache_dir/<symbol>_<tag>.parquet` when given), so memory
    is bounded by the largest single chain. `build(symbol)` returns the lazy
    frame for that symbol; a `FileNotFoundError` skips it.
    """
    pieces = []
    for count, symbol in enumerate(symbols, 1):
        cache_path = cache_dir / f"{symbol}_{tag}.parquet" if cache_dir else None
        if cache_path is not None and cache_path.exists() and not refresh:
            pieces.append(pl.read_parquet(cache_path))
            continue
        try:
            panel_df = build(symbol).collect()
        except FileNotFoundError:
            continue
        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            panel_df.write_parquet(cache_path)
        pieces.append(panel_df)
        if count % 100 == 0 or count == len(symbols):
            log.info("materialize: %d/%d symbols", count, len(symbols))
    if not pieces:
        return pl.DataFrame(schema=schema)
    return pl.concat(pieces)
