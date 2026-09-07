import datetime as dt

import numpy as np
import polars as pl

from synthetic import make_chain, third_friday
from voltium.backtester import BacktestConfig, Backtester
from voltium.costs import HalfSpreadCost, PerShareCost
from voltium.instruments.straddle import DeltaHedgedStraddle, StraddleConfig
from voltium.panel_backtester import PanelBacktestConfig, PanelBacktester
from voltium.providers.base import PanelProvider
from voltium.providers.calendar import TradingCalendar
from voltium.providers.chain import FrameChainProvider
from voltium.providers.straddle_returns import compute_reference_path
from voltium.results import BacktestResults
from voltium.strategy import FixedTargetStrategy
from voltium.trade_generator import TradeGenerator, TradeGeneratorConfig


def business_days(start: dt.date, n: int) -> list[dt.date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def random_chain(seed: int, n_days: int, symbol: str) -> tuple[list[dt.date], pl.DataFrame]:
    rng = np.random.default_rng(seed)
    dates = business_days(dt.date(2024, 1, 2), n_days)
    spot, spots, ivs = 100.0, {}, {}
    for d in dates:
        spot *= float(np.exp(rng.normal(0, 0.02)))
        spots[d] = spot
        ivs[d] = float(0.30 + rng.normal(0, 0.02))
    chain_df = make_chain(dates, spots, ivs, symbol=symbol, expirations=[third_friday(2024, m) for m in range(2, 8)], strikes=np.arange(60.0, 141.0, 2.5))
    return dates, chain_df


def test_panel_engine_matches_chain_engine_for_a_fractional_book_through_a_roll():
    instrument = DeltaHedgedStraddle(StraddleConfig(target_dte=60, roll_dte=20))
    dates, chain_a = random_chain(0, 45, "AAA")
    _, chain_b = random_chain(1, 45, "BBB")
    calendar = TradingCalendar(dates)
    targets = {"AAA": 2_000.0, "BBB": -1_500.0}

    # the chain engine: fractional contracts, no screens, full half-spread costs
    chain_engine = Backtester(
        calendar=calendar,
        chain=FrameChainProvider(pl.concat([chain_a, chain_b])),
        instrument=instrument,
        strategy=FixedTargetStrategy(targets),
        trade_generator=TradeGenerator(instrument, TradeGeneratorConfig.research()),
        config=BacktestConfig(start=dates[0], end=dates[-1], rebalance="weekly"),
        option_cost=HalfSpreadCost(),
        hedge_cost=PerShareCost(),
    )
    chain_records_df, _ = chain_engine.run(progress=False)
    assert (chain_records_df["event"] == "roll").sum() >= 2

    # the panel engine, on reference paths built by the same code
    reference_df = pl.concat(
        [compute_reference_path(c, instrument, calendar, dates[0], dates[-1]) for c in (chain_a, chain_b)]
    )
    panel_engine = PanelBacktester(
        calendar, PanelProvider(reference_df), FixedTargetStrategy(targets), PanelBacktestConfig(dates[0], dates[-1], "weekly", cost_fraction=1.0)
    )
    panel_records_df, book = panel_engine.run(progress=False)

    chain_book = BacktestResults(chain_records_df).book_df
    panel_book = BacktestResults(panel_records_df).book_df
    assert chain_book.height == panel_book.height == len(dates)
    # the first session of each unit has no reference P&L; both engines earn nothing there anyway
    assert (chain_book["gross_pnl"] - panel_book["gross_pnl"]).abs().max() < 1e-6
    assert (chain_book["gross_vega"] - panel_book["gross_vega"]).abs().max() < 1e-6
    assert (chain_book["net_vega"] - panel_book["net_vega"]).abs().max() < 1e-6
    # costs match exactly except on rebalance sessions, where the chain engine
    # nets the resize's hedge fill against the daily rehedge (the panel carries
    # no per-unit delta, so it prices the rehedge alone); the gap is small
    rebalance = np.array([i == 0 or d.isocalendar()[:2] != dates[i - 1].isocalendar()[:2] for i, d in enumerate(dates)])
    cost_gap = (chain_book["cost"] - panel_book["cost"]).to_numpy()
    assert np.abs(cost_gap[~rebalance]).max() < 1e-6
    assert np.abs(cost_gap[rebalance]).sum() < 0.01 * chain_book["cost"].sum()
    assert abs(book.cumulative_pnl - (panel_book["net_pnl"].sum())) < 1e-6


def test_panel_engine_closes_a_name_whose_reference_row_disappears():
    dates = business_days(dt.date(2024, 1, 2), 6)
    rows = []
    for i, d in enumerate(dates):
        if i == 3:
            continue  # the reference unit was force-closed on day 3
        rows.append(
            {"date": d, "symbol": "X", "pnl_per_vega": 0.5 if i not in (0, 4) else None, "cost_per_vega": 0.0, "exit_cost_per_vega": 0.1,
             "dollar_vega": 10.0, "entry_vega": 10.0, "dollar_theta": 0.0, "mid": 1.0, "underlying": 100.0, "dte": 60, "event": "open" if i in (0, 4) else ""}
        )
    reference_df = pl.DataFrame(rows).with_columns(pl.col("dte").cast(pl.Int64))
    engine = PanelBacktester(TradingCalendar(dates), PanelProvider(reference_df), FixedTargetStrategy({"X": 100.0}), PanelBacktestConfig(dates[0], dates[-1], "daily"))
    records_df, _ = engine.run(progress=False)
    by_date = {r["date"]: r for r in records_df.to_dicts()}
    assert by_date[dates[0]]["contracts"] == 10.0 and by_date[dates[0]]["event"] == "open"
    assert by_date[dates[1]]["option_pnl"] == 10.0 * 10.0 * 0.5  # units × entry vega × pnl per vega
    assert by_date[dates[3]]["event"] == "forced_close" and by_date[dates[3]]["contracts"] == 0.0 and by_date[dates[3]]["option_pnl"] == 0.0
    assert by_date[dates[4]]["event"] == "open" and by_date[dates[4]]["contracts"] == 10.0
    assert by_date[dates[0]]["exit_cost"] == 10.0 * 0.1 * 10.0 and by_date[dates[0]]["option_cost"] == 0.0  # cost_fraction 0
