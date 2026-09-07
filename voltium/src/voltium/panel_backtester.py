"""Fast backtest over a precomputed structure-return panel.

The chain backtester (`backtester.py`) scans the option chain every session
and marks every leg. Most of what it computes is linear in the number of
contracts held, and the reference-return builder has already computed it
for one unit of the instrument per name: `pnl_per_vega`, `cost_per_vega`,
`exit_cost_per_vega`, `entry_vega`, `dollar_vega` (`REFERENCE_SCHEMA`,
`providers/straddle_returns.py`). This engine holds `units` of that
reference unit per name and reads everything off the panel:

    option_pnl(t)  = units × entry_vega(t) × pnl_per_vega(t)
    holding cost   = |units| × entry_vega(t) × cost_per_vega(t)      rolls and hedging
    trade cost     = |Δunits| × half_spread(t)                        at a rebalance
    dollar_vega(t) = units × dollar_vega(t)                          mark to market

where `half_spread(t)` is `exit_cost_per_vega × inception vega` of the unit
held at today's close. Because the reference path holds a constant one
unit through its rolls, `units` are contracts, and between rebalances the
numbers are identical to the chain backtester's for a fractional book with
no screens (`tests/test_panel_backtester.py` checks P&L, vega and holding
costs to 1e-6 through a roll). What is *not* reproduced: integer
contracts, the no-trade band, the liquidity screens, the per-share hedge
fill on a resize (the panel carries no per-unit delta; it is under 1% of
the option cost), and a name the chain engine could size but whose
reference unit was never opened. The reference path also fixes the
instrument and its roll rule; a different `StraddleConfig` needs a
different panel (`data_pipelines.cli vol-risk-model`).

A name with no panel row today (the reference unit was force-closed, or the
name is not in the store yet) is treated as closed: units go to zero with
no P&L and no cost, and it can be re-entered at the next rebalance once a
row is back. A name whose row is an `open` has no `pnl_per_vega` today but
is tradeable at today's close.

Costs are recorded at `cost_fraction` of the panel's full half-spread and
hedge cost (default 0, the research configuration); `exit_cost` is always
recorded, as in the chain engine. Records use `RECORD_SCHEMA` so
`BacktestResults` reads them unchanged: option and hedge P&L are not
separable in the panel, so all P&L is `option_pnl` and all cost
`option_cost`; `expiration`, `strike`, `hedge_shares` and `book_delta` are
null or zero.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Literal

import numpy as np
import polars as pl

from voltium.backtester import RECORD_SCHEMA, is_rebalance_session
from voltium.providers.base import PanelProvider
from voltium.providers.calendar import CalendarProvider
from voltium.strategy import Strategy

log = logging.getLogger(__name__)

PANEL_COLUMNS = ("pnl_per_vega", "cost_per_vega", "exit_cost_per_vega", "entry_vega", "dollar_vega", "dollar_theta", "mid", "underlying", "dte")


@dataclass(frozen=True)
class PanelBacktestConfig:
    start: dt.date
    end: dt.date
    rebalance: Literal["daily", "weekly"] = "weekly"
    cost_fraction: float = 0.0  # share of the panel's half-spread and hedge cost to charge


class VegaBook:
    """The book as `units` of the reference unit per name, marked at today's unit vega."""

    def __init__(self, symbols: list[str]) -> None:
        self.symbol_list = symbols
        self.index = {s: i for i, s in enumerate(symbols)}
        self.units = np.zeros(len(symbols))
        self.unit_vega = np.zeros(len(symbols))  # today's dollar vega of one unit
        self.date: dt.date | None = None
        self.cumulative_pnl = 0.0
        self.cumulative_cost = 0.0

    def symbols(self) -> list[str]:
        return [self.symbol_list[i] for i in np.flatnonzero(self.units)]

    def dollar_vega(self, symbol: str) -> float:
        i = self.index.get(symbol)
        return float(self.units[i] * self.unit_vega[i]) if i is not None else 0.0


