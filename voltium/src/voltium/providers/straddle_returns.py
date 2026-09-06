"""Per-unit-vega P&L of one delta-hedged reference straddle per symbol.

The risk model's "returns" are the daily P&L of holding one long unit of the
`Instrument` in each name, delta-hedged and rolled exactly as the book would
be, divided by the unit's dollar vega **at inception** (the close it was
opened or rolled into). They are built by running the `Backtester` itself,
one symbol at a time, with `ReferenceUnitStrategy` — so the number is by
construction the same thing the book earns per dollar of vega bought, gross
of costs, and not a change in IV.

Why inception vega and not the previous close's: a unit that has drifted
from the money has a small current vega, and dividing a gamma-driven P&L
(or a roll cost) by it produces per-vega numbers in the hundreds that are
an artefact of the denominator, not risk. Inception vega is what the book
actually sized on.

Output panel, one row per (date, symbol):

    pnl_per_vega        (option_pnl + hedge_pnl) / entry_vega
    cost_per_vega       (option_cost + hedge_cost) / entry_vega
    exit_cost_per_vega  half-spread to close the unit today / entry_vega
    dollar_vega         of one unit at today's close (current, not entry)
    entry_vega          dollar vega of the unit held into today, at its inception
    event               "", "open", "roll", "forced_close", ...

On a roll day the P&L and the cost (closing the old unit, opening the new)
are scaled by the *old* unit's inception vega. The first session of a unit
has no prior unit, so `pnl_per_vega` is null there. Reruns are cheap to
avoid: pass `cache_dir` and each symbol's path is written to parquet and
reused.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
from pathlib import Path

import polars as pl

from voltium.backtester import BacktestConfig, Backtester
from voltium.costs import HalfSpreadCost, PerShareCost
from voltium.instruments.base import Instrument
from voltium.loaders import DataPaths, scan_options
from voltium.providers.base import PanelProvider
from voltium.providers.calendar import TradingCalendar
from voltium.providers.chain import ChainBand, ChainProvider
from voltium.strategy import ReferenceUnitStrategy
from voltium.trade_generator import TradeGenerator, TradeGeneratorConfig

log = logging.getLogger(__name__)

REFERENCE_SCHEMA = {
    "date": pl.Date,
    "symbol": pl.Utf8,
    "pnl_per_vega": pl.Float64,
    "cost_per_vega": pl.Float64,
    "exit_cost_per_vega": pl.Float64,
    "dollar_vega": pl.Float64,
    "entry_vega": pl.Float64,
    "dollar_theta": pl.Float64,
    "mid": pl.Float64,
    "underlying": pl.Float64,
    "dte": pl.Int64,
    "event": pl.Utf8,
}


class PartitionedChainProvider(ChainProvider):
    """One symbol's chain, partitioned by date once so each day is a dict hit."""

    def __init__(self, chain_df: pl.DataFrame) -> None:
        self.by_date = {k[0]: v for k, v in chain_df.partition_by("date", as_dict=True).items()}
        self.schema = chain_df.schema

    def get(self, date_: dt.date) -> pl.DataFrame:
        frame = self.by_date.get(date_)
        return frame if frame is not None else pl.DataFrame(schema=self.schema)


def compute_reference_path(
    chain_df: pl.DataFrame,
    instrument: Instrument,
    calendar: TradingCalendar,
    start: dt.date,
    end: dt.date,
    option_cost: HalfSpreadCost = HalfSpreadCost(),
    hedge_cost: PerShareCost = PerShareCost(),
) -> pl.DataFrame:
    """Run the backtester on one symbol's chain holding one unit; per-vega P&L."""
    backtester = Backtester(
        calendar=calendar,
        chain=PartitionedChainProvider(chain_df),
        instrument=instrument,
        strategy=ReferenceUnitStrategy(),
        trade_generator=TradeGenerator(
            instrument,
            TradeGeneratorConfig(max_spread=10.0, min_oi=0, band=0.0),
            constraints=[],
        ),
        config=BacktestConfig(start=start, end=end, rebalance="daily"),
        option_cost=option_cost,
        hedge_cost=hedge_cost,
    )
    records_df, _ = backtester.run(progress=False)
    if records_df.is_empty():
        return pl.DataFrame(schema=REFERENCE_SCHEMA)
    inception = pl.col("event").is_in(["open", "roll"])
    return (
        records_df.sort("date")
        .with_columns(
            pl.when(inception).then(pl.col("dollar_vega")).otherwise(None).forward_fill().alias("inception_vega")
        )
        # the unit held *into* today is the one whose inception vega applied yesterday
        .with_columns(pl.col("inception_vega").shift(1).alias("entry_vega"))
        .with_columns(
            pl.when(pl.col("entry_vega") > 0)
            .then((pl.col("option_pnl") + pl.col("hedge_pnl")) / pl.col("entry_vega"))
            .otherwise(None)
            .alias("pnl_per_vega"),
            pl.when(pl.col("entry_vega") > 0)
            .then((pl.col("option_cost") + pl.col("hedge_cost")) / pl.col("entry_vega"))
            .otherwise(None)
            .alias("cost_per_vega"),
            pl.when(pl.col("inception_vega") > 0)
            .then(pl.col("exit_cost") / pl.col("inception_vega"))
            .otherwise(None)
            .alias("exit_cost_per_vega"),
        )
        .select(list(REFERENCE_SCHEMA))
    )


class StraddleReturnsProvider(PanelProvider):
    """`get(date) -> (date, symbol, pnl_per_vega, cost_per_vega, dollar_vega, ...)`."""

    @classmethod
    def from_store(
        cls,
        paths: DataPaths,
        symbols: list[str],
        instrument: Instrument,
        calendar: TradingCalendar,
        start: dt.date,
        end: dt.date,
        band: ChainBand = ChainBand(),
        index: bool = False,
        cache_dir: Path | None = None,
        refresh: bool = False,
    ) -> "StraddleReturnsProvider":
        pieces = []
        # the cache is keyed on the instrument's config too, so a changed roll
        # rule never reuses paths built under the old one
        tag = hashlib.md5(repr(getattr(instrument, "config", instrument.__class__.__name__)).encode()).hexdigest()[:8]
        for count, symbol in enumerate(symbols, 1):
            cache_path = cache_dir / f"{symbol}_{start}_{end}_{tag}.parquet" if cache_dir else None
            if cache_path is not None and cache_path.exists() and not refresh:
                pieces.append(pl.read_parquet(cache_path))
                continue
            try:
                chain_df = (
                    scan_options(paths, [symbol], start, end, index=index, with_oi=False)
                    .filter(
                        (pl.col("expiration") - pl.col("date")).dt.total_days() <= band.max_dte,
                        (pl.col("strike") / pl.col("underlying") - 1.0).abs() <= band.max_moneyness,
                    )
                    .collect()
                )
            except FileNotFoundError:
                continue
            path_df = compute_reference_path(chain_df, instrument, calendar, start, end)
            if cache_path is not None:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                path_df.write_parquet(cache_path)
            pieces.append(path_df)
            if count % 50 == 0 or count == len(symbols):
                log.info("reference paths: %d/%d symbols", count, len(symbols))
        panel_df = pl.concat(pieces) if pieces else pl.DataFrame(schema=REFERENCE_SCHEMA)
        return cls(panel_df.sort("date", "symbol"))
