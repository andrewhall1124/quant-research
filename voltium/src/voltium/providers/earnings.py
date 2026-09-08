"""Earnings events, the jump variance they carry, and the surface without it.

An announcement between today and an expiration adds a lump of variance to
that expiration's ATM implied vol. `iv60` therefore rises and falls with
the calendar — it contains the jump when the next print is inside 60 days
and not otherwise — while a 60-session realized-vol forecast almost always
spans a print. That makes the raw variance premium mechanically higher for
names whose print is near. This module strips it.

**Events.** `load_earnings_events(paths, calendar)` maps each announcement
to the session whose close-to-close return carries the move: the same
session for a before-open report, the next session for an after-close one
(an unknown session is treated as before-open).

**Jump variance.** `estimate_jump_variance(pillars_df, events_df)` uses the
term structure on each (date, symbol): with `w = iv² × dte / 365` the total
variance of a pillar, the nearest pillar expiring *before* the event gives
the diffusive variance rate `v = w_B / t_B`, and the nearest pillar
expiring *after* it gives `J = w_A − v × t_A`, the variance the event adds
(clipped at zero). `sqrt(J)` is the implied move. Where no pillar expires
before the event (about a quarter of days within 60 days of a print, more
for thinly listed names) the name's trailing median of its own term
estimates over the past 250 sessions stands in (`method = "history"`);
with neither, nothing is subtracted (`method = "none"`).

**Adjuster.** `TermStructureEarningsAdjuster` implements the
`EarningsAdjuster` hook: every pillar spanning `k` events has
`k × J × 365 / dte` taken out of `iv²`, floored at `floor_fraction` of the
original variance so a bad estimate cannot zero a pillar. The rest of the
surface build is unchanged, so `iv60` from the adjusted pillars is the
diffusive constant-maturity vol.

**Realized side.** `compute_close_to_close_panel(..., exclude_df=events)`
in `realized_vol.py` drops the event sessions' squared returns from every
window, so the forecast target and features are diffusive too.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import polars as pl

from voltium.loaders import DataPaths, scan_earnings
from voltium.providers.calendar import TradingCalendar
from voltium.providers.surface import EarningsAdjuster

DAYS_PER_YEAR = 365.0

EVENT_SCHEMA = {"symbol": pl.Utf8, "event_date": pl.Date, "session": pl.Utf8, "event_session": pl.Date}
JUMP_SCHEMA = {
    "date": pl.Date,
    "symbol": pl.Utf8,
    "next_event": pl.Date,
    "days_to_event": pl.Int64,
    "jump_var": pl.Float64,
    "method": pl.Utf8,
}


def load_earnings_events(paths: DataPaths, calendar: TradingCalendar) -> pl.DataFrame:
    """`(symbol, event_date, session, event_session)` for every announcement in the store."""
    events_df = scan_earnings(paths).collect().rename({"date": "event_date"})
    sessions = calendar.sessions
    session_frame = pl.DataFrame({"event_date": sessions}, schema={"event_date": pl.Date}).with_columns(
        pl.col("event_date").alias("same_session"),
        pl.col("event_date").shift(-1).alias("next_session"),
    )
    # an announcement on a non-session (rare) rolls to the next session either way
    events_df = (
        events_df.sort("event_date")
        .join_asof(session_frame.sort("event_date"), on="event_date", strategy="forward")
        .with_columns(
            pl.when(pl.col("session") == "amc").then(pl.col("next_session")).otherwise(pl.col("same_session")).alias("event_session")
        )
        .drop_nulls("event_session")
        .select(list(EVENT_SCHEMA))
        .sort("symbol", "event_session")
    )
    return events_df


def attach_next_event(dates_df: pl.DataFrame, events_df: pl.DataFrame) -> pl.DataFrame:
    """`dates_df` `(date, symbol)` plus `next_event` (the first event session ≥ date) and `days_to_event`."""
    events = events_df.select("symbol", pl.col("event_session").alias("next_event")).sort("symbol", "next_event")
    return (
        dates_df.sort("symbol", "date")
        .join_asof(events, left_on="date", right_on="next_event", by="symbol", strategy="forward", check_sortedness=False)
        .with_columns((pl.col("next_event") - pl.col("date")).dt.total_days().alias("days_to_event"))
    )


def count_events_spanned(pillars_df: pl.DataFrame, events_df: pl.DataFrame) -> pl.DataFrame:
    """`pillars_df` plus `n_events`: event sessions in `(date, expiration]` for the symbol."""
    events = events_df.select("symbol", "event_session")
    counts = (
        pillars_df.select("date", "symbol", "expiration")
        .unique()
        .join(events, on="symbol", how="left")
        .with_columns(
            ((pl.col("event_session") > pl.col("date")) & (pl.col("event_session") <= pl.col("expiration"))).alias("spans")
        )
        .group_by("date", "symbol", "expiration")
        .agg(pl.col("spans").sum().cast(pl.Int64).alias("n_events"))
    )
    return pillars_df.join(counts, on=["date", "symbol", "expiration"], how="left").with_columns(pl.col("n_events").fill_null(0))


@dataclass(frozen=True)
class JumpConfig:
    history_window: int = 250  # sessions of own term estimates for the fallback median
    history_min: int = 20
    max_gap_days: int = 90  # ignore a term estimate whose two pillars are further apart than this


def estimate_jump_variance(pillars_df: pl.DataFrame, events_df: pl.DataFrame, config: JumpConfig = JumpConfig()) -> pl.DataFrame:
    """`JUMP_SCHEMA` per (date, symbol) from the term structure around the next event.

    `pillars_df` is `(date, symbol, expiration, dte, atm_iv)` for any number of symbols.
    """
    with_event = attach_next_event(pillars_df.select("date", "symbol").unique(), events_df)
    frame = pillars_df.join(with_event, on=["date", "symbol"], how="left").with_columns(
        (pl.col("atm_iv") ** 2 * pl.col("dte") / DAYS_PER_YEAR).alias("w"),
        (pl.col("expiration") >= pl.col("next_event")).alias("after"),
    )
    before = (
        frame.filter(~pl.col("after") & pl.col("next_event").is_not_null())
        .sort("dte", descending=True)
        .group_by("date", "symbol", maintain_order=True)
        .agg(pl.col("w").first().alias("w_b"), pl.col("dte").first().alias("t_b"))
    )
    after = (
        frame.filter(pl.col("after"))
        .sort("dte")
        .group_by("date", "symbol", maintain_order=True)
        .agg(pl.col("w").first().alias("w_a"), pl.col("dte").first().alias("t_a"))
    )
    term = (
        with_event.join(before, on=["date", "symbol"], how="left")
        .join(after, on=["date", "symbol"], how="left")
        .with_columns(
            pl.when((pl.col("t_a") - pl.col("t_b")) <= config.max_gap_days)
            .then((pl.col("w_a") - pl.col("w_b") / pl.col("t_b") * pl.col("t_a")).clip(lower_bound=0.0))
            .otherwise(None)
            .alias("jump_term")
        )
        .sort("symbol", "date")
        .with_columns(
            pl.col("jump_term")
            .shift(1)
            .rolling_median(window_size=config.history_window, min_samples=config.history_min)
            .over("symbol")
            .alias("jump_history")
        )
        .with_columns(
            pl.coalesce("jump_term", "jump_history").alias("jump_var"),
            pl.when(pl.col("next_event").is_null())
            .then(pl.lit("none"))
            .when(pl.col("jump_term").is_not_null())
            .then(pl.lit("term"))
            .when(pl.col("jump_history").is_not_null())
            .then(pl.lit("history"))
            .otherwise(pl.lit("none"))
            .alias("method"),
        )
    )
    return term.select(list(JUMP_SCHEMA)).sort("date", "symbol")


class TermStructureEarningsAdjuster(EarningsAdjuster):
    """Strip `n_events × J` of total variance from every pillar that spans an announcement."""

    def __init__(self, events_df: pl.DataFrame, config: JumpConfig = JumpConfig(), floor_fraction: float = 0.25) -> None:
        self.events_df = events_df
        self.config = config
        self.floor_fraction = floor_fraction
        self.jump_df: pl.DataFrame | None = None  # the last estimate, for inspection

    def adjust(self, pillars: pl.LazyFrame) -> pl.LazyFrame:
        pillars_df = pillars.collect()
        if pillars_df.is_empty():
            return pillars_df.lazy()
        self.jump_df = estimate_jump_variance(pillars_df, self.events_df, self.config)
        spanned = count_events_spanned(pillars_df, self.events_df)
        adjusted = (
            spanned.join(self.jump_df.select("date", "symbol", "jump_var"), on=["date", "symbol"], how="left")
            .with_columns(
                (pl.col("atm_iv") ** 2 - pl.col("n_events") * pl.col("jump_var").fill_null(0.0) * DAYS_PER_YEAR / pl.col("dte")).alias("var_adj")
            )
            .with_columns(
                pl.max_horizontal(pl.col("var_adj"), pl.col("atm_iv") ** 2 * self.floor_fraction).sqrt().alias("atm_iv")
            )
            .select(pillars_df.columns)
        )
        return adjusted.lazy()
