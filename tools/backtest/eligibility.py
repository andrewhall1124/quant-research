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


class MinStructureVega:
    """Require the structure to carry at least `threshold` dollars of vega.

    Sizing solves `quantity = target_vega / structure_vega`, so a structure
    whose vega is near zero demands an absurd number of contracts to fill a
    vega budget — in the 2025 sample the thinnest straddles carry about $1.20
    of vega and would need 8,000 contracts to reach a $10k target. Those are
    not positions, they are a lever that multiplies whatever noise is in the
    quote. This is the filter that keeps the sizing sane, and it belongs here
    rather than as a hard-coded guard so its threshold can be swept like any
    other.
    """

    def __init__(self, threshold: float):
        self.threshold = threshold
        self.name = f"vega>={threshold:g}"

    def mask(self) -> pl.Expr:
        return pl.col("vega").abs() >= self.threshold


class MinPremium:
    """Drop structures whose total premium is too small to be worth a round trip."""

    def __init__(self, threshold: float):
        self.threshold = threshold
        self.name = f"prem>={threshold:g}"

    def mask(self) -> pl.Expr:
        return pl.col("premium").abs() >= self.threshold


class ExcludeEarningsBeforeExpiry:
    """Keep only contracts that expire before the next announcement.

    The sharpest version of earnings conditioning for an option: implied vol
    prices the variance it expects to cover, so an earnings date inside the
    contract's life is a discrete addition to that variance, not a matter of
    degree. Excluding those names asks whether the strategy has anything left
    once it can no longer trade the earnings cycle.
    """

    def __init__(self):
        self.name = "no_earnings_in_life"

    def mask(self) -> pl.Expr:
        return ~pl.col("earnings_before_expiry").fill_null(False)


class RequireEarningsBeforeExpiry:
    """The complement: only contracts whose life contains an announcement.

    Run alongside `ExcludeEarningsBeforeExpiry` it partitions the universe, so
    the two together say how the P&L splits between the earnings trade and
    everything else.
    """

    def __init__(self):
        self.name = "earnings_in_life"

    def mask(self) -> pl.Expr:
        return pl.col("earnings_before_expiry").fill_null(False)


class ExcludeEarningsWithin:
    """Drop names announcing within `days` of formation, either side.

    A blunter screen than the expiry test, and the one that matters if the
    concern is the move itself rather than the variance the contract prices.
    """

    def __init__(self, days: int):
        self.days = days
        self.name = f"earnings_gap>{days}d"

    def mask(self) -> pl.Expr:
        return (pl.col("days_to_earnings").fill_null(9999) > self.days) & (
            pl.col("days_since_earnings").fill_null(9999) > self.days
        )


def apply_filters(positions_df: pl.DataFrame, filters: tuple) -> pl.DataFrame:
    if not filters:
        return positions_df
    return positions_df.filter(pl.all_horizontal([f.mask() for f in filters]))


def describe(filters: tuple) -> str:
    return " & ".join(f.name for f in filters) if filters else "none"
