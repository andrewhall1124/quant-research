"""Point-in-time S&P 500 membership as `(date, symbol)`.

The store has a genuinely point-in-time membership table, so no static-list
wrapper is needed. `PointInTimeUniverse` also drops members that have no
option chain on disk for the year in question: a name the optimizer cannot
trade should not be in the cross-section it normalises against.

Sectors, by contrast, *are* static (today's GICS table); see `SectorProvider`.
"""

from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod

import polars as pl

from voltium.loaders import DataPaths, scan_sectors, scan_universe
from voltium.providers.base import PanelProvider, Provider


class UniverseProvider(Provider):
    """`get(date) -> (date, symbol)`."""

    @abstractmethod
    def symbols(self, start: dt.date, end: dt.date) -> list[str]:
        """Every symbol that is a member on at least one session in the window."""


class PointInTimeUniverse(UniverseProvider, PanelProvider):
    def __init__(self, paths: DataPaths, start: dt.date, end: dt.date, require_chain: bool = True) -> None:
        panel_df = scan_universe(paths, start, end).collect()
        if require_chain:
            on_disk = {
                s
                for year in range(start.year, end.year + 1)
                for s in paths.available_symbols("option_greeks", year)
            }
            panel_df = panel_df.filter(pl.col("symbol").is_in(sorted(on_disk)))
        PanelProvider.__init__(self, panel_df.sort("date", "symbol"))

    def symbols(self, start: dt.date, end: dt.date) -> list[str]:
        return (
            self.panel_df.filter(pl.col("date").is_between(start, end))["symbol"]
            .unique()
            .sort()
            .to_list()
        )


class SectorProvider(ABC):
    """`get() -> (symbol, sector)`; undated because the source is a snapshot."""

    @abstractmethod
    def get(self) -> pl.DataFrame: ...


class StaticSectorProvider(SectorProvider):
    """GICS sector of today's constituents. Known v1 limitation: a name that
    left the index has no sector and is dropped from sector factors; a name
    that changed sector carries its current one."""

    def __init__(self, paths: DataPaths) -> None:
        self.sectors_df = scan_sectors(paths).collect()

    def get(self) -> pl.DataFrame:
        return self.sectors_df
