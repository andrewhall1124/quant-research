"""Layer 2: configuration in, dollars out.

    result = run(BacktestConfig(signal=VrpSignal(), structure=AtmStraddle()), context)

Everything expensive lives in the `BacktestContext` — the selection panel, the
vol forecasts, the marks, the hedge prices — and is built once. A run over that
context is seconds, which is what makes an OI x holding-period grid a loop
rather than an overnight job.

The order of operations is the whole design:

    select structures  ->  summarize to one row per name-day
                       ->  attach signal (needs the traded contract's IV)
                       ->  apply eligibility (formation only)
                       ->  rank into quantiles
                       ->  size to a dollar vega budget
                       ->  expand into overlapping cohorts
                       ->  mark daily, hedge, aggregate

Selection comes before the signal on purpose: the score is built from the IV of
the contract actually being traded, so signal and instrument cannot drift apart.
"""

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import polars as pl

import data_access_layer as dal
from tools.backtest import panel as panel_module
from tools.backtest import pnl as pnl_module
from tools.backtest.config import BacktestConfig, BacktestResult
from tools.backtest.eligibility import apply_filters, describe
from tools.backtest.portfolio import assign_quantiles, size_positions
from tools.backtest.signals import build_forecasts
from tools.backtest.structures import summarize_positions


def build_returns(start: date | None = None, end: date | None = None) -> pl.DataFrame:
    """Split-adjusted log returns for the names whose adjustment is verified.

    Identical construction to `research/single_name_vol/panel.py`, and for the
    same reasons: raw prices, six 2025 splits and a spinoff, a reused ticker
    with pre-listing stub rows. Each of `trusted_symbols`, `with_actions`,
    `in_universe` and `split_adjusted_return` removes a different artefact.

    Reaching before 2025 needs care, because the point-in-time membership table
    only covers the option window — asking for `in_universe` over 2023 returns
    nothing. So the two periods are spliced rather than loaded together: the
    option window is universe-restricted as usual, and the pre-sample history
    is restricted to the symbols that survived that restriction. The history is
    only ever used to burn a volatility model in, never to form a position, so
    it needs to be clean rather than point-in-time.
    """
    panel_df = dal.load_underlying(
        dal.trusted_symbols(), None, end, with_actions=True, in_universe=True
    )
    frames = [panel_df]

    if start is not None and start < panel_df["date"].min():
        history_df = dal.load_underlying(
            panel_df["symbol"].unique().to_list(),
            start,
            panel_df["date"].min(),
            with_actions=True,
            with_history=True,
        ).filter(pl.col("date") < panel_df["date"].min())
        frames.insert(0, history_df)

    prices_df = pl.concat(frames, how="vertical_relaxed").unique(subset=["symbol", "date"])
    return (
        prices_df.sort("symbol", "date")
        .with_columns(dal.split_adjusted_return().over("symbol"))
        .with_columns((1 + pl.col("return")).log().alias("ret"))
        .select("date", "symbol", "ret")
        .drop_nulls("ret")
    )


@dataclass
class BacktestContext:
    """The expensive, configuration-independent inputs, built once."""

    selection_df: pl.DataFrame
    forecasts_df: pl.DataFrame
    underlying_df: pl.DataFrame
    splits_df: pl.DataFrame
    calendar_df: pl.DataFrame
    earnings_df: pl.DataFrame
    marks_cache: dict = field(default_factory=dict)


