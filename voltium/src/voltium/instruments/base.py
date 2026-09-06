"""Instrument: how a symbol's exposure is expressed in listed contracts.

This is where the equity abstraction stops carrying over. atium holds a
weight per ticker and the "instrument" is the share. Here a unit of exposure
is a package of option legs plus a stock hedge, the package has to be chosen
from a chain (which expiration, which strike), it decays, and it must be
rolled. `Instrument` owns those decisions; the backtester and the risk
model's reference-return builder both go through it so the marking logic
exists in exactly one place.

Every method takes `quotes_df`: canonical option rows for **one symbol on
one date** (see `loaders.OPTION_SCHEMA`). Nothing here reads the store.
"""

from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod
from dataclasses import dataclass

import polars as pl


@dataclass(frozen=True)
class LegSpec:
    """One listed contract. Unsigned; direction lives on the position."""

    expiration: dt.date
    strike: float
    right: str  # "C" or "P"

    def key(self) -> tuple[dt.date, float, str]:
        return (self.expiration, self.strike, self.right)


@dataclass(frozen=True)
class UnitSpec:
    """One unit of the instrument: a fixed bundle of legs, `ratio` contracts each."""

    symbol: str
    legs: tuple[LegSpec, ...]
    ratios: tuple[int, ...]

    @property
    def expiration(self) -> dt.date:
        return min(leg.expiration for leg in self.legs)


@dataclass(frozen=True)
class UnitMark:
    """Everything the backtester needs about one unit on one date.

    All per-share for one unit (one straddle = one call + one put, so
    `mid` is the straddle midpoint per share). Multiply by the contract
    multiplier for dollars per contract.
    """

    date: dt.date
    mid: float
    bid: float
    ask: float
    delta: float
    vega: float  # per share per vol point
    theta: float  # per share per calendar day
    underlying: float
    dte: int
    complete: bool  # every leg had a usable quote

    @property
    def half_spread(self) -> float:
        return 0.5 * (self.ask - self.bid)


class Instrument(ABC):
    """Select, mark, hedge and roll one unit of exposure for one symbol."""

    @abstractmethod
    def select(self, quotes_df: pl.DataFrame, date_: dt.date) -> UnitSpec | None:
        """Choose the legs to enter on `date_`, or None if nothing qualifies."""

    @abstractmethod
    def mark(self, quotes_df: pl.DataFrame, unit: UnitSpec, date_: dt.date) -> UnitMark | None:
        """Mark one unit at the midpoint; None if no leg is quoted at all."""

    @abstractmethod
    def should_roll(self, unit: UnitSpec, mark: UnitMark) -> bool:
        """Close this unit and open a fresh one today (time to expiry, drift, ...)."""

    @abstractmethod
    def hedge_shares(self, mark: UnitMark, contracts: float) -> float:
        """Stock position that neutralises the delta of `contracts` units."""
