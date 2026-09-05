"""Signal, screens, sizing and P&L for the one-day IV z-score straddle book.

`panel.py` produces contracts; this turns them into a portfolio. Everything
here is a pure function of the panel so that a robustness run is a keyword
argument rather than an edited copy of the code.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import polars as pl
import statsmodels.api as sm

from data_access_layer import (
    load_corporate_actions,
    load_earnings,
    load_universe,
    usable_symbol_years,
)

TRADING_DAYS = 252

# One vol point of vega on each side of the book. The book is vega-neutral by
# construction, so this is a scale choice, not a risk choice: every reported
# Sharpe, t-statistic and break-even is invariant to it, and only the dollar
# axes move.
VEGA_PER_SIDE = 10_000.0
# Contract multiplier: one option contract is 100 shares, and `vega` is a
# per-share sensitivity to a 1.00 move in vol — so `vega` itself is already
# dollars per vol *point* per contract.
MULTIPLIER = 100.0
# Charged on the delta hedge, both legs of the round trip. Single-name equity
# at the close is the cheapest thing in this trade by an order of magnitude;
# it is here so the number is not zero, not because it is the binding cost.
STOCK_COST_BPS = 1.0


@dataclass
class Config:
    """One specification of the strategy. Defaults are the headline run."""

    window: int = 60
    # Sessions between the close that produces the signal and the close that
    # enters the trade. 0 is the strategy as specified — form and execute on
    # the same print — and is therefore exposed to the bid-ask bounce in that
    # print. 1 buys at a quote the signal did not see. See REPORT.md.
    signal_lag: int = 0
    decile: float = 0.10
    min_names: int = 100
    max_relative_spread: float = 0.50
    min_straddle_mid: float = 0.20
    min_price: float = 5.0
    # Calendar-day ceiling on the rolling window, as a multiple of its length
    # in sessions. 60 sessions of a continuously quoted name span ~86 calendar
    # days, so 2.0 admits a few holidays and gaps and rejects a stale root
    # being scored against vol it printed half a year earlier. Expressed as a
    # ratio rather than a constant so a 252-session window is not rejected
    # outright.
    max_window_span_ratio: float = 2.0
    spread_fractions: tuple[float, ...] = (0.25, 0.50)
    exclude_earnings_within: int | None = None
    label: str = "base"
    years: tuple[int, ...] | None = None
    extra: dict = field(default_factory=dict)


def screen_panel(panel_df: pl.DataFrame) -> pl.DataFrame:
    """Drop what cannot be traded or cannot be trusted, before any ranking.

    Order matters only in that all of it happens before the z-score: screening
    after ranking would let a name that is about to be dropped still push the
    decile boundary around.
    """
    usable = usable_symbol_years().with_columns(pl.col("year").cast(pl.Int32))
    members = (
        load_universe(with_history=True)
        .select("date", pl.col("ticker").alias("symbol"))
        .unique()
    )
    # A split between the two closes re-strikes the contract, so the strike
    # match at t+1 is a different instrument and the spot move is fictional.
    splits = (
        load_corporate_actions(kind="split")
        .select("symbol", pl.col("date").alias("split_date"))
        .unique()
    )
    screened = (
        panel_df.with_columns(pl.col("date").dt.year().cast(pl.Int32).alias("year"))
        .join(usable, on=["symbol", "year"], how="inner")
        .join(members, on=["date", "symbol"], how="inner")
        .filter(
            pl.col("next_straddle_mid").is_not_null(),
            pl.col("straddle_vega") > 0,
            pl.col("straddle_iv") > 0.01,
            pl.col("straddle_mid") > 0,
        )
        .join(splits, on="symbol", how="left")
        .with_columns(
            (
                (pl.col("split_date") > pl.col("date"))
                & (pl.col("split_date") <= pl.col("next_date"))
            ).fill_null(False).alias("split_in_hold")
        )
        .group_by("symbol", "date")
        .agg(pl.all().exclude("split_date", "split_in_hold").first(),
             pl.col("split_in_hold").any())
        .filter(~pl.col("split_in_hold"))
        .drop("split_in_hold")
        .sort("symbol", "date")
    )
    return screened


def add_signal(screened_df: pl.DataFrame, config: Config) -> pl.DataFrame:
    """Rolling z-score of the straddle's own implied vol, per symbol.

    The window is `config.window` *observations*, and the span guard rejects a
    window whose 60 rows are spread over more calendar time than a name with a
    continuous quote history would take — a stale root must not be scored
    against vol it printed half a year earlier.
    """
    window = config.window
    scored = screened_df.sort("symbol", "date").with_columns(
        pl.col("straddle_iv").rolling_mean(window, min_periods=window)
        .over("symbol").alias("iv_mean"),
        pl.col("straddle_iv").rolling_std(window, min_periods=window)
        .over("symbol").alias("iv_std"),
        (pl.col("date") - pl.col("date").shift(window - 1))
        .dt.total_days().over("symbol").alias("window_span"),
    )
    scored = scored.with_columns(
        pl.when(
            (pl.col("iv_std") > 0)
            & (pl.col("window_span") <= config.window * config.max_window_span_ratio)
        )
        .then((pl.col("straddle_iv") - pl.col("iv_mean")) / pl.col("iv_std"))
        .otherwise(None)
        .alias("iv_zscore")
    )
    return add_lagged_signal(scored, config.signal_lag)


def add_lagged_signal(scored_df: pl.DataFrame, lag: int) -> pl.DataFrame:
    """The z-score the book is actually ranked on, `lag` sessions stale.

    Shifting by rows is not enough: a symbol with a gap in its chain would pick
    up a signal from an arbitrary distance back. The session index is built on
    the market calendar the panel itself spans, so a shift is only accepted
    when the two rows really are consecutive sessions.
    """
    calendar = (
        scored_df.select("date").unique().sort("date")
        .with_row_index("session").with_columns(pl.col("session").cast(pl.Int64))
    )
    with_session = scored_df.join(calendar, on="date", how="left").sort("symbol", "date")
    if lag == 0:
        return with_session.with_columns(pl.col("iv_zscore").alias("signal"))
    return with_session.with_columns(
        pl.when(
            (pl.col("session") - pl.col("session").shift(lag).over("symbol")) == lag
        )
        .then(pl.col("iv_zscore").shift(lag).over("symbol"))
        .otherwise(None)
        .alias("signal")
    )


def apply_liquidity(scored_df: pl.DataFrame, config: Config) -> pl.DataFrame:
    """Tradability screen, applied after the signal so it never censors history.

    A name whose quote is too wide today should not be traded today, but its IV
    is still a legitimate observation in tomorrow's rolling window — which is
    why this runs here and not in `screen_panel`.
    """
    return scored_df.with_columns(
        (pl.col("straddle_spread") / pl.col("straddle_mid")).alias("relative_spread")
    ).filter(
        pl.col("signal").is_not_null(),
        pl.col("relative_spread") <= config.max_relative_spread,
        pl.col("straddle_mid") >= config.min_straddle_mid,
        pl.col("underlying_price") >= config.min_price,
    )


def add_earnings_distance(frame_df: pl.DataFrame) -> pl.DataFrame:
    """Calendar days from the formation date to the next announcement."""
    events = (
        load_earnings()
        .select("symbol", pl.col("date").alias("earnings_date"))
        .unique()
        .sort("symbol", "earnings_date")
    )
    ordered = frame_df.sort("symbol", "date")
    forward = ordered.join_asof(
        events, left_on="date", right_on="earnings_date", by="symbol",
        strategy="forward", check_sortedness=False,
    ).select(
        "symbol", "date",
        (pl.col("earnings_date") - pl.col("date")).dt.total_days()
        .alias("days_to_earnings"),
    )
    return frame_df.join(forward, on=["symbol", "date"], how="left")


def add_contract_pnl(frame_df: pl.DataFrame) -> pl.DataFrame:
    """Per-contract delta-hedged P&L, and its decomposition, before sizing.

    `pnl_per_vega` divides the dollar result by the position's dollar vega, so
    it reads as the vol points the trade actually earned — the only unit in
    which a $400 stock and a $30 stock are comparable.
    """
    option_pnl = (pl.col("next_straddle_mid") - pl.col("straddle_mid")) * MULTIPLIER
    hedge_pnl = (
        -pl.col("straddle_delta")
        * MULTIPLIER
        * (pl.col("next_underlying_price") - pl.col("underlying_price"))
    )
    return frame_df.with_columns(
        option_pnl.alias("option_pnl"),
        hedge_pnl.alias("hedge_pnl"),
        (option_pnl + hedge_pnl).alias("contract_pnl"),
        ((pl.col("next_straddle_iv") - pl.col("straddle_iv")) * 100).alias("iv_change_points"),
    ).with_columns(
        (pl.col("contract_pnl") / pl.col("straddle_vega")).alias("pnl_per_vega"),
        # First-order vega attribution: what the position would have made if
        # only implied vol had moved, holding everything else fixed.
        (pl.col("straddle_vega") * pl.col("iv_change_points")).alias("vega_pnl"),
    )


def build_book(traded_df: pl.DataFrame, config: Config) -> pl.DataFrame:
    """Rank into deciles, size to equal vega, and cost the round trip.

    Two things are enforced rather than approximated: every name in a leg
    carries the same dollar vega, and the two legs carry the same dollar vega
    as each other. So the book's net vega is zero by construction and its
    P&L is a pure cross-sectional bet.
    """
    ranked = traded_df.with_columns(
        pl.len().over("date").alias("names_today"),
        pl.col("signal").rank("ordinal").over("date").alias("iv_rank"),
    ).filter(pl.col("names_today") >= config.min_names)

    cut = (pl.col("names_today") * config.decile).ceil()
    ranked = ranked.with_columns(
        pl.when(pl.col("iv_rank") <= cut)
        .then(1)  # cheapest vol -> long
        .when(pl.col("iv_rank") > pl.col("names_today") - cut)
        .then(-1)  # richest vol -> short
        .otherwise(0)
        .alias("side"),
        ((pl.col("iv_rank") - 1) / pl.col("names_today") * 10)
        .floor().clip(0, 9).cast(pl.Int32)
        .alias("iv_decile"),
    )

    book = ranked.filter(pl.col("side") != 0).with_columns(
        pl.len().over("date", "side").alias("leg_names")
    )
    book = book.with_columns(
        (VEGA_PER_SIDE / pl.col("leg_names") / pl.col("straddle_vega"))
        .alias("contracts")
    )
    entry_spread = pl.col("straddle_spread") * MULTIPLIER * pl.col("contracts")
    exit_spread = pl.col("next_straddle_spread") * MULTIPLIER * pl.col("contracts")
    hedge_notional = (
        pl.col("straddle_delta").abs() * MULTIPLIER * pl.col("contracts")
        * (pl.col("underlying_price") + pl.col("next_underlying_price"))
    )
    return book.with_columns(
        (pl.col("side") * pl.col("contracts") * pl.col("contract_pnl")).alias("pnl"),
        (pl.col("side") * pl.col("contracts") * pl.col("vega_pnl")).alias("book_vega_pnl"),
        (pl.col("contracts") * pl.col("straddle_vega")).alias("position_vega"),
        # Full round trip: the entry crossing on the formation date and the
        # exit crossing the next day. Charging only the exit halves every cost
        # figure and doubles every break-even.
        (entry_spread + exit_spread).alias("spread_cost_full"),
        (hedge_notional * STOCK_COST_BPS / 10_000).alias("hedge_cost"),
    ).with_columns(
        pl.col("book_vega_pnl").fill_null(0.0),
    )


def daily_pnl(book_df: pl.DataFrame, config: Config) -> pl.DataFrame:
    """Collapse the book to one row per formation date."""
    aggregates = [
        pl.col("pnl").sum().alias("gross_pnl"),
        pl.col("book_vega_pnl").sum().alias("vega_pnl"),
        pl.col("spread_cost_full").sum().alias("spread_cost_full"),
        pl.col("hedge_cost").sum().alias("hedge_cost"),
        pl.len().alias("positions"),
        pl.col("names_today").first().alias("names_today"),
        pl.col("straddle_iv").mean().alias("mean_iv"),
        pl.col("relative_spread").mean().alias("mean_relative_spread"),
        (pl.col("side") * pl.col("position_vega")).sum().alias("net_vega"),
        (pl.col("side") * pl.col("contracts") * pl.col("straddle_delta")
         * MULTIPLIER * pl.col("underlying_price")).sum().alias("net_hedge_notional"),
        (pl.col("contracts") * pl.col("straddle_mid") * MULTIPLIER).sum()
        .alias("gross_premium"),
        pl.col("pnl").filter(pl.col("side") == 1).sum().alias("long_pnl"),
        pl.col("pnl").filter(pl.col("side") == -1).sum().alias("short_pnl"),
    ]
    daily = book_df.group_by("date").agg(aggregates).sort("date")
    for fraction in config.spread_fractions:
        tag = f"{int(fraction * 100)}"
        daily = daily.with_columns(
            (
                pl.col("gross_pnl")
                - fraction * pl.col("spread_cost_full")
                - pl.col("hedge_cost")
            ).alias(f"net_pnl_{tag}")
        )
    return daily


def newey_west_tstat(values: np.ndarray, lags: int = 5) -> tuple[float, float]:
    """Mean and its HAC t-statistic. Daily and non-overlapping, but the
    portfolio's own turnover leaves a little serial correlation, so the errors
    are still lag-corrected rather than assumed independent."""
    clean = values[~np.isnan(values)]
    if clean.size < 30:
        return float("nan"), float("nan")
    model = sm.OLS(clean, np.ones_like(clean)).fit(
        cov_type="HAC", cov_kwds={"maxlags": lags}
    )
    return float(model.params[0]), float(model.tvalues[0])


def summarize(daily_df: pl.DataFrame, config: Config) -> dict:
    """Headline statistics for one specification."""
    if daily_df.is_empty():
        return {"label": config.label, "days": 0}
    gross = daily_df["gross_pnl"].to_numpy()
    mean, tstat = newey_west_tstat(gross)
    stats = {
        "label": config.label,
        "days": daily_df.height,
        "start": daily_df["date"].min(),
        "end": daily_df["date"].max(),
        "mean_names": float(daily_df["names_today"].mean()),
        "positions_per_side": float(daily_df["positions"].mean()) / 2,
        "gross_pnl_per_day": mean,
        "gross_tstat": tstat,
        "gross_sharpe": annualized_sharpe(gross),
        "gross_total": float(np.nansum(gross)),
        "hit_rate": float(np.mean(gross > 0)),
        "gross_premium_per_day": float(daily_df["gross_premium"].mean()),
        # The book has no capital base of its own, so the only honest scale
        # reference is the premium it puts up: P&L as a share of that.
        "gross_return_on_premium_bps": float(
            np.nanmean(gross) / daily_df["gross_premium"].mean() * 10_000
        ),
        "vega_share_of_pnl": float(
            np.nansum(daily_df["vega_pnl"].to_numpy()) / np.nansum(gross)
        ),
    }
    cost_at_one = daily_df["spread_cost_full"].to_numpy()
    hedge = daily_df["hedge_cost"].to_numpy()
    stats["spread_cost_full_per_day"] = float(np.nanmean(cost_at_one))
    stats["hedge_cost_per_day"] = float(np.nanmean(hedge))
    # The share of the quoted spread the strategy can pay and still break even.
    net_of_hedge = np.nanmean(gross) - np.nanmean(hedge)
    stats["breakeven_spread_fraction"] = (
        net_of_hedge / np.nanmean(cost_at_one) if np.nanmean(cost_at_one) > 0 else float("nan")
    )
    for fraction in config.spread_fractions:
        tag = f"{int(fraction * 100)}"
        net = daily_df[f"net_pnl_{tag}"].to_numpy()
        net_mean, net_t = newey_west_tstat(net)
        stats[f"net_{tag}_pnl_per_day"] = net_mean
        stats[f"net_{tag}_tstat"] = net_t
        stats[f"net_{tag}_sharpe"] = annualized_sharpe(net)
    return stats


def annualized_sharpe(values: np.ndarray) -> float:
    """Sharpe of a dollar P&L series at a constant vega scale.

    No risk-free rate: the book is a self-financing vega-neutral spread, so
    there is no capital base for a cash return to be earned on.
    """
    clean = values[~np.isnan(values)]
    if clean.size < 30 or clean.std(ddof=1) == 0:
        return float("nan")
    return float(clean.mean() / clean.std(ddof=1) * np.sqrt(TRADING_DAYS))


def run(panel_df: pl.DataFrame, config: Config, prepared_df: pl.DataFrame | None = None):
    """Panel to (book, daily, stats) for one configuration.

    `prepared_df` lets a caller reuse the expensive screen-and-score stage
    across specifications that only differ in how the book is formed.
    """
    if prepared_df is None:
        prepared_df = prepare(panel_df, config)
    traded = prepared_df
    if config.years is not None:
        traded = traded.filter(pl.col("date").dt.year().is_in(list(config.years)))
    if config.exclude_earnings_within is not None:
        traded = traded.filter(
            pl.col("days_to_earnings").is_null()
            | (pl.col("days_to_earnings") > config.exclude_earnings_within)
        )
    book = build_book(traded, config)
    daily = daily_pnl(book, config)
    return book, daily, summarize(daily, config)


def prepare(panel_df: pl.DataFrame, config: Config) -> pl.DataFrame:
    """Screen, score, cost-screen and price every symbol-day. The slow part."""
    screened = screen_panel(panel_df)
    scored = add_signal(screened, config)
    tradable = apply_liquidity(scored, config)
    with_pnl = add_contract_pnl(tradable)
    return add_earnings_distance(with_pnl)
