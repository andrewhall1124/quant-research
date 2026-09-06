"""Per-date option cross-section.

`ChainProvider.get(date)` returns canonical option rows for every symbol the
book might hold or trade on that date. The backtester calls it once per
session and hands each symbol's slice to the `Instrument`.

`StoreChainProvider` runs one lazy scan per call: the date predicate is
pushed into every symbol-year parquet, and the dte / moneyness band keeps
the collected frame to a few tens of thousands of rows. On this store that
is 1-3 seconds per session over ~500 names.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import polars as pl

from voltium.loaders import DataPaths, scan_options
from voltium.providers.base import Provider


@dataclass(frozen=True)
class ChainBand:
    max_dte: int = 130
    max_moneyness: float = 0.30


class ChainProvider(Provider):
    """`get(date) -> canonical option rows` (see `loaders.OPTION_SCHEMA`)."""


class StoreChainProvider(ChainProvider):
    def __init__(
        self,
        paths: DataPaths,
        symbols: list[str],
        band: ChainBand = ChainBand(),
        index: bool = False,
    ) -> None:
        self.paths = paths
        self.symbols = symbols
        self.band = band
        self.index = index

    def get(self, date_: dt.date) -> pl.DataFrame:
        return (
            scan_options(self.paths, self.symbols, date_, date_, index=self.index)
            .filter(
                (pl.col("expiration") - pl.col("date")).dt.total_days() <= self.band.max_dte,
                (pl.col("strike") / pl.col("underlying") - 1.0).abs() <= self.band.max_moneyness,
            )
            .collect()
        )


class FrameChainProvider(ChainProvider):
    """A chain held in memory; for tests and for small synthetic runs."""

    def __init__(self, chain_df: pl.DataFrame) -> None:
        self.chain_df = chain_df

    def get(self, date_: dt.date) -> pl.DataFrame:
        return self.chain_df.filter(pl.col("date") == date_)
