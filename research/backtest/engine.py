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
from research.backtest import panel as panel_module
from research.backtest import pnl as pnl_module
from research.backtest.config import BacktestConfig, BacktestResult
from research.backtest.eligibility import apply_filters, describe
from research.backtest.portfolio import assign_quantiles, size_positions
from research.backtest.signals import build_forecasts
from research.backtest.structures import summarize_positions


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

    return BacktestContext(
        selection_df=selection_df,
        forecasts_df=forecasts_df,
        underlying_df=underlying_df,
        splits_df=splits_df,
        calendar_df=calendar_df,
    )


def get_marks(context: BacktestContext, legs_df: pl.DataFrame, holding_days: int) -> pl.DataFrame:
    """Fetch (and cache) daily quotes for the contracts a run actually selected.

    Keyed on the structure's contract set and the holding window, so every
    configuration sharing a structure and a hold reuses one fetch — which is
    every point in an OI-threshold sweep.
    """
    contracts_df = (
        legs_df.group_by("symbol", "expiration", "strike", "right")
        .agg(pl.col("date").min().alias("mark_from"), pl.col("date").max().alias("mark_to"))
    )
    # The hold runs past the last formation date, so the mark window has to be
    # extended by the holding period plus a weekend allowance.
    contracts_df = contracts_df.with_columns(
        (pl.col("mark_to") + pl.duration(days=int(holding_days * 1.6) + 5)).alias("mark_to")
    )

    key = (contracts_df.height, holding_days, int(contracts_df["mark_from"].min().toordinal()))
    if key not in context.marks_cache:
        print(f"  fetching marks for {contracts_df.height:,} contracts", flush=True)
        context.marks_cache[key] = panel_module.build_marks_panel(contracts_df)
    return context.marks_cache[key]


def run(config: BacktestConfig, context: BacktestContext) -> BacktestResult:
    """One configuration, end to end."""
    diagnostics: dict = {"filters": describe(config.eligibility)}

    legs_df = config.structure.select(context.selection_df)
    positions_df = summarize_positions(legs_df)
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
    diagnostics["formation_days"] = sized_df["date"].n_unique()
    diagnostics["mean_names_per_side"] = float(sized_df.group_by("date", "side").len()["len"].mean())

    # Marks are fetched for every *ranked* name, not just the two traded
    # deciles. The strategy only needs the traded legs, but the decile
    # monotonicity check needs all ten, and fetching only the extremes would
    # silently truncate the middle deciles at entry and report them as flat.
    ranked_legs_df = legs_df.join(ranked_df.select("date", "symbol"), on=["date", "symbol"], how="semi")
    marks_df = get_marks(context, ranked_legs_df, config.holding_days)
    traded_legs_df = legs_df.join(sized_df.select("date", "symbol"), on=["date", "symbol"], how="semi")

    holdings_df = pnl_module.build_holdings(
        sized_df, traded_legs_df, context.calendar_df, config.holding_days
    )
    marked_df = pnl_module.attach_marks(holdings_df, marks_df)
    kept_df = pnl_module.truncate_at_failure(marked_df, context.splits_df)
    diagnostics["marks_dropped_frac"] = round(1 - kept_df.height / max(marked_df.height, 1), 4)

    by_cohort = pnl_module.compute_pnl(
        kept_df, context.underlying_df, config.hedge_delta, config.spread_cost_fraction
    )

    # Entry day carries no P&L; it is the mark the first change is measured from.
    live = by_cohort.filter(pl.col("k") > 0)

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
    scale = 1.0 / config.holding_days
    daily_pnl = daily_pnl.with_columns(
        [
            pl.col(c) * scale
            for c in ["option_pnl", "hedge_pnl", "cost", "gross_pnl",
                      "total_pnl", "long_pnl", "short_pnl"]
        ]
    )

    decile_pnl = (
        pnl_module.compute_pnl(
            pnl_module.truncate_at_failure(
                pnl_module.attach_marks(
                    pnl_module.build_holdings(
                        size_all_quantiles(ranked_df, config), ranked_legs_df, context.calendar_df, config.holding_days
                    ),
                    marks_df,
                ),
                context.splits_df,
            ),
            context.underlying_df,
            config.hedge_delta,
            config.spread_cost_fraction,
        )
        .filter(pl.col("k") > 0)
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
