"""Strategy: what the book should look like on a rebalance date.

atium's `Strategy.generate_weights(date) -> (date, ticker, weight)` becomes
`generate_targets(date, chain_df, portfolio) -> (symbol, target_vega)`: the
unit is dollar vega, and the strategy sees the chain and the current book
because a vol target only makes sense for names that have a tradeable
straddle today.

Three kinds live here:

* `FixedTargetStrategy` — a hard-coded book, used to validate the backtester.
* `ReferenceUnitStrategy` — one long contract per name, which builds the
  per-unit-vega reference series.
* `ScoreStrategy` — the ABC every signal-driven book shares: pick today's
  eligible names (chain ∩ universe, plus anything held so it can be unwound),
  fetch a score per name, and hand both to `size`. `RankWeightedStrategy`
  sizes by centred rank with no risk model; `optimizer/strategy.py`'s
  `OptimizationStrategy` sizes with CVXPY and lives there so this module
  does not import it.
"""

from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod
from typing import Literal, Protocol

import polars as pl

from voltium.providers.base import Provider
from voltium.providers.universe import UniverseProvider

TARGET_SCHEMA = {"symbol": pl.Utf8, "target_vega": pl.Float64}
PREVIOUS_SCHEMA = {"symbol": pl.Utf8, "dollar_vega": pl.Float64}


class Book(Protocol):
    """What a strategy needs to know about the current book.

    `portfolio.Portfolio` (the chain backtester's state) and
    `panel_backtester.VegaBook` both satisfy it.
    """

    def symbols(self) -> list[str]: ...

    def dollar_vega(self, symbol: str) -> float: ...


class Strategy(ABC):
    @abstractmethod
    def generate_targets(self, date_: dt.date, chain_df: pl.DataFrame, portfolio: Book) -> pl.DataFrame:
        """`(symbol, target_vega)` in dollars per vol point; long > 0."""


class FixedTargetStrategy(Strategy):
    def __init__(self, targets: dict[str, float]) -> None:
        self.targets = targets

    def generate_targets(self, date_: dt.date, chain_df: pl.DataFrame, portfolio: Book) -> pl.DataFrame:
        return pl.DataFrame(
            {"symbol": list(self.targets), "target_vega": list(self.targets.values())},
            schema=TARGET_SCHEMA,
        )


class ReferenceUnitStrategy(Strategy):
    """Hold exactly one long unit in every symbol that has a chain today.

    Used to build the per-unit-vega reference P&L series the risk model and
    the decile table are estimated on.
    """

    def generate_targets(self, date_: dt.date, chain_df: pl.DataFrame, portfolio: Book) -> pl.DataFrame:
        symbols = chain_df["symbol"].unique().sort().to_list()
        return pl.DataFrame(
            {"symbol": symbols, "target_vega": [0.0] * len(symbols), "target_contracts": [1] * len(symbols)},
            schema=TARGET_SCHEMA | {"target_contracts": pl.Int64},
        )


class ScoreStrategy(Strategy):
    """Score today's eligible names, then size them.

    `scores` is any provider whose `get(date)` has `symbol` and `score_column`
    — a `SignalProvider` (`signal`) for a rank book, an `AlphaProvider`
    (`alpha`) for the optimizer. Eligibility is the chain today, intersected
    with the point-in-time universe when one is given, plus every held name
    so a name that has left can be unwound by the sizer rather than dumped
    by the trade generator. Names held but absent from `size`'s output are
    targets of zero.
    """

    def __init__(self, scores: Provider, score_column: str, universe: UniverseProvider | None = None) -> None:
        self.scores = scores
        self.score_column = score_column
        self.universe = universe

    def generate_targets(self, date_: dt.date, chain_df: pl.DataFrame, portfolio: Book) -> pl.DataFrame:
        eligible = set(chain_df["symbol"].unique().to_list())
        if self.universe is not None:
            eligible &= set(self.universe.get(date_)["symbol"].to_list())
        held = set(portfolio.symbols())
        scores_df = self.scores.get(date_).filter(pl.col("symbol").is_in(sorted(eligible | held)))
        previous_df = pl.DataFrame(
            {"symbol": portfolio.symbols(), "dollar_vega": [portfolio.dollar_vega(s) for s in portfolio.symbols()]},
            schema=PREVIOUS_SCHEMA,
        )
        targets_df = self.size(date_, scores_df, previous_df).select(list(TARGET_SCHEMA))
        missing = sorted(held - set(targets_df["symbol"].to_list()))
        if missing:
            targets_df = pl.concat([targets_df, pl.DataFrame({"symbol": missing, "target_vega": [0.0] * len(missing)}, schema=TARGET_SCHEMA)])
        return targets_df

    @abstractmethod
    def size(self, date_: dt.date, scores_df: pl.DataFrame, previous_df: pl.DataFrame) -> pl.DataFrame:
        """`(symbol, target_vega)` from today's `(symbol, <score_column>)` and the current book."""


class RankWeightedStrategy(ScoreStrategy):
    """Dollar vega proportional to centred rank of the score; no risk model.

    With `scheme="linear"`, weight_i ∝ rank_i − mean rank, so the book is net
    vega neutral by construction and every scored name is held. With
    `scheme="quantile"`, only the top and bottom `quantile` of names are held,
    equal vega each. Either way `sum |w| = gross_vega`. `short_rich=True`
    shorts the high scores (a richness signal); `False` goes long them.
    Names with a null score are dropped, which for a held name means a
    target of zero.
    """

    def __init__(
        self,
        signal: Provider,
        gross_vega: float,
        universe: UniverseProvider | None = None,
        scheme: Literal["linear", "quantile"] = "linear",
        quantile: float = 0.1,
        short_rich: bool = True,
        score_column: str = "signal",
        min_names: int = 10,
    ) -> None:
        super().__init__(signal, score_column, universe)
        self.gross_vega = gross_vega
        self.scheme = scheme
        self.quantile = quantile
        self.short_rich = short_rich
        self.min_names = min_names

    def size(self, date_: dt.date, scores_df: pl.DataFrame, previous_df: pl.DataFrame) -> pl.DataFrame:
        scored_df = scores_df.filter(pl.col(self.score_column).is_not_null()).sort(self.score_column, "symbol")
        n = scored_df.height
        if n < self.min_names:
            return pl.DataFrame(schema=TARGET_SCHEMA)
        rank = pl.col(self.score_column).rank(method="average").cast(pl.Float64)
        if self.scheme == "linear":
            raw = rank - (n + 1) / 2.0
        elif self.scheme == "quantile":
            k = max(1, int(round(n * self.quantile)))
            raw = pl.when(rank <= k).then(-1.0).when(rank > n - k).then(1.0).otherwise(0.0)
        else:
            raise ValueError(f"unknown scheme {self.scheme!r}")
        sign = -1.0 if self.short_rich else 1.0
        weights_df = scored_df.with_columns((sign * raw).alias("raw"))
        gross = weights_df["raw"].abs().sum()
        if gross == 0:
            return pl.DataFrame(schema=TARGET_SCHEMA)
        return weights_df.select("symbol", (pl.col("raw") * self.gross_vega / gross).alias("target_vega"))
