import datetime as dt

import numpy as np
import polars as pl

from synthetic import make_chain, third_friday
from voltium.backtester import BacktestConfig, Backtester
from voltium.costs import HalfSpreadCost, PerShareCost
from voltium.instruments.straddle import DeltaHedgedStraddle, StraddleConfig
from voltium.loaders import CONTRACT_MULTIPLIER
from voltium.portfolio import Portfolio
from voltium.providers.calendar import TradingCalendar
from voltium.providers.chain import FrameChainProvider
from voltium.strategy import FixedTargetStrategy
from voltium.trade_generator import TradeGenerator, TradeGeneratorConfig


def business_days(start: dt.date, n: int) -> list[dt.date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def build(chain_df: pl.DataFrame, dates: list[dt.date], target: float, rebalance: str = "daily") -> Backtester:
    instrument = DeltaHedgedStraddle(StraddleConfig(target_dte=60, roll_dte=20))
    return Backtester(
        calendar=TradingCalendar(dates),
        chain=FrameChainProvider(chain_df),
        instrument=instrument,
        strategy=FixedTargetStrategy({"XYZ": target}),
        trade_generator=TradeGenerator(instrument, TradeGeneratorConfig(min_oi=0, band=0.0)),
        config=BacktestConfig(start=dates[0], end=dates[-1], rebalance=rebalance),
        option_cost=HalfSpreadCost(),
        hedge_cost=PerShareCost(0.01),
    )


def test_long_straddle_with_no_iv_or_spot_change_loses_theta_minus_costs():
    dates = business_days(dt.date(2024, 1, 2), 2)
    spots = {d: 100.0 for d in dates}
    ivs = {d: 0.30 for d in dates}
    chain_df = make_chain(dates, spots, ivs)
    # Day-2 mids are exactly day-1 mids plus one day of vendor theta, so the
    # assertion is exact rather than "close to Black-Scholes decay".
    day1 = chain_df.filter(pl.col("date") == dates[0])
    day2 = day1.with_columns(
        pl.lit(dates[1]).alias("date"),
        (pl.col("mid") + pl.col("theta")).alias("mid"),
    ).with_columns((pl.col("mid") - 0.05).alias("bid"), (pl.col("mid") + 0.05).alias("ask"))
    chain_df = pl.concat([day1, day2])

    backtester = build(chain_df, dates, target=1_000.0)
    records_df, portfolio = backtester.run(progress=False)

    d1 = records_df.filter(pl.col("date") == dates[0]).row(0, named=True)
    d2 = records_df.filter(pl.col("date") == dates[1]).row(0, named=True)
    contracts = d1["contracts"]
    assert contracts > 0
    # entry cost: half spread on both legs = 0.05 * 2 * 100 per contract, plus hedge shares
    assert abs(d1["option_cost"] - contracts * 0.10 * CONTRACT_MULTIPLIER) < 1e-9
    assert d1["hedge_cost"] > 0

    unit = portfolio.positions["XYZ"].unit
    legs = day1.filter(pl.col("expiration") == unit.expiration, pl.col("strike") == unit.legs[0].strike)
    theta_per_straddle = legs["theta"].sum()
    expected_option_pnl = theta_per_straddle * CONTRACT_MULTIPLIER * contracts
    assert abs(d2["option_pnl"] - expected_option_pnl) < 1e-9
    assert d2["hedge_pnl"] == 0.0
    # the day-2 net P&L is theta minus the (tiny) rehedge cost
    net = d2["option_pnl"] + d2["hedge_pnl"] - d2["option_cost"] - d2["hedge_cost"]
    assert net <= expected_option_pnl + 1e-9
    assert abs(net - (expected_option_pnl - d2["hedge_cost"])) < 1e-9


def test_delta_hedge_leaves_book_delta_at_zero_each_day():
    rng = np.random.default_rng(0)
    dates = business_days(dt.date(2024, 1, 2), 40)
    spot = 100.0
    spots, ivs = {}, {}
    for d in dates:
        spot *= float(np.exp(rng.normal(0, 0.02)))
        spots[d] = spot
        ivs[d] = float(0.30 + rng.normal(0, 0.02))
    chain_df = make_chain(dates, spots, ivs, expirations=[third_friday(2024, m) for m in range(2, 8)], strikes=np.arange(60.0, 141.0, 2.5))
    backtester = build(chain_df, dates, target=2_000.0, rebalance="weekly")
    records_df, portfolio = backtester.run(progress=False)
    assert records_df.height == len(dates)
    assert records_df["book_delta"].abs().max() < 1e-9
    # rolled at least once over 40 sessions starting from a ~60 dte series
    assert (records_df["event"] == "roll").sum() >= 1
    # hedge is -delta * 100 * contracts every day
    assert (records_df["hedge_shares"] + records_df["contracts"] * 0 == records_df["hedge_shares"]).all()


def test_backtester_is_restartable_from_saved_portfolio(tmp_path):
    rng = np.random.default_rng(1)
    dates = business_days(dt.date(2024, 1, 2), 20)
    spot = 100.0
    spots, ivs = {}, {}
    for d in dates:
        spot *= float(np.exp(rng.normal(0, 0.015)))
        spots[d] = spot
        ivs[d] = 0.3
    chain_df = make_chain(dates, spots, ivs, strikes=np.arange(60.0, 141.0, 2.5))
    full_df, _ = build(chain_df, dates, 1_500.0, "weekly").run(progress=False)

    first_df, portfolio = build(chain_df, dates[:10], 1_500.0, "weekly").run(progress=False)
    portfolio.save(tmp_path / "book.json")
    resumed = Portfolio.load(tmp_path / "book.json")
    second_df, _ = build(chain_df, dates, 1_500.0, "weekly").run(portfolio=resumed, progress=False)
    stitched_df = pl.concat([first_df, second_df])
    assert stitched_df.height == full_df.height
    for col in ("option_pnl", "hedge_pnl", "option_cost", "hedge_cost", "contracts", "hedge_shares"):
        assert (stitched_df[col] - full_df[col]).abs().max() < 1e-9
