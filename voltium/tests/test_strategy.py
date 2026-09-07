import datetime as dt

import polars as pl

from synthetic import make_chain
from voltium.backtester import BacktestConfig, Backtester
from voltium.instruments.straddle import DeltaHedgedStraddle, StraddleConfig
from voltium.loaders import CONTRACT_MULTIPLIER
from voltium.portfolio import Portfolio
from voltium.providers.base import PanelProvider
from voltium.providers.calendar import TradingCalendar
from voltium.providers.chain import FrameChainProvider
from voltium.strategy import FixedTargetStrategy, RankWeightedStrategy
from voltium.trade_generator import TradeGenerator, TradeGeneratorConfig

DATE = dt.date(2024, 1, 2)


def make_signal(scores: dict[str, float | None]) -> PanelProvider:
    return PanelProvider(
        pl.DataFrame(
            {"date": [DATE] * len(scores), "symbol": list(scores), "signal": list(scores.values())},
            schema={"date": pl.Date, "symbol": pl.Utf8, "signal": pl.Float64},
        )
    )


def chain_for(symbols: list[str]) -> pl.DataFrame:
    return pl.DataFrame({"symbol": symbols}, schema={"symbol": pl.Utf8})


def test_linear_rank_book_is_vega_neutral_at_gross_and_shorts_rich():
    symbols = [f"S{i}" for i in range(10)]
    signal = make_signal({s: float(i) for i, s in enumerate(symbols)})
    strategy = RankWeightedStrategy(signal, gross_vega=1_000.0, min_names=5)
    targets_df = strategy.generate_targets(DATE, chain_for(symbols), Portfolio())
    assert abs(targets_df["target_vega"].sum()) < 1e-9
    assert abs(targets_df["target_vega"].abs().sum() - 1_000.0) < 1e-9
    by_symbol = dict(zip(targets_df["symbol"], targets_df["target_vega"]))
    assert by_symbol["S9"] < 0 < by_symbol["S0"]  # richest is shorted
    assert abs(by_symbol["S9"]) == abs(by_symbol["S0"])
    assert abs(by_symbol["S9"]) > abs(by_symbol["S8"]) > abs(by_symbol["S7"])


def test_quantile_rank_book_holds_only_the_tails_equal_vega():
    symbols = [f"S{i}" for i in range(20)]
    signal = make_signal({s: float(i) for i, s in enumerate(symbols)})
    strategy = RankWeightedStrategy(signal, gross_vega=1_000.0, scheme="quantile", quantile=0.1, min_names=5)
    targets_df = strategy.generate_targets(DATE, chain_for(symbols), Portfolio())
    held_df = targets_df.filter(pl.col("target_vega") != 0)
    assert set(held_df["symbol"]) == {"S0", "S1", "S18", "S19"}
    assert (held_df["target_vega"].abs() == 250.0).all()


def test_rank_book_only_sizes_eligible_names_and_drops_nulls():
    signal = make_signal({"A": 1.0, "B": 2.0, "C": 3.0, "D": None, "E": 4.0, "F": 5.0})
    strategy = RankWeightedStrategy(signal, gross_vega=100.0, min_names=2)
    targets_df = strategy.generate_targets(DATE, chain_for(["A", "B", "C", "D", "E"]), Portfolio())
    assert set(targets_df["symbol"]) == {"A", "B", "C", "E"}  # F not in chain, D has no score


def test_fractional_trade_generator_hits_the_target_vega_exactly():
    dates = [DATE]
    chain_df = make_chain(dates, {DATE: 100.0}, {DATE: 0.30})
    instrument = DeltaHedgedStraddle(StraddleConfig(target_dte=60, roll_dte=20))
    for fractional in (False, True):
        config = TradeGeneratorConfig.research() if fractional else TradeGeneratorConfig(min_oi=0, band=0.0)
        backtester = Backtester(
            calendar=TradingCalendar(dates),
            chain=FrameChainProvider(chain_df),
            instrument=instrument,
            strategy=FixedTargetStrategy({"XYZ": 333.0}),
            trade_generator=TradeGenerator(instrument, config),
            config=BacktestConfig(start=DATE, end=DATE),
        )
        records_df, _ = backtester.run(progress=False)
        row = records_df.row(0, named=True)
        assert row["option_cost"] == 0.0 and row["hedge_cost"] == 0.0  # NoCost by default
        if fractional:
            assert abs(row["dollar_vega"] - 333.0) < 1e-9
            assert row["contracts"] != round(row["contracts"])
        else:
            assert row["contracts"] == round(row["contracts"])
            assert abs(row["dollar_vega"] - 333.0) > 1.0