def build_context(
    start: date | None = None,
    end: date | None = None,
    horizon: int = 21,
    burn_in: int = 120,
    forecast_start: date | None = None,
    selection_path=None,
    forecast_cache=None,
    refresh: bool = False,
) -> BacktestContext:
    """Load the selection panel and fit the vol forecasts.

    `forecast_start` lets the return history reach back further than the option
    sample so the model burn-in does not consume formation dates. With only
    2025 underlying on disk a 120-day burn-in costs roughly half the backtest;
    with 2023-2024 loaded as well it costs none of it.
    """
    selection_df = (
        panel_module.load_selection_panel()
        if selection_path is None
        else panel_module.load_selection_panel(selection_path)
    )
    if start is not None:
        selection_df = selection_df.filter(pl.col("date") >= start)
    if end is not None:
        selection_df = selection_df.filter(pl.col("date") <= end)

    # Fitting GARCH and ARCH at every origin for 500 names is the one slow
    # step in layer 2, so it is cached like `single_name_vol/panel.parquet`:
    # a research artefact, rebuilt with `refresh`, never written to data_store.
    cache_path = Path(forecast_cache) if forecast_cache else panel_module.PANEL_DIR / "forecasts.parquet"
    if cache_path.exists() and not refresh:
        forecasts_df = pl.read_parquet(cache_path)
        print(f"context: forecasts from cache ({forecasts_df['symbol'].n_unique()} names)", flush=True)
    else:
        # Only names that appear in the selection panel can ever be traded, so
        # fitting a model for anything else is wasted work.
        tradeable = selection_df["symbol"].unique()
        returns_df = build_returns(forecast_start, end).filter(pl.col("symbol").is_in(tradeable))
        print(f"context: fitting forecasts on {returns_df['symbol'].n_unique()} names", flush=True)
        forecasts_df = build_forecasts(returns_df, horizon=horizon, burn_in=burn_in)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        forecasts_df.write_parquet(cache_path)

    underlying_df = pnl_module.load_hedge_prices(forecast_start, end)
    splits_df = underlying_df.filter(pl.col("split_ratio") != 1.0).select(
        "symbol", "date", "split_ratio"
    )
    calendar_df = pnl_module.build_calendar(underlying_df["date"].unique().to_list())

    # Earnings distance for every (symbol, date) a structure could select. A
    # cross-sectional implied-vol sort is known to load on earnings timing, so
    # this rides along as a column and lets a filter or a signal condition on
    # it — the same treatment every other eligibility metric gets.
    # The earnings table is spelled in Wikipedia tickers and the option store
    # strips the dot (BRK.B -> BRKB, BF.B -> BFB), so a naive join silently
    # loses those names. Stripping the dot on both sides is enough: it is the
    # only transformation between the two spellings.
    earnings_dates_df = dal.load_earnings().with_columns(
        pl.col("symbol").str.replace_all(r"\.", "").alias("symbol")
    )
    keys_df = selection_df.select("symbol", "date").unique()
    events_df = earnings_dates_df.select(
        "symbol", pl.col("date").alias("earnings_date")
    ).unique().sort("symbol", "earnings_date")
    ordered = keys_df.sort("symbol", "date")
    forward = ordered.join_asof(
        events_df, left_on="date", right_on="earnings_date", by="symbol",
        strategy="forward", check_sortedness=False,
    ).select("symbol", "date", pl.col("earnings_date").alias("next_earnings"))
    backward = ordered.join_asof(
        events_df, left_on="date", right_on="earnings_date", by="symbol",
        strategy="backward", check_sortedness=False,
    ).select("symbol", "date", pl.col("earnings_date").alias("previous_earnings"))
    earnings_df = (
        keys_df.join(forward, on=["symbol", "date"], how="left")
        .join(backward, on=["symbol", "date"], how="left")
        .with_columns(
            (pl.col("next_earnings") - pl.col("date")).dt.total_days().alias("days_to_earnings"),
            (pl.col("date") - pl.col("previous_earnings")).dt.total_days().alias("days_since_earnings"),
        )
        .select("symbol", "date", "days_to_earnings", "days_since_earnings")
    )

    return BacktestContext(
        selection_df=selection_df,
        forecasts_df=forecasts_df,
        underlying_df=underlying_df,
        splits_df=splits_df,
        calendar_df=calendar_df,
        earnings_df=earnings_df,
    )


def get_marks(
    context: BacktestContext,
    legs_df: pl.DataFrame,
    holding_days: int,
    cache_key: tuple,
    hold_to_expiry: bool = False,
) -> pl.DataFrame:
    """Fetch (and cache) daily quotes for the contracts a structure can select.

    `legs_df` is every leg the structure produced, *before* any eligibility
    filter. That is deliberate and it is what makes a filter sweep affordable:
    keying the cache on the filtered set would miss on every threshold, so an
    OI grid would refetch the marks once per point. Fetching the superset once
    costs a larger single read and turns the whole sweep into a dictionary
    lookup.

    `cache_key` therefore identifies the structure and the holding period only.
    """
    contracts_df = (
        legs_df.group_by("symbol", "expiration", "strike", "right")
        .agg(pl.col("date").min().alias("mark_from"), pl.col("date").max().alias("mark_to"))
    )
    # The hold runs past the last formation date, so the mark window has to be
    # extended by the holding period plus a weekend allowance.
    if hold_to_expiry:
        # Every contract has to be markable right up to the day it settles.
        contracts_df = contracts_df.with_columns(
            pl.max_horizontal("mark_to", "expiration").alias("mark_to")
        )
    else:
        contracts_df = contracts_df.with_columns(
            (pl.col("mark_to") + pl.duration(days=int(holding_days * 1.6) + 5)).alias("mark_to")
        )

    if cache_key not in context.marks_cache:
        print(f"  fetching marks for {contracts_df.height:,} contracts", flush=True)
        context.marks_cache[cache_key] = panel_module.build_marks_panel(contracts_df)
    return context.marks_cache[cache_key]


