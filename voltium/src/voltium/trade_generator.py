"""Target dollar vega -> straddle contracts, with liquidity screens.

atium's `TradeGenerator` applies `TradingConstraint`s to a weight frame. The
same shape is kept — a list of composable `TradingConstraint`s — but the
input is a target in dollar vega and the output is a list of `Trade`s in
contracts, because rounding to a lot size and checking the quote are
per-contract questions the weight abstraction cannot ask.

Order of operations per symbol:

1. Resolve the unit: the held series if there is a position, else a fresh
   `Instrument.select`. A held series is never swapped outside a roll.
2. Screen (opening or adding only): straddle spread `(ask - bid) / mid`
   above `max_spread`, or either leg's OI below `min_oi`, drops the name.
3. Round `target_vega / unit_vega` to integer contracts, or keep the
   fraction when `fractional=True` (a research book that wants to see the
   signal before lot sizes get in the way).
4. No-trade band: skip if the dollar-vega change is under
   `band * |current dollar vega|`.

`TradeGeneratorConfig.research()` is the screen-free, fractional, no-band
configuration.
"""

from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod
from dataclasses import dataclass

import polars as pl

from voltium.instruments.base import Instrument, UnitMark, UnitSpec
from voltium.loaders import CONTRACT_MULTIPLIER
from voltium.portfolio import Portfolio


@dataclass(frozen=True)
class Trade:
    symbol: str
    unit: UnitSpec
    contracts: float  # signed change in contracts; integral unless `fractional`
    mark: UnitMark
    reason: str = "rebalance"


@dataclass(frozen=True)
class TradeGeneratorConfig:
    max_spread: float = 0.08
    min_oi: int = 100
    band: float = 0.10
    max_contracts: int | None = None
    fractional: bool = False

    @classmethod
    def research(cls) -> "TradeGeneratorConfig":
        """No liquidity screens, fractional contracts, no no-trade band."""
        return cls(max_spread=float("inf"), min_oi=0, band=0.0, fractional=True)


class TradingConstraint(ABC):
    """Post-optimization screen on one candidate trade. Return False to drop it."""

    @abstractmethod
    def allow(self, quotes_df: pl.DataFrame, unit: UnitSpec, mark: UnitMark, is_opening: bool) -> bool: ...


@dataclass(frozen=True)
class MaxSpread(TradingConstraint):
    max_spread: float

    def allow(self, quotes_df: pl.DataFrame, unit: UnitSpec, mark: UnitMark, is_opening: bool) -> bool:
        if not is_opening:
            return True
        return mark.mid > 0 and (mark.ask - mark.bid) / mark.mid <= self.max_spread


@dataclass(frozen=True)
class MinOpenInterest(TradingConstraint):
    min_oi: int

    def allow(self, quotes_df: pl.DataFrame, unit: UnitSpec, mark: UnitMark, is_opening: bool) -> bool:
        if not is_opening:
            return True
        legs = quotes_df.filter(
            pl.col("expiration") == unit.expiration,
            pl.col("strike") == unit.legs[0].strike,
            pl.col("right").is_in([leg.right for leg in unit.legs]),
        )
        if legs.height < len(unit.legs):
            return False
        oi = legs["oi"]
        if oi.null_count() > 0:
            return False
        return bool(oi.min() >= self.min_oi)


class TradeGenerator:
    def __init__(
        self,
        instrument: Instrument,
        config: TradeGeneratorConfig = TradeGeneratorConfig(),
        constraints: list[TradingConstraint] | None = None,
    ) -> None:
        self.instrument = instrument
        self.config = config
        if constraints is None:
            constraints = [MaxSpread(config.max_spread), MinOpenInterest(config.min_oi)]
        self.constraints = constraints

    def generate(
        self,
        targets_df: pl.DataFrame,
        chain_by_symbol: dict[str, pl.DataFrame],
        marks: dict[str, UnitMark],
        portfolio: Portfolio,
        date_: dt.date,
    ) -> list[Trade]:
        """`targets_df` is `(symbol, target_vega)`; symbols held but absent are closed.

        `marks` holds today's mark for every held position (keyed by symbol);
        `chain_by_symbol` today's canonical rows per symbol.
        """
        targets = dict(zip(targets_df["symbol"].to_list(), targets_df["target_vega"].to_list()))
        for symbol in portfolio.symbols():
            targets.setdefault(symbol, 0.0)
        # An optional `target_contracts` column bypasses the vega rounding; the
        # reference-path builder uses it to hold exactly one contract.
        fixed_contracts: dict[str, float] = {}
        if "target_contracts" in targets_df.columns:
            fixed_contracts = {
                s: float(c)
                for s, c in zip(targets_df["symbol"].to_list(), targets_df["target_contracts"].to_list())
                if c is not None
            }

        trades: list[Trade] = []
        for symbol in sorted(targets):
            target_vega = float(targets[symbol])
            quotes_df = chain_by_symbol.get(symbol)
            position = portfolio.positions.get(symbol)

            if position is not None:
                mark = marks.get(symbol)
                if mark is None:
                    continue  # unmarkable today; leave it alone
                unit = position.unit
                current = position.contracts
            else:
                if quotes_df is None or quotes_df.is_empty():
                    continue
                if target_vega == 0.0 and symbol not in fixed_contracts:
                    continue
                unit = self.instrument.select(quotes_df, date_)
                if unit is None:
                    continue
                mark = self.instrument.mark(quotes_df, unit, date_)
                if mark is None:
                    continue
                current = 0

            unit_vega = mark.vega * CONTRACT_MULTIPLIER
            if unit_vega <= 0:
                continue
            raw_contracts = target_vega / unit_vega if self.config.fractional else float(round(target_vega / unit_vega))
            target_contracts = fixed_contracts.get(symbol, raw_contracts)
            if self.config.max_contracts is not None:
                target_contracts = max(-self.config.max_contracts, min(self.config.max_contracts, target_contracts))
            change = target_contracts - current
            if change == 0:
                continue

            is_opening = abs(target_contracts) > abs(current)
            if is_opening and quotes_df is not None:
                if not all(c.allow(quotes_df, unit, mark, is_opening) for c in self.constraints):
                    continue

            current_vega = abs(current) * unit_vega
            if current != 0 and abs(change) * unit_vega < self.config.band * current_vega:
                continue

            trades.append(Trade(symbol=symbol, unit=unit, contracts=change, mark=mark))
        return trades
