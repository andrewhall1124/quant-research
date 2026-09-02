"""Point-in-time structure selection.

A `Structure` turns one day's chain for one name into a list of `Leg`s. It
only ever sees the rows for that (symbol, date), so it cannot look ahead by
construction, and it owns its own expiration and strike search — an
`AtmStraddle(target_dte=30)` and a `Strangle(target_dte=45, delta=0.25)` carry
different tenors without the engine knowing anything about either.

Selection is vectorised over the whole panel rather than looped per name-day:
the rules here are all "rank within a group and take the best", which polars
does in one pass over 13M rows instead of 100k Python calls.

A structure returns nothing for a name-day it cannot serve — no expiration
close enough to the target, no strike close enough to the money, a leg with no
quote. Those name-days simply drop out of the universe that day, which is the
honest treatment: you could not have put the trade on.
"""

import polars as pl

CALL = "CALL"
PUT = "PUT"


class AtmStraddle:
    """Long the call and the put at the strike nearest the money.

    `target_dte` picks the expiration, `max_dte_error` says how far off the
    listed calendar is allowed to be before the name-day is skipped, and
    `max_moneyness` does the same for the strike. On single names those
    tolerances bind often: half the universe has no weeklies, so a 30-day
    target can be a week away from anything listed.
    """

    def __init__(
        self,
        target_dte: int = 30,
        max_dte_error: int = 7,
        max_moneyness: float = 0.05,
        require_quotes: bool = True,
        max_iv_error: float | None = 1.0,
    ):
        self.target_dte = target_dte
        self.max_dte_error = max_dte_error
        self.max_moneyness = max_moneyness
        self.require_quotes = require_quotes
        self.max_iv_error = max_iv_error
        self.name = f"atm_straddle_{target_dte}d"

    def band(self) -> tuple[int, int]:
        """The dte range the selection panel must cover for this structure."""
        return (self.target_dte - self.max_dte_error, self.target_dte + self.max_dte_error)

    def select(self, panel_df: pl.DataFrame) -> pl.DataFrame:
        """One row per selected leg: (date, symbol, expiration, strike, right, ratio).

        Both legs of a straddle are long, so `ratio` is +1 on each; the sizing
        layer scales them together.
        """
        candidates = panel_df.filter(
            (pl.col("dte") - self.target_dte).abs() <= self.max_dte_error,
            pl.col("moneyness").abs() <= self.max_moneyness,
        )
        if self.require_quotes:
            # `ask >= bid` drops crossed quotes. `research/data_quality/` put
            # them at 0.008% of stored contract-days, but they survive into the
            # near-money 30-day band this selects from and produce a negative
            # spread, so they are excluded explicitly rather than assumed away.
            candidates = candidates.filter(
                pl.col("bid") > 0, pl.col("ask") >= pl.col("bid"), pl.col("vega") > 0
            )
        if self.max_iv_error is not None:
            # ~3% of contract-days fail to invert and come back pinned at 0.5
            # with an error near +/-100. Their IV is not a number.
            candidates = candidates.filter(pl.col("iv_error").abs() <= self.max_iv_error)

        # Pick the expiration nearest the target, then the strike nearest the
        # money within it, then require both legs to have survived the filters.
        chosen = (
            candidates.with_columns(
                (pl.col("dte") - self.target_dte).abs().alias("dte_error")
            )
            .with_columns(
                pl.col("dte_error").min().over("date", "symbol").alias("best_dte_error")
            )
            .filter(pl.col("dte_error") == pl.col("best_dte_error"))
            .with_columns(
                pl.col("moneyness").abs().min().over("date", "symbol", "expiration").alias("best_m")
            )
            .filter(pl.col("moneyness").abs() == pl.col("best_m"))
            .filter(pl.col("right").n_unique().over("date", "symbol", "expiration", "strike") == 2)
        )

        # A tie on |moneyness| between two strikes (spot exactly between them)
        # would leave four legs; keep the lower strike deterministically.
        chosen = (
            chosen.sort("date", "symbol", "strike")
            .with_columns(pl.col("strike").first().over("date", "symbol").alias("keep_strike"))
            .filter(pl.col("strike") == pl.col("keep_strike"))
            .with_columns(pl.lit(1.0).alias("ratio"))
            .drop("dte_error", "best_dte_error", "best_m", "keep_strike")
        )
        return chosen


