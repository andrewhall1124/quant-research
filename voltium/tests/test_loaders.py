import datetime as dt

import polars as pl
import pytest

from voltium.loaders import (
    OPTION_SCHEMA,
    STOCK_SCHEMA,
    UNIVERSE_SCHEMA,
    VegaConvention,
    run_vega_check,
    scan_options,
    scan_stocks,
    scan_universe,
)


def require_symbol(data_paths, symbol: str, year: int):
    if symbol not in data_paths.available_symbols("option_greeks", year):
        pytest.skip(f"{symbol} {year} chain not on disk")


def test_option_loader_produces_canonical_schema(data_paths):
    require_symbol(data_paths, "AAPL", 2024)
    frame = scan_options(
        data_paths, ["AAPL"], dt.date(2024, 3, 1), dt.date(2024, 3, 5)
    ).collect()
    assert frame.schema == pl.Schema(OPTION_SCHEMA)
    assert frame.height > 0
    assert set(frame["right"].unique().to_list()) <= {"C", "P"}
    assert frame["date"].min() >= dt.date(2024, 3, 1)
    assert frame["date"].max() <= dt.date(2024, 3, 5)
    assert frame["oi"].is_not_null().mean() > 0.9
    # mid is the quote midpoint; iv is a decimal
    assert (frame["mid"] - (frame["bid"] + frame["ask"]) / 2).abs().max() < 1e-9
    atm = frame.filter(pl.col("iv").is_not_null(), pl.col("delta").abs().is_between(0.4, 0.6))
    assert 0.05 < atm["iv"].median() < 1.0


def test_stock_and_universe_loaders_produce_canonical_schema(data_paths):
    stocks = scan_stocks(data_paths, dt.date(2024, 3, 1), dt.date(2024, 3, 5)).collect()
    assert stocks.schema == pl.Schema(STOCK_SCHEMA)
    assert stocks.height > 0
    universe = scan_universe(data_paths, dt.date(2024, 3, 1), dt.date(2024, 3, 5)).collect()
    assert universe.schema == pl.Schema(UNIVERSE_SCHEMA)
    assert 400 < universe.filter(pl.col("date") == dt.date(2024, 3, 1)).height < 520
    # option-root spelling: no dots
    assert not universe["symbol"].str.contains(r"\.").any()


def test_vega_convention_is_one_of_the_two_hypotheses(data_paths):
    require_symbol(data_paths, "AAPL", 2024)
    check = run_vega_check(data_paths.option_dir("option_greeks", 2024) / "AAPL.parquet")
    assert check.convention in (VegaConvention.PER_UNIT_IV, VegaConvention.PER_VOL_POINT)
    assert max(check.share_per_unit, check.share_per_point) >= 2 / 3
    # theta is quoted per calendar day
    assert check.theta_per_day_share > 0.9


def test_canonical_vega_is_per_vol_point(data_paths):
    """After loading, vega * 0.01 should NOT match a 1-point reprice; vega should."""
    require_symbol(data_paths, "AAPL", 2024)
    from voltium.utils import bs

    frame = (
        scan_options(data_paths, ["AAPL"], dt.date(2024, 3, 1), dt.date(2024, 3, 1))
        .filter(pl.col("iv").is_not_null(), pl.col("bid") > 0, pl.col("delta").abs().is_between(0.3, 0.7))
        .with_columns((pl.col("expiration") - pl.col("date")).dt.total_days().alias("dte"))
        .filter(pl.col("dte").is_between(20, 200))
        .collect()
    )
    spot = frame["underlying"].to_numpy()
    strike = frame["strike"].to_numpy()
    tau = frame["dte"].to_numpy() / 365.0
    sigma = frame["iv"].to_numpy()
    is_call = (frame["right"] == "C").to_numpy()
    change = bs.price(spot, strike, tau, 0.04, sigma + 0.01, is_call) - bs.price(
        spot, strike, tau, 0.04, sigma, is_call
    )
    ratio = change / frame["vega"].to_numpy()
    assert 0.9 < float(abs(ratio).mean()) < 1.1
