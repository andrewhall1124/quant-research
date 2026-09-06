"""Delta-hedged ATM straddle.

Selection: the listed *monthly* expiration nearest `target_dte` calendar days
(default 60), the strike nearest the underlying with both a call and a put
quoted. Marking: the sum of the two mids. Hedge: `-delta * multiplier`
shares per contract, reset daily at the close. Roll: when `dte <= roll_dte`
(default 20).

A monthly expiration is taken to be one dated the 15th-21st of the month on
a Thursday or Friday (the Thursday case covers Good Friday). Weeklies are
excluded from selection so a unit is never rolled into a thin series.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import polars as pl

from voltium.instruments.base import Instrument, LegSpec, UnitMark, UnitSpec
from voltium.loaders import CONTRACT_MULTIPLIER


@dataclass(frozen=True)
class StraddleConfig:
    target_dte: int = 60
    roll_dte: int = 20
    min_dte: int = 25  # never *enter* a series closer than this
    max_dte: int = 120
    monthly_only: bool = True
    max_moneyness: float = 0.10  # |K/S - 1| bound on the entry strike


def is_monthly_expiration(expiration: dt.date) -> bool:
    return 15 <= expiration.day <= 21 and expiration.weekday() in (3, 4)


class DeltaHedgedStraddle(Instrument):
    def __init__(self, config: StraddleConfig = StraddleConfig()) -> None:
        self.config = config

    def select(self, quotes_df: pl.DataFrame, date_: dt.date) -> UnitSpec | None:
        cfg = self.config
        if quotes_df.is_empty():
            return None
        symbol = quotes_df["symbol"][0]
        frame = (
            quotes_df.filter(pl.col("bid") > 0, pl.col("ask") > pl.col("bid"))
            .with_columns((pl.col("expiration") - pl.lit(date_)).dt.total_days().alias("dte"))
            .filter(pl.col("dte").is_between(cfg.min_dte, cfg.max_dte))
        )
        if cfg.monthly_only:
            frame = frame.filter(
                pl.col("expiration").dt.day().is_between(15, 21),
                pl.col("expiration").dt.weekday().is_in([4, 5]),  # polars: Mon=1 .. Sun=7
            )
        if frame.is_empty():
            return None
        # strikes with both rights quoted, per expiration
        pairs = (
            frame.group_by("expiration", "strike", "dte")
            .agg(pl.col("right").n_unique().alias("rights"), pl.col("underlying").first())
            .filter(pl.col("rights") == 2)
            .with_columns((pl.col("strike") / pl.col("underlying") - 1.0).abs().alias("moneyness"))
            .filter(pl.col("moneyness") <= cfg.max_moneyness)
        )
        if pairs.is_empty():
            return None
        nearest_dte = pairs.with_columns((pl.col("dte") - cfg.target_dte).abs().alias("dte_gap"))
        best_gap = nearest_dte["dte_gap"].min()
        chosen = (
            nearest_dte.filter(pl.col("dte_gap") == best_gap)
            .sort("moneyness", "strike")
            .head(1)
        )
        expiration = chosen["expiration"][0]
        strike = float(chosen["strike"][0])
        legs = (LegSpec(expiration, strike, "C"), LegSpec(expiration, strike, "P"))
        return UnitSpec(symbol=symbol, legs=legs, ratios=(1, 1))

    def mark(self, quotes_df: pl.DataFrame, unit: UnitSpec, date_: dt.date) -> UnitMark | None:
        rows = quotes_df.filter(
            pl.col("expiration") == unit.expiration,
            pl.col("strike") == unit.legs[0].strike,
            pl.col("right").is_in([leg.right for leg in unit.legs]),
        )
        if rows.is_empty():
            return None
        usable = rows.filter(pl.col("ask") > 0, pl.col("bid") >= 0, pl.col("ask") >= pl.col("bid"))
        if usable.height < len(unit.legs):
            return None
        total = usable.select(
            pl.col("mid").sum(),
            pl.col("bid").sum(),
            pl.col("ask").sum(),
            pl.col("delta").sum(),
            pl.col("vega").sum(),
            pl.col("theta").sum(),
            pl.col("underlying").first(),
        ).row(0)
        mid, bid, ask, delta, vega, theta, underlying = total
        return UnitMark(
            date=date_,
            mid=float(mid),
            bid=float(bid),
            ask=float(ask),
            delta=float(delta),
            vega=float(vega),
            theta=float(theta),
            underlying=float(underlying),
            dte=(unit.expiration - date_).days,
            complete=True,
        )

    def should_roll(self, unit: UnitSpec, date_: dt.date) -> bool:
        return (unit.expiration - date_).days <= self.config.roll_dte

    def hedge_shares(self, mark: UnitMark, contracts: float) -> float:
        return -mark.delta * CONTRACT_MULTIPLIER * contracts

    @staticmethod
    def dollar_vega(mark: UnitMark, contracts: float) -> float:
        """Dollar vega of `contracts` straddles: P&L per vol point."""
        return mark.vega * CONTRACT_MULTIPLIER * contracts