class Strangle:
    """Long a call and a put at symmetric target deltas.

    Included to prove the seam: the engine, signal, sizing and P&L layers all
    work on this without modification. Needs a wider `max_moneyness` in the
    selection panel than a straddle does.
    """

    def __init__(
        self,
        target_dte: int = 30,
        max_dte_error: int = 7,
        target_delta: float = 0.25,
        require_quotes: bool = True,
        max_iv_error: float | None = 1.0,
    ):
        self.target_dte = target_dte
        self.max_dte_error = max_dte_error
        self.target_delta = target_delta
        self.require_quotes = require_quotes
        self.max_iv_error = max_iv_error
        self.name = f"strangle_{target_dte}d_{int(target_delta * 100)}d"

    def band(self) -> tuple[int, int]:
        return (self.target_dte - self.max_dte_error, self.target_dte + self.max_dte_error)

    def select(self, panel_df: pl.DataFrame) -> pl.DataFrame:
        candidates = panel_df.filter(
            (pl.col("dte") - self.target_dte).abs() <= self.max_dte_error
        )
        if self.require_quotes:
            candidates = candidates.filter(
                pl.col("bid") > 0, pl.col("ask") >= pl.col("bid"), pl.col("vega") > 0
            )
        if self.max_iv_error is not None:
            candidates = candidates.filter(pl.col("iv_error").abs() <= self.max_iv_error)

        chosen = (
            candidates.with_columns(
                (pl.col("dte") - self.target_dte).abs().alias("dte_error")
            )
            .with_columns(
                pl.col("dte_error").min().over("date", "symbol").alias("best_dte_error")
            )
            .filter(pl.col("dte_error") == pl.col("best_dte_error"))
            # Calls carry positive delta, puts negative; target the absolute.
            .with_columns((pl.col("delta").abs() - self.target_delta).abs().alias("delta_error"))
            .with_columns(
                pl.col("delta_error").min().over("date", "symbol", "expiration", "right").alias("best_delta_error")
            )
            .filter(pl.col("delta_error") == pl.col("best_delta_error"))
            .filter(pl.col("right").n_unique().over("date", "symbol", "expiration") == 2)
        )
        return (
            chosen.sort("date", "symbol", "right", "strike")
            .unique(subset=["date", "symbol", "right"], keep="first")
            .with_columns(pl.lit(1.0).alias("ratio"))
            .drop("dte_error", "best_dte_error", "delta_error", "best_delta_error")
        )


def summarize_positions(legs_df: pl.DataFrame) -> pl.DataFrame:
    """Collapse legs to one row per (date, symbol) for ranking and filtering.

    The signal sorts on `atm_iv` — the IV of the contracts actually traded,
    not a separately interpolated measure — so the premium being measured is
    the premium in the contract being bought. It is vega-weighted across legs,
    which for a straddle is close to a plain average and for a skewed
    structure correctly leans on the leg carrying the vol exposure.

    Eligibility binds on the *worst* leg: a structure is only as tradeable as
    its tightest constraint allows.
    """
    return (
        legs_df.group_by("date", "symbol")
        .agg(
            pl.col("expiration").first(),
            pl.col("dte").first(),
            pl.col("underlying_price").first(),
            pl.len().alias("n_legs"),
            (
                (pl.col("implied_vol") * pl.col("vega") * pl.col("ratio").abs()).sum()
                / (pl.col("vega") * pl.col("ratio").abs()).sum()
            ).alias("atm_iv"),
            (pl.col("vega") * pl.col("ratio")).sum().alias("vega"),
            (pl.col("delta") * pl.col("ratio")).sum().alias("delta"),
            (pl.col("gamma") * pl.col("ratio")).sum().alias("gamma"),
            (pl.col("theta") * pl.col("ratio")).sum().alias("theta"),
            (pl.col("mid") * pl.col("ratio")).sum().alias("premium"),
            pl.col("open_interest").min().alias("min_open_interest"),
            pl.col("volume").min().alias("min_volume"),
            pl.col("rel_spread").max().alias("max_rel_spread"),
        )
        .sort("date", "symbol")
    )
