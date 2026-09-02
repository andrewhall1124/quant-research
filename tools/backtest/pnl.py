"""Mark cohorts daily and turn them into dollars.

Daily formation with an `h`-day hold means `h` overlapping cohorts are live at
once. Each is opened on its own date, held for `h` trading days, and marked
every day in between; the portfolio's P&L on any date is the sum across
whatever cohorts are alive. That is the Jegadeesh-Titman construction: it
keeps daily formation, turns the book over at 1/h per day, and uses every
formation date rather than one in every h.

## Greek units

ThetaData quotes per share, per unit of vol, which produces one convenient
coincidence and two traps:

* `mid` and `delta` are **per share** — multiply by 100 for a contract.
* `vega` is per share per *unit* of vol (1.00 = 100 vol points), so
  `vega * 100 shares * 0.01 vol` = `vega` unchanged. **Stored vega is already
  dollars per contract per vol point**, and must NOT be multiplied by 100.
  An ATM 32-day AAPL contract at 27.5% vol shows 27.67, which is exactly the
  dollars it gains if implied vol rises one point.
* `theta` is per share per day; multiply by 100.

Getting this wrong scales the vega budget by 100 and every dollar figure with
it, so the engine asserts the resulting book vega matches its target.

## What ends a position early

Filters bind at formation, so a name failing a screen mid-hold changes
nothing. Two things do end a position early, and both are hard failures rather
than choices:

* **A missing mark.** The contract stops being quoted. The cohort is closed at
  its last good mark rather than carried at a stale price.
* **A split inside the hold.** The contract's terms are restruck by the OCC,
  so the pre-split and post-split rows are not the same instrument and their
  price change is not a P&L. Those cohorts are truncated at the last
  pre-split mark and counted in the diagnostics.
"""

import polars as pl

import data_access_layer as dal

SHARES_PER_CONTRACT = 100


def build_calendar(dates: list) -> pl.DataFrame:
    """Trading days as a contiguous integer index, so holds are date arithmetic."""
    ordered = sorted(set(dates))
    return pl.DataFrame({"date": ordered, "t": list(range(len(ordered)))})


def build_holdings(
    sized_df: pl.DataFrame,
    legs_df: pl.DataFrame,
    calendar_df: pl.DataFrame,
    holding_days: int,
) -> pl.DataFrame:
    """Expand each sized position into one row per (cohort, leg, mark date)."""
    positions = sized_df.select(
        pl.col("date").alias("formation_date"), "symbol", "quantity", "side", "quantile", "score"
    )
    legs = legs_df.select(
        pl.col("date").alias("formation_date"),
        "symbol", "expiration", "strike", "right", "ratio",
    )
    cohorts = positions.join(legs, on=["formation_date", "symbol"], how="inner")

    offsets = pl.DataFrame({"k": list(range(holding_days + 1))})
    return (
        cohorts.join(
            calendar_df.select(pl.col("date").alias("formation_date"), pl.col("t").alias("t0")),
            on="formation_date",
            how="inner",
        )
        .join(offsets, how="cross")
        .with_columns((pl.col("t0") + pl.col("k")).alias("t"))
        .join(calendar_df.select("t", pl.col("date").alias("mark_date")), on="t", how="inner")
    )