def build_cohorts(
    sized_df: pl.DataFrame,
    legs_df: pl.DataFrame,
    calendar_df: pl.DataFrame,
    config: BacktestConfig,
) -> pl.DataFrame:
    """Expand sized positions into daily marks, by whichever exit rule applies."""
    if config.hold_to_expiry:
        return pnl_module.build_holdings_to_expiry(sized_df, legs_df, calendar_df)
    return pnl_module.build_holdings(sized_df, legs_df, calendar_df, config.holding_days)


def run(config: BacktestConfig, context: BacktestContext) -> BacktestResult:
    """One configuration, end to end."""
    diagnostics: dict = {"filters": describe(config.eligibility)}

    legs_df = config.structure.select(context.selection_df)
    positions_df = summarize_positions(legs_df).join(
        context.earnings_df, on=["symbol", "date"], how="left"
    ).with_columns(
        # For an option, the question is not "how far away is the
        # announcement" but "does it happen before this contract expires".
        # That is the binary that moves implied vol, and it is what makes a
        # 30-day sort different from a 120-day one: a 30-day window either
        # contains an earnings date or it does not, while a 120-day window
        # almost always contains exactly one.
        (pl.col("days_to_earnings") <= pl.col("dte")).alias("earnings_before_expiry")
    )
    diagnostics["name_days_selected"] = positions_df.height

    scored_df = config.signal.attach(positions_df, context.forecasts_df)
    diagnostics["name_days_scored"] = scored_df.height

    eligible_df = apply_filters(scored_df, config.eligibility)
    diagnostics["name_days_eligible"] = eligible_df.height
    if eligible_df.height == 0:
        raise ValueError(f"no name-days survived filters: {describe(config.eligibility)}")

    ranked_df = assign_quantiles(eligible_df, config.n_quantiles)
    sized_df = size_positions(
        ranked_df,
        config.long_quantile,
        config.short_index(),
        config.gross_vega_per_side,
        config.min_names_per_side,
    )
    if sized_df.height == 0:
        raise ValueError(
            "no day had enough eligible names on both sides to form a portfolio"
        )
    diagnostics["formation_days"] = sized_df["date"].n_unique()
    diagnostics["mean_names_per_side"] = float(sized_df.group_by("date", "side").len()["len"].mean())

    # Marks are fetched for every *scored* name — not just the two traded
    # deciles, and not just the eligible ones. Two reasons. The decile
    # monotonicity check needs all ten deciles, and fetching only the extremes
    # would silently truncate the middle ones at entry and report them flat.
    # And keying the cache on the post-filter set would miss on every point of
    # a filter sweep, so an OI grid would refetch its marks once per threshold.
    scored_legs_df = legs_df.join(scored_df.select("date", "symbol"), on=["date", "symbol"], how="semi")
    marks_df = get_marks(
        context,
        scored_legs_df,
        config.holding_days,
        (
            getattr(config.structure, "name", "structure"),
            "expiry" if config.hold_to_expiry else config.holding_days,
        ),
        config.hold_to_expiry,
    )
    ranked_legs_df = legs_df.join(ranked_df.select("date", "symbol"), on=["date", "symbol"], how="semi")
    traded_legs_df = legs_df.join(sized_df.select("date", "symbol"), on=["date", "symbol"], how="semi")

    holdings_df = build_cohorts(sized_df, traded_legs_df, context.calendar_df, config)
    marked_df = pnl_module.attach_marks(holdings_df, marks_df)
    if config.hold_to_expiry:
        marked_df = pnl_module.apply_expiry_settlement(marked_df)
    kept_df = pnl_module.truncate_at_failure(marked_df, context.splits_df)
    diagnostics["marks_dropped_frac"] = round(1 - kept_df.height / max(marked_df.height, 1), 4)

    by_cohort = pnl_module.compute_pnl(
        kept_df, context.underlying_df, config.hedge_delta, config.spread_cost_fraction,
        charge_exit_cost=not config.hold_to_expiry,
    )

    # The entry day is kept, not dropped. It carries no market P&L by
    # construction — `d_mid` and the prior delta are both null there, so the
    # option and hedge legs are zero — but it is where the entry half of the
    # bid-ask cost is booked. Filtering it out silently charged only the exit
    # crossing, which understated the cost of a round trip by half and made
    # hold-to-expiry (entry-only) look free.
    live = by_cohort

    daily_pnl = (
        live.group_by("mark_date")
        .agg(
            pl.col("option_pnl").sum(),
            pl.col("hedge_pnl").sum(),
            pl.col("cost").sum(),
            pl.col("gross_pnl").sum(),
            pl.col("total_pnl").sum(),
            (pl.col("total_pnl") * (pl.col("side") > 0)).sum().alias("long_pnl"),
            (pl.col("total_pnl") * (pl.col("side") < 0)).sum().alias("short_pnl"),
            pl.col("position_vega").abs().sum().alias("gross_vega"),
            pl.len().alias("n_positions"),
        )
        .rename({"mark_date": "date"})
        .sort("date")
    )
    # Overlapping cohorts mean h are live at once; divide so the reported series
    # is the P&L of one book run at the target vega, not h books stacked.
    # Overlapping cohorts mean several are live at once; divide so the series
    # is one book at the target vega rather than N books stacked. Held to
    # expiry the count is the realised life of a cohort, not `holding_days`.
    live_cohorts = (
        float(live.group_by("mark_date").agg(
            pl.struct("formation_date", "symbol").n_unique().alias("n")
        )["n"].mean() / max(diagnostics["mean_names_per_side"] * 2, 1.0))
        if config.hold_to_expiry else float(config.holding_days)
    )
    diagnostics["mean_live_cohorts"] = round(live_cohorts, 1)
    scale = 1.0 / max(live_cohorts, 1.0)
    daily_pnl = daily_pnl.with_columns(
        [
            pl.col(c) * scale
            for c in ["option_pnl", "hedge_pnl", "cost", "gross_pnl",
                      "total_pnl", "long_pnl", "short_pnl"]
        ]
    )

    decile_marked_df = pnl_module.attach_marks(
        build_cohorts(
            size_all_quantiles(ranked_df, config), ranked_legs_df, context.calendar_df, config
        ),
        marks_df,
    )
    if config.hold_to_expiry:
        decile_marked_df = pnl_module.apply_expiry_settlement(decile_marked_df)
    decile_pnl = (
        pnl_module.compute_pnl(
            pnl_module.truncate_at_failure(decile_marked_df, context.splits_df),
            context.underlying_df,
            config.hedge_delta,
            config.spread_cost_fraction,
            charge_exit_cost=not config.hold_to_expiry,
        )
        .group_by("mark_date", "quantile")
        .agg((pl.col("total_pnl").sum() * scale).alias("pnl"))
        .rename({"mark_date": "date"})
        .sort("date", "quantile")
    ) if config.n_quantiles > 2 else pl.DataFrame()

    return BacktestResult(
        config=config,
        daily_pnl=daily_pnl,
        decile_pnl=decile_pnl,
        positions=by_cohort,
        diagnostics=diagnostics,
    )


def size_all_quantiles(ranked_df: pl.DataFrame, config: BacktestConfig) -> pl.DataFrame:
    """Size every quantile long, so the monotonicity check covers the whole sort."""
    counts = ranked_df.group_by("date", "quantile").agg(pl.len().alias("n_side"))
    return (
        ranked_df.join(counts, on=["date", "quantile"], how="left")
        .with_columns(pl.lit(1.0).alias("side"))
        .with_columns((config.gross_vega_per_side / pl.col("n_side")).alias("target_vega"))
        .with_columns((pl.col("target_vega") / pl.col("vega")).alias("quantity"))
        .filter(pl.col("quantity").is_finite())
    )
