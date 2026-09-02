"""The types every layer of the backtest passes around.

Kept in one module so `structures.py`, `pnl.py` and `engine.py` share a
vocabulary without importing each other.
"""

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class Leg:
    """One contract in a structure, before sizing.

    `ratio` is signed and relative: +1/+1 is a long straddle, +1/-1 a risk
    reversal. The sizing layer multiplies it by a quantity, so a structure
    never needs to know how big the position will be.
    """

    expiration: date
    strike: float
    right: str  # "C" or "P"
    ratio: float


@dataclass(frozen=True)
class BacktestConfig:
    """Everything that defines one run.

    Frozen and hashable so a grid can be keyed on it, and so a result can be
    traced back to exactly the configuration that produced it.
    """

    signal: object  # Signal: (candidates_df) -> adds a "score" column
    structure: object  # Structure: (day_df) -> list[Leg] | None
    eligibility: tuple = ()  # Filters, applied at formation only
    n_quantiles: int = 10
    long_quantile: int = 0  # lowest score
    short_quantile: int = -1  # highest score; -1 means n_quantiles - 1
    holding_days: int = 21
    gross_vega_per_side: float = 10_000.0
    hedge_delta: bool = True
    min_names_per_side: int = 5
    start: date | None = None
    end: date | None = None
    label: str = "baseline"

    def short_index(self) -> int:
        return self.n_quantiles - 1 if self.short_quantile == -1 else self.short_quantile


@dataclass
class BacktestResult:
    """What a run produces.

    `daily_pnl` is the headline series in dollars, split into the option and
    hedge legs — for a delta-hedged straddle that split is the difference
    between "the vol premium paid" and "the hedge worked", and a decile spread
    that comes entirely from the hedge is not a VRP result.
    """

    config: BacktestConfig
    daily_pnl: object  # pl.DataFrame: date, long_pnl, short_pnl, hedge_pnl, total_pnl
    decile_pnl: object  # pl.DataFrame: date, quantile, pnl - the monotonicity check
    positions: object  # pl.DataFrame: one row per (cohort, symbol, mark date)
    diagnostics: dict = field(default_factory=dict)