def build_holdings_to_expiry(
    sized_df: pl.DataFrame,
    legs_df: pl.DataFrame,
    calendar_df: pl.DataFrame,
) -> pl.DataFrame:
    """Expand each position and hold it until its contract expires.

    The alternative to a fixed holding period, and the cheaper one: a position
    carried to expiration settles at intrinsic rather than being sold, so it
    crosses the quoted spread once instead of twice. Since spread is the
    binding constraint on this kind of strategy, halving the number of
    crossings is worth more than any signal improvement measured so far.

    The holding period is then whatever the tenor implies — a 60-day contract
    is held about 60 days — so this cannot be combined with a holding-period
    sweep. That is the trade being made.
    """
    positions = sized_df.select(
        pl.col("date").alias("formation_date"), "symbol", "quantity", "side", "quantile", "score"
    )
    legs = legs_df.select(
        pl.col("date").alias("formation_date"),
        "symbol", "expiration", "strike", "right", "ratio",
    )
    cohorts = positions.join(legs, on=["formation_date", "symbol"], how="inner")

    # The last trading day at or before expiration. Expiration itself is a
    # trading day for listed equity options, but asof-joining is robust to a
    # holiday and to the calendar ending before a long-dated contract expires.
    sessions = calendar_df.sort("date")
    cohorts = (
        cohorts.join(
            sessions.select(pl.col("date").alias("formation_date"), pl.col("t").alias("t0")),
            on="formation_date",
            how="inner",
        )
        .sort("expiration")
        .join_asof(
            sessions.select(pl.col("date").alias("expiration"), pl.col("t").alias("t_last")),
            on="expiration",
            strategy="backward",
        )
        .filter(pl.col("t_last") >= pl.col("t0"))
    )

    return (
        cohorts.with_columns(
            pl.int_ranges(0, pl.col("t_last") - pl.col("t0") + 1).alias("k")
        )
        .explode("k")
        .with_columns((pl.col("t0") + pl.col("k")).alias("t"))
        .join(sessions.select("t", pl.col("date").alias("mark_date")), on="t", how="inner")
        .drop("t_last")
    )


def apply_expiry_settlement(marked_df: pl.DataFrame) -> pl.DataFrame:
    """Mark a contract at intrinsic value on its expiration date.

    On the last day the quote is unreliable and irrelevant: the position is
    not sold, it settles. Using intrinsic makes the final mark exact rather
    than a wide two-sided quote on a contract nobody is trading any more.
    """
    expiring = pl.col("mark_date") == pl.col("expiration")
    intrinsic = (
        pl.when(pl.col("right") == "CALL")
        .then((pl.col("underlying_price") - pl.col("strike")).clip(lower_bound=0.0))
        .otherwise((pl.col("strike") - pl.col("underlying_price")).clip(lower_bound=0.0))
    )
    return marked_df.with_columns(
        pl.when(expiring & pl.col("underlying_price").is_not_null())
        .then(intrinsic)
        .otherwise(pl.col("mid"))
        .alias("mid"),
        # A settled contract has no delta to hedge into the next day.
        pl.when(expiring).then(0.0).otherwise(pl.col("delta")).alias("delta"),
    )


def attach_marks(holdings_df: pl.DataFrame, marks_df: pl.DataFrame) -> pl.DataFrame:
    """Join each held leg to its quote on each mark date."""
    marks = marks_df.select(
        pl.col("date").alias("mark_date"),
        "symbol", "expiration", "strike", "right",
        "mid", "bid", "ask", "delta", "gamma", "theta", "vega",
        "implied_vol", "underlying_price",
    )
    return holdings_df.join(
        marks,
        on=["mark_date", "symbol", "expiration", "strike", "right"],
        how="left",
    )


def truncate_at_failure(marked_df: pl.DataFrame, splits_df: pl.DataFrame) -> pl.DataFrame:
    """Cut each cohort at its first missing mark or split, whichever comes first.

    Done on a cohort-wide basis rather than per leg: a straddle with one dead
    leg is not half a straddle, it is a position you can no longer mark.
    """
    cohort = ["formation_date", "symbol"]

    # A leg with no quote on a mark date poisons that date for the whole cohort.
    by_date = (
        marked_df.group_by([*cohort, "mark_date", "k"])
        .agg(pl.col("mid").is_null().any().alias("bad"))
        .sort([*cohort, "k"])
    )

    if splits_df.height:
        by_date = by_date.join(
            splits_df.select("symbol", pl.col("date").alias("mark_date"), "split_ratio"),
            on=["symbol", "mark_date"],
            how="left",
        ).with_columns(
            (pl.col("bad") | (pl.col("split_ratio").fill_null(1.0) != 1.0)).alias("bad")
        ).drop("split_ratio")

    # k = 0 is the entry mark; a cohort that cannot even be marked at entry is
    # dropped entirely by the inner join below.
    good = (
        by_date.with_columns(
            pl.col("bad").cast(pl.Int32).cum_sum().over(cohort).alias("failures")
        )
        .filter(pl.col("failures") == 0)
        .select([*cohort, "mark_date"])
    )
    return marked_df.join(good, on=[*cohort, "mark_date"], how="inner")


