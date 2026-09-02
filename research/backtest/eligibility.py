"""Formation-time filters on the tradeable universe.

Every filter is a small object with a `mask` returning a boolean expression,
so a configuration is a tuple of them and the grid over OI thresholds is a
loop over tuples. They bind **at formation only**: a position that qualified
on the day it was opened is held for its full life even if the name later
fails the screen, because a screen changing is not a reason you could have
unwound, and re-applying it daily would be using information the trade did
not have.

The metrics they read are columns on the panel, never filters applied when the
panel was built — that is what makes the sweep cheap.
"""

import polars as pl


class MinOpenInterest:
    """Contracts with at least `threshold` contracts of standing position.

    The screen this study is actually about. Open interest is stamped pre-open
    and reports the position standing after the previous close, so it is known
    at formation. Names whose OI is missing are dropped rather than passed:
    a missing liquidity reading is not evidence of liquidity.
    """

    def __init__(self, threshold: int):
        self.threshold = threshold
        self.name = f"oi>={threshold}"

    def mask(self) -> pl.Expr:
        return pl.col("min_open_interest").fill_null(-1) >= self.threshold


class MinVolume:
    """Contracts that traded at least `threshold` times on the formation day."""

    def __init__(self, threshold: int):
        self.threshold = threshold
        self.name = f"vol>={threshold}"

    def mask(self) -> pl.Expr:
        return pl.col("min_volume").fill_null(-1) >= self.threshold


class MaxRelativeSpread:
    """Drop structures whose widest leg is quoted wider than `threshold` of mid.

    Single-name option spreads are wide enough that this is the filter most
    likely to decide whether the strategy is tradeable at all, independent of
    whether transaction costs are charged.
    """

    def __init__(self, threshold: float):
        self.threshold = threshold
        self.name = f"spread<={threshold:g}"

    def mask(self) -> pl.Expr:
        return pl.col("max_rel_spread").fill_null(1e9) <= self.threshold


class MinUnderlyingPrice:
    """Keep names above a price floor, where a strike grid is fine enough for ATM."""

    def __init__(self, threshold: float):
        self.threshold = threshold
        self.name = f"px>={threshold:g}"

    def mask(self) -> pl.Expr:
        return pl.col("underlying_price") >= self.threshold


class MinPremium:
    """Drop structures whose total premium is too small to be worth a round trip."""

    def __init__(self, threshold: float):
        self.threshold = threshold
        self.name = f"prem>={threshold:g}"

    def mask(self) -> pl.Expr:
        return pl.col("premium").abs() >= self.threshold


def apply_filters(positions_df: pl.DataFrame, filters: tuple) -> pl.DataFrame:
    if not filters:
        return positions_df
    return positions_df.filter(pl.all_horizontal([f.mask() for f in filters]))


def describe(filters: tuple) -> str:
    return " & ".join(f.name for f in filters) if filters else "none"