class PanelBacktester:
    def __init__(self, calendar: CalendarProvider, reference: PanelProvider, strategy: Strategy, config: PanelBacktestConfig) -> None:
        self.calendar = calendar
        self.strategy = strategy
        self.config = config
        panel_df = reference.panel_df.filter(pl.col("date").is_between(config.start, config.end))
        self.symbols = panel_df["symbol"].unique().sort().to_list()
        self.dates = panel_df["date"].unique().sort().to_list()
        self.wide = {column: pivot(panel_df, column, self.dates, self.symbols) for column in PANEL_COLUMNS}
        self.event = pivot(panel_df, "event", self.dates, self.symbols, fill="")
        self.date_index = {d: i for i, d in enumerate(self.dates)}

    def run(self, progress: bool = True) -> tuple[pl.DataFrame, VegaBook]:
        book = VegaBook(self.symbols)
        sessions = self.calendar.get(self.config.start, self.config.end)
        previous_session = None
        records: list[dict] = []
        for i, date_ in enumerate(sessions):
            day_records = self.step(date_, book, is_rebalance_session(date_, previous_session, self.config.rebalance))
            records.extend(day_records)
            previous_session = date_
            if progress and (i % 50 == 0 or i == len(sessions) - 1):
                pnl = sum(r["option_pnl"] - r["option_cost"] for r in day_records)
                log.info("%s  positions=%d  day pnl=%.0f  cum pnl=%.0f", date_, len(book.symbols()), pnl, book.cumulative_pnl)
        return pl.DataFrame(records, schema=RECORD_SCHEMA), book

    def step(self, date_: dt.date, book: VegaBook, rebalance: bool) -> list[dict]:
        n = len(self.symbols)
        row = self.date_index.get(date_)
        if row is None:  # no panel row for anyone today: every held unit is closed
            present = np.zeros(n, dtype=bool)
            values = {c: np.full(n, np.nan) for c in PANEL_COLUMNS}
            events = np.full(n, "", dtype=object)
        else:
            values = {c: self.wide[c][row] for c in PANEL_COLUMNS}
            events = self.event[row]
            present = ~np.isnan(values["dollar_vega"])
        held_before = book.units != 0

        # 1. mark and P&L on the units held into today
        pnl = np.where(present, book.units * nan0(values["entry_vega"]) * nan0(values["pnl_per_vega"]), 0.0)
        cost = np.where(present, np.abs(book.units) * nan0(values["entry_vega"]) * nan0(values["cost_per_vega"]), 0.0) * self.config.cost_fraction
        closed = held_before & ~present
        book.units[closed] = 0.0
        book.unit_vega = np.where(present, values["dollar_vega"], 0.0)
        inception_vega = np.where(np.isin(events, ["open", "roll"]), values["dollar_vega"], values["entry_vega"])
        half_spread = nan0(values["exit_cost_per_vega"]) * nan0(inception_vega)
        event = np.where(closed, "forced_close", events).astype(object)

        # 2. rebalance
        traded = np.zeros(n, dtype=bool)
        if rebalance:
            chain_df = pl.DataFrame({"symbol": [self.symbols[i] for i in np.flatnonzero(present)]}, schema={"symbol": pl.Utf8})
            book.date = date_
            targets_df = self.strategy.generate_targets(date_, chain_df, book)
            target = np.zeros(n)
            has_target = np.zeros(n, dtype=bool)
            for symbol, vega in zip(targets_df["symbol"].to_list(), targets_df["target_vega"].to_list()):
                i = book.index.get(symbol)
                if i is not None:
                    target[i], has_target[i] = float(vega), True
            # held names absent from the targets close, as in the trade generator
            has_target |= book.units != 0
            sizable = present & has_target & (values["dollar_vega"] > 0)
            new_units = np.where(sizable, target / np.where(sizable, values["dollar_vega"], 1.0), book.units)
            delta = new_units - book.units
            traded = delta != 0
            cost = cost + np.abs(delta) * half_spread * self.config.cost_fraction
            opened, closed_now = traded & ~held_before, traded & (new_units == 0)
            event = np.where(opened, "open", np.where(closed_now, "close", np.where(traded & (event == ""), "trade", event)))
            book.units = new_units

        # 3. record every name held at any point today
        active = np.flatnonzero(held_before | (book.units != 0) | traded)
        records = []
        for i in active:
            units = float(book.units[i])
            records.append(
                {
                    "date": date_,
                    "symbol": self.symbols[i],
                    "expiration": None,
                    "strike": None,
                    "contracts": units,
                    "hedge_shares": 0.0,
                    "mid": none_if_nan(values["mid"][i]),
                    "underlying": none_if_nan(values["underlying"][i]),
                    "option_pnl": float(pnl[i]),
                    "hedge_pnl": 0.0,
                    "option_cost": float(cost[i]),
                    "hedge_cost": 0.0,
                    "dollar_vega": units * float(book.unit_vega[i]),
                    "dollar_theta": units * float(nan0(values["dollar_theta"][i])),
                    "exit_cost": abs(units) * float(half_spread[i]),
                    "book_delta": 0.0,
                    "dte": None if np.isnan(values["dte"][i]) else int(values["dte"][i]),
                    "stale": not bool(present[i]),
                    "event": str(event[i]),
                }
            )
        book.cumulative_pnl += float(pnl.sum() - cost.sum())
        book.cumulative_cost += float(cost.sum())
        book.date = date_
        return records


def pivot(panel_df: pl.DataFrame, column: str, dates: list[dt.date], symbols: list[str], fill=np.nan) -> np.ndarray:
    """`(len(dates), len(symbols))` array of `column`, `fill` where there is no row."""
    grid = (
        pl.DataFrame({"date": dates}, schema={"date": pl.Date})
        .join(pl.DataFrame({"symbol": symbols}, schema={"symbol": pl.Utf8}), how="cross")
        .join(panel_df.select("date", "symbol", column), on=["date", "symbol"], how="left")
        .sort("date", "symbol")
    )
    if panel_df.schema[column] == pl.Utf8:
        return grid[column].fill_null(fill).to_numpy().astype(object).reshape(len(dates), len(symbols))
    return grid[column].cast(pl.Float64).fill_null(fill).to_numpy().reshape(len(dates), len(symbols))


def nan0(values: np.ndarray | float) -> np.ndarray | float:
    return np.nan_to_num(values, nan=0.0)


def none_if_nan(value: float) -> float | None:
    return None if np.isnan(value) else float(value)