def spread_costs(legs: pl.DataFrame, fraction: float, charge_exit: bool = True) -> pl.DataFrame:
    """Charge `fraction` of the quoted spread on entry and on exit.

    `fraction = 0.5` is the usual assumption: you cross half the spread to get
    filled at the mid-to-touch, once opening and once closing. `1.0` pays the
    full quoted spread each way and is a hard lower bound on viability.

    Costs are charged against the *quotes on the day*, not a modelled average,
    because the spread is on the row: entry uses the formation-day quote, exit
    uses the quote on the last day the cohort was marked. That matters here —
    single-name option spreads widen exactly when the position is being closed
    in a stressed tape.

    `charge_exit=False` is the hold-to-expiry case: the position is not sold,
    it settles at intrinsic, so only the entry crossing is paid. That halves
    the bill, which is the whole point of holding to expiry.

    Nothing is charged on the delta hedge. Stock spreads are two to three
    orders of magnitude tighter than these option spreads, so they would not
    change any conclusion, and pretending to model them precisely would imply
    an accuracy this study does not have.
    """
    cohort = ["formation_date", "symbol"]
    leg = [*cohort, "expiration", "strike", "right"]
    ordered = legs.sort([*leg, "k"])
    edges = ordered.group_by(leg).agg(
        pl.col("k").min().alias("k_entry"), pl.col("k").max().alias("k_exit")
    )
    marked = ordered.join(edges, on=leg, how="left").with_columns(
        ((pl.col("ask") - pl.col("bid")).clip(lower_bound=0.0) * fraction
         * pl.col("units").abs() * SHARES_PER_CONTRACT).alias("edge_cost")
    )
    charged = (pl.col("k") == pl.col("k_entry"))
    if charge_exit:
        charged = charged | (pl.col("k") == pl.col("k_exit"))
    return marked.with_columns(
        pl.when(charged).then(pl.col("edge_cost")).otherwise(0.0).alias("cost")
    ).drop("k_entry", "k_exit", "edge_cost")


