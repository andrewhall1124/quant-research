"""Daily loop over a book of delta-hedged option units.

Per session, in this order:

1. Mark every held unit at the midpoint; option P&L is the change in mid
   times the multiplier and contracts. Stock hedge P&L is the hedge shares
   times the change in the underlying (close to close).
2. Roll any unit the instrument says to (`should_roll`: at or inside
   `roll_dte`, or drifted from the money): close at mid (pay the option cost
   model), select a fresh unit, reopen the same contracts (pay again).
3. On a rebalance session: strategy -> targets -> trade generator ->
   execute at mid, paying the option cost model on every contract traded.
4. Re-hedge once, on the final position of the day: set the stock hedge to
   `Instrument.hedge_shares(mark, contracts)` and pay the stock cost model on
   the shares traded. (The spec lists the hedge before the roll; doing it
   last means a roll does not pay for a hedge it immediately undoes.)
5. Record one row per held symbol. `exit_cost` is what closing the position
   at today's half-spread would cost; it is informational (nothing is
   charged) and feeds the net-of-cost decile table.

A unit with no usable quote carries its last mark (zero option P&L) and
counts a stale day; after `max_stale_days`, or on reaching expiration, it is
closed at the last mark with no cost and flagged in `event`.

The book is a `Portfolio`, which is plain data: pass one in to resume a run.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Literal

import polars as pl

from voltium.costs import CostModel, NoCost
from voltium.instruments.base import Instrument, UnitMark
from voltium.loaders import CONTRACT_MULTIPLIER
from voltium.portfolio import Portfolio, Position
from voltium.providers.calendar import CalendarProvider
from voltium.providers.chain import ChainProvider
from voltium.strategy import Strategy
from voltium.trade_generator import Trade, TradeGenerator

log = logging.getLogger(__name__)

RECORD_SCHEMA = {
    "date": pl.Date,
    "symbol": pl.Utf8,
    "expiration": pl.Date,
    "strike": pl.Float64,
    "contracts": pl.Int64,
    "hedge_shares": pl.Float64,
    "mid": pl.Float64,
    "underlying": pl.Float64,
    "option_pnl": pl.Float64,
    "hedge_pnl": pl.Float64,
    "option_cost": pl.Float64,
    "hedge_cost": pl.Float64,
    "dollar_vega": pl.Float64,
    "dollar_theta": pl.Float64,
    "exit_cost": pl.Float64,
    "book_delta": pl.Float64,
    "dte": pl.Int64,
    "stale": pl.Boolean,
    "event": pl.Utf8,
}


@dataclass(frozen=True)
class BacktestConfig:
    start: dt.date
    end: dt.date
    rebalance: Literal["daily", "weekly"] = "weekly"
    max_stale_days: int = 5


def is_rebalance_session(date_: dt.date, previous: dt.date | None, frequency: str) -> bool:
    if previous is None or frequency == "daily":
        return True
    if frequency == "weekly":
        return date_.isocalendar()[:2] != previous.isocalendar()[:2]
    raise ValueError(f"unknown rebalance frequency {frequency!r}")


class Backtester:
    def __init__(
        self,
        calendar: CalendarProvider,
        chain: ChainProvider,
        instrument: Instrument,
        strategy: Strategy,
        trade_generator: TradeGenerator,
        config: BacktestConfig,
        option_cost: CostModel | None = None,
        hedge_cost: CostModel | None = None,
    ) -> None:
        self.calendar = calendar
        self.chain = chain
        self.instrument = instrument
        self.strategy = strategy
        self.trade_generator = trade_generator
        self.config = config
        self.option_cost = option_cost or NoCost()
        self.hedge_cost = hedge_cost or NoCost()

    def run(self, portfolio: Portfolio | None = None, progress: bool = True) -> tuple[pl.DataFrame, Portfolio]:
        """Run from `config.start` (or the session after `portfolio.date`) to `config.end`.

        Returns the per-(date, symbol) records and the final portfolio.
        """
        portfolio = portfolio or Portfolio()
        start = self.config.start
        if portfolio.date is not None and portfolio.date >= start:
            start = self.calendar.get(portfolio.date, self.config.end)[1]
        sessions = self.calendar.get(start, self.config.end)
        previous_session = portfolio.date
        records: list[dict] = []
        for i, date_ in enumerate(sessions):
            day_records = self.step(date_, portfolio, is_rebalance_session(date_, previous_session, self.config.rebalance))
            records.extend(day_records)
            previous_session = date_
            if progress and (i % 10 == 0 or i == len(sessions) - 1):
                pnl = sum(r["option_pnl"] + r["hedge_pnl"] - r["option_cost"] - r["hedge_cost"] for r in day_records)
                log.info("%s  positions=%d  day pnl=%.0f  cum pnl=%.0f", date_, len(portfolio.positions), pnl, portfolio.cumulative_pnl)
        return pl.DataFrame(records, schema=RECORD_SCHEMA), portfolio

    # ------------------------------------------------------------------

    def step(self, date_: dt.date, portfolio: Portfolio, rebalance: bool) -> list[dict]:
        chain_df = self.chain.get(date_)
        chain_by_symbol = {k[0]: v for k, v in chain_df.partition_by("symbol", as_dict=True).items()}
        rows: dict[str, dict] = {}
        marks: dict[str, UnitMark] = {}

        # 1. mark and P&L
        for symbol in portfolio.symbols():
            position = portfolio.positions[symbol]
            quotes_df = chain_by_symbol.get(symbol)
            row = new_record(date_, position)
            mark = self.instrument.mark(quotes_df, position.unit, date_) if quotes_df is not None else None
            if mark is None:
                position.stale_days += 1
                row["stale"] = True
                underlying = float(quotes_df["underlying"][0]) if quotes_df is not None and not quotes_df.is_empty() else position.last_underlying
                row["hedge_pnl"] = position.hedge_shares * (underlying - position.last_underlying)
                position.last_underlying = underlying
                expired = (position.unit.expiration - date_).days <= 0
                if position.stale_days > self.config.max_stale_days or expired:
                    row["event"] = "forced_close"
                    row["hedge_cost"] += self.hedge_cost.compute(position.hedge_shares, 0, 0, underlying)
                    self.close_position(portfolio, symbol)
                    row.update(contracts=0, hedge_shares=0.0, dollar_vega=0.0, dollar_theta=0.0, book_delta=0.0)
                rows[symbol] = row
                continue
            position.stale_days = 0
            row["option_pnl"] = (mark.mid - position.last_mid) * CONTRACT_MULTIPLIER * position.contracts
            row["hedge_pnl"] = position.hedge_shares * (mark.underlying - position.last_underlying)
            update_marks(position, mark)
            marks[symbol] = mark
            rows[symbol] = row

        # 2. roll
        for symbol in list(marks):
            position = portfolio.positions[symbol]
            mark = marks[symbol]
            if not self.instrument.should_roll(position.unit, mark):
                continue
            row = rows[symbol]
            row["option_cost"] += self.option_cost.compute(position.contracts, mark.bid, mark.ask, mark.mid)
            contracts = position.contracts
            quotes_df = chain_by_symbol[symbol]
            new_unit = self.instrument.select(quotes_df, date_)
            new_mark = self.instrument.mark(quotes_df, new_unit, date_) if new_unit is not None else None
            if new_unit is None or new_mark is None:
                row["event"] = "roll_failed_closed"
                row["hedge_cost"] += self.hedge_cost.compute(position.hedge_shares, 0, 0, mark.underlying)
                self.close_position(portfolio, symbol)
                del marks[symbol]
                continue
            row["option_cost"] += self.option_cost.compute(contracts, new_mark.bid, new_mark.ask, new_mark.mid)
            row["event"] = "roll"
            position.unit = new_unit
            position.entry_date = date_
            update_marks(position, new_mark)
            marks[symbol] = new_mark

        # 3. rebalance
        if rebalance:
            targets_df = self.strategy.generate_targets(date_, chain_df, portfolio)
            trades = self.trade_generator.generate(targets_df, chain_by_symbol, marks, portfolio, date_)
            for trade in trades:
                self.execute(trade, date_, portfolio, rows, marks)

        # 4. re-hedge on the final position, then record
        for symbol in list(portfolio.symbols()):
            position = portfolio.positions[symbol]
            row = rows.setdefault(symbol, new_record(date_, position))
            mark = marks.get(symbol)
            if position.contracts == 0:
                row["hedge_cost"] += self.hedge_cost.compute(position.hedge_shares, 0, 0, position.last_underlying)
                self.close_position(portfolio, symbol)
                row.update(contracts=0, hedge_shares=0.0, dollar_vega=0.0, dollar_theta=0.0, book_delta=0.0)
                continue
            if mark is not None:
                target_shares = self.instrument.hedge_shares(mark, position.contracts)
                row["hedge_cost"] += self.hedge_cost.compute(target_shares - position.hedge_shares, 0, 0, mark.underlying)
                position.hedge_shares = target_shares
            fill_record(row, position, mark)

        day_pnl = sum(r["option_pnl"] + r["hedge_pnl"] for r in rows.values())
        day_cost = sum(r["option_cost"] + r["hedge_cost"] for r in rows.values())
        portfolio.cumulative_pnl += day_pnl - day_cost
        portfolio.cumulative_cost += day_cost
        portfolio.date = date_
        return [rows[s] for s in sorted(rows)]

    def execute(self, trade: Trade, date_: dt.date, portfolio: Portfolio, rows: dict[str, dict], marks: dict[str, UnitMark]) -> None:
        mark = trade.mark
        position = portfolio.positions.get(trade.symbol)
        cost = self.option_cost.compute(trade.contracts, mark.bid, mark.ask, mark.mid)
        if position is None:
            position = Position(
                unit=trade.unit,
                contracts=trade.contracts,
                hedge_shares=0.0,
                entry_date=date_,
                last_mid=mark.mid,
                last_underlying=mark.underlying,
                last_vega=mark.vega,
                last_delta=mark.delta,
            )
            portfolio.positions[trade.symbol] = position
            marks[trade.symbol] = mark
            row = rows.setdefault(trade.symbol, new_record(date_, position))
            row["event"] = "open"
        else:
            position.contracts += trade.contracts
            row = rows[trade.symbol]
            if position.contracts == 0:
                row["event"] = "close"
            elif row["event"] == "":
                row["event"] = "trade"
        row["option_cost"] += cost

    @staticmethod
    def close_position(portfolio: Portfolio, symbol: str) -> None:
        del portfolio.positions[symbol]


def update_marks(position: Position, mark: UnitMark) -> None:
    position.last_mid = mark.mid
    position.last_underlying = mark.underlying
    position.last_vega = mark.vega
    position.last_delta = mark.delta


def new_record(date_: dt.date, position: Position) -> dict:
    return {
        "date": date_,
        "symbol": position.symbol,
        "expiration": position.unit.expiration,
        "strike": position.unit.legs[0].strike,
        "contracts": position.contracts,
        "hedge_shares": position.hedge_shares,
        "mid": position.last_mid,
        "underlying": position.last_underlying,
        "option_pnl": 0.0,
        "hedge_pnl": 0.0,
        "option_cost": 0.0,
        "hedge_cost": 0.0,
        "dollar_vega": position.last_vega * CONTRACT_MULTIPLIER * position.contracts,
        "dollar_theta": 0.0,
        "exit_cost": 0.0,
        "book_delta": 0.0,
        "dte": (position.unit.expiration - date_).days,
        "stale": False,
        "event": "",
    }


def fill_record(row: dict, position: Position, mark: UnitMark | None) -> None:
    row["expiration"] = position.unit.expiration
    row["strike"] = position.unit.legs[0].strike
    row["contracts"] = position.contracts
    row["hedge_shares"] = position.hedge_shares
    row["mid"] = position.last_mid
    row["underlying"] = position.last_underlying
    row["dollar_vega"] = position.last_vega * CONTRACT_MULTIPLIER * position.contracts
    row["dte"] = (position.unit.expiration - row["date"]).days
    if mark is not None:
        row["dollar_theta"] = mark.theta * CONTRACT_MULTIPLIER * position.contracts
        row["exit_cost"] = mark.half_spread * CONTRACT_MULTIPLIER * abs(position.contracts)
        row["book_delta"] = mark.delta * CONTRACT_MULTIPLIER * position.contracts + position.hedge_shares
    else:
        row["book_delta"] = position.last_delta * CONTRACT_MULTIPLIER * position.contracts + position.hedge_shares