def compute_pnl(
    marked_df: pl.DataFrame,
    underlying_df: pl.DataFrame,
    hedge_delta: bool,
    spread_cost_fraction: float = 0.0,
    charge_exit_cost: bool = True,
) -> pl.DataFrame:
    """Daily dollar P&L per (cohort, mark date), split into option and hedge.

    The option leg is the mark-to-market change on the contracts held. The
    hedge leg is yesterday's position delta carried against today's move in the
    stock, which is what makes the result a statement about volatility rather
    than about direction.
    """
    cohort = ["formation_date", "symbol"]
    leg = [*cohort, "expiration", "strike", "right"]

    legs = (
        marked_df.sort([*leg, "k"])
        .with_columns(
            (pl.col("quantity") * pl.col("ratio")).alias("units"),
        )
        .with_columns(
            (pl.col("mid") - pl.col("mid").shift(1).over(leg)).alias("d_mid"),
            # Yesterday's greeks against today's move: the standard first-order
            # attribution. Everything they miss lands in the residual.
            pl.col("vega").shift(1).over(leg).alias("prior_vega"),
            pl.col("gamma").shift(1).over(leg).alias("prior_gamma"),
            pl.col("theta").shift(1).over(leg).alias("prior_theta"),
            (pl.col("implied_vol") - pl.col("implied_vol").shift(1).over(leg)).alias("d_iv"),
            (pl.col("underlying_price") - pl.col("underlying_price").shift(1).over(leg)).alias("d_underlying"),
            (pl.col("mark_date") - pl.col("mark_date").shift(1).over(leg))
            .dt.total_days().alias("days_elapsed"),
        )
        .with_columns(
            (pl.col("units") * pl.col("d_mid").fill_null(0.0) * SHARES_PER_CONTRACT).alias("option_pnl"),
            (pl.col("units") * pl.col("delta") * SHARES_PER_CONTRACT).alias("position_delta"),
            (pl.col("units") * pl.col("vega")).alias("position_vega"),
            # Attribution. `vega` as stored is dollars per contract per vol
            # point and `implied_vol` is a decimal, so the change needs the
            # same x100 the per-share quantities do. Theta is per share per
            # calendar day, and the gap between marks is three days over a
            # weekend.
            (
                pl.col("units") * pl.col("prior_vega").fill_null(0.0)
                * pl.col("d_iv").fill_null(0.0) * SHARES_PER_CONTRACT
            ).alias("vega_pnl"),
            (
                pl.col("units") * pl.col("prior_theta").fill_null(0.0)
                * pl.col("days_elapsed").fill_null(0).cast(pl.Float64) * SHARES_PER_CONTRACT
            ).alias("theta_pnl"),
            (
                pl.col("units") * 0.5 * pl.col("prior_gamma").fill_null(0.0)
                * pl.col("d_underlying").fill_null(0.0) ** 2 * SHARES_PER_CONTRACT
            ).alias("gamma_pnl"),
        )
    )
    legs = (
        spread_costs(legs, spread_cost_fraction, charge_exit_cost)
        if spread_cost_fraction
        else legs.with_columns(pl.lit(0.0).alias("cost"))
    )

    by_day = (
        legs.group_by([*cohort, "mark_date", "k"])
        .agg(
            pl.col("option_pnl").sum(),
            pl.col("vega_pnl").sum(),
            pl.col("theta_pnl").sum(),
            pl.col("gamma_pnl").sum(),
            pl.col("cost").sum(),
            pl.col("position_delta").sum(),
            pl.col("position_vega").sum(),
            pl.col("side").first(),
            pl.col("quantile").first(),
            pl.col("score").first(),
            pl.col("underlying_price").first(),
        )
        .sort([*cohort, "k"])
    )

    if hedge_delta:
        # Yesterday's delta against today's move. The stock change is
        # split-adjusted, and any cohort spanning a split was already cut.
        spot = underlying_df.select(
            "symbol", pl.col("date").alias("mark_date"), "close", "split_ratio"
        )
        by_day = (
            by_day.join(spot, on=["symbol", "mark_date"], how="left")
            .sort([*cohort, "k"])
            .with_columns(
                (
                    pl.col("close") * pl.col("split_ratio").fill_null(1.0)
                    - pl.col("close").shift(1).over(cohort)
                ).alias("d_spot"),
                pl.col("position_delta").shift(1).over(cohort).alias("prior_delta"),
            )
            .with_columns(
                (-pl.col("prior_delta").fill_null(0.0) * pl.col("d_spot").fill_null(0.0)).alias("hedge_pnl")
            )
        )
    else:
        by_day = by_day.with_columns(pl.lit(0.0).alias("hedge_pnl"))

    return by_day.with_columns(
        (pl.col("option_pnl") + pl.col("hedge_pnl") - pl.col("cost")).alias("total_pnl"),
        (pl.col("option_pnl") + pl.col("hedge_pnl")).alias("gross_pnl"),
    )


def load_hedge_prices(start=None, end=None) -> pl.DataFrame:
    """Split-adjusted stock closes for the hedge leg.

    Same discipline `research/single_name_vol/` needed: raw prices, verified
    symbols, point-in-time membership. A hedge marked against an unadjusted
    split books a fictional six-figure gain on one day.

    `with_history` so the prices span every year the option store covers; a
    hedge that stops at the start of the option sample silently truncates the
    backtest to whatever the price panel happens to hold.
    """
    return dal.load_underlying(
        dal.trusted_symbols(), start, end,
        with_actions=True, in_universe=True, with_history=True,
    ).select("date", "symbol", "close", "split_ratio", "dividend")
