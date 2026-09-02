"""Build the single-name forecast panel: one row per (symbol, date, horizon).

The expensive half of this study. For every S&P 500 name with a 2025 chain it
produces, using information through `t` only:

* `RV`     — trailing realized vol over `h` days,
* `ARCH`   — ARCH(5), expanding window,
* `GARCH`  — GARCH(1,1), expanding window,
* `IV`     — 30-day ATM implied vol inverted from the name's own chain,

and the two targets, forward realized vol over `t+1..t+h` and the name's own
implied vol `h` days ahead.

Building it takes ~15 minutes, so the result is cached as a parquet under
`results/`. That cache is a research artefact, not a dataset: nothing here
writes to `data_store/`, and `--refresh` rebuilds it from scratch.

Two things about single names that the SPX study never had to handle:

* **Prices are raw.** Returns come from `load_underlying(with_actions=True,
  in_universe=True)` and `split_adjusted_return()`, never `close.diff()`, and
  the universe is restricted to `trusted_symbols()` — the names whose split
  adjustment has been checked against a second source. Skipping any of that
  books a -93% day on ORLY and poisons every window that touches it.
* **There is no VIX per name.** Implied vol has to be inverted from mids, which
  means the American-exercise caveat in the report applies. Chains are also
  ragged: half the universe has no weekly listings, so a 30-day measure is
  available almost every day and a 7-day one is not.
"""

import argparse
import time
from datetime import date

import numpy as np
import polars as pl

import data_access_layer as dal
from data_access_layer import paths
from research.vol_models import (
    fit_and_forecast_horizons,
    forward_realized_vol,
    trailing_realized_vol,
)
from research.volatility.implied_vol import atm_vol_for_expiry

HORIZONS = [5, 21]
BURN_IN = 120
REFIT_EVERY = 5
TARGET_DAYS = 30
# A name needs enough sessions to burn in a GARCH and still leave origins.
MIN_TRADING_DAYS = 200
MIN_ORIGINS = 40

PANEL_PATH = paths.REPO_ROOT / "research" / "single_name_vol" / "results" / "panel.parquet"


def build_returns(start: date | None = None, end: date | None = None) -> pl.DataFrame:
    """Split-adjusted log returns for every name whose adjustment is verified.

    `with_actions` attaches the ex-date split ratio and `split_adjusted_return`
    applies it, so the six 2025 splits and the DD spinoff stop being -60% to
    -93% days. `in_universe` drops the pre-listing stub rows ThetaData returns
    for a reused ticker (SOLS), and `trusted_symbols()` keeps only the names
    whose adjustment agrees with a second source.

    The models want log returns; the loader gives a simple one.
    """
    prices_df = dal.load_underlying(
        dal.trusted_symbols(), start, end, with_actions=True, in_universe=True
    )
    return (
        prices_df.sort("symbol", "date")
        .with_columns(dal.split_adjusted_return().over("symbol"))
        .with_columns((1 + pl.col("return")).log().alias("ret"))
        .select("date", "symbol", "ret")
    )


def build_atm_iv(symbol: str, rates: dict[date, float], target_days: int = TARGET_DAYS) -> pl.DataFrame:
    """Daily ATM implied vol for one name, interpolated to `target_days`.

    Same construction as the SPX check in `research/volatility/implied_vol.py`:
    imply the forward from put-call parity at the tightest strike, invert
    Black-76 for the call and the put nearest that forward, average, and
    interpolate the two bracketing expirations in total variance.

    Taking the forward from parity rather than assuming a dividend yield is what
    makes this usable on 500 names with 500 different dividend policies.
    """
    chain_df = dal.load_option_greeks(
        symbol, min_dte=3, max_dte=90, max_moneyness=0.10
    ).filter(pl.col("bid") > 0)
    if chain_df.height == 0:
        return pl.DataFrame(schema={"date": pl.Date, "atm_iv": pl.Float64})

    rows = []
    for (quote_date,), day_df in chain_df.group_by(["date"], maintain_order=True):
        discount_rate = rates.get(quote_date, 0.04)
        available = sorted(day_df["dte"].unique().to_list())
        below = [dte for dte in available if dte <= target_days]
        above = [dte for dte in available if dte > target_days]
        if not below or not above:
            continue

        near_ttm, near_vol = atm_vol_for_expiry(day_df.filter(pl.col("dte") == below[-1]), discount_rate)
        far_ttm, far_vol = atm_vol_for_expiry(day_df.filter(pl.col("dte") == above[0]), discount_rate)
        if not (np.isfinite(near_vol) and np.isfinite(far_vol)):
            continue

        target_ttm = target_days / 365.0
        weight = (far_ttm - target_ttm) / (far_ttm - near_ttm)
        total_variance = weight * near_vol**2 * near_ttm + (1 - weight) * far_vol**2 * far_ttm
        rows.append({"date": quote_date, "atm_iv": float(np.sqrt(total_variance / target_ttm))})

    if not rows:
        return pl.DataFrame(schema={"date": pl.Date, "atm_iv": pl.Float64})
    return pl.DataFrame(rows).sort("date")


def build_symbol(symbol: str, returns_df: pl.DataFrame, rates: dict[date, float]) -> pl.DataFrame:
    """One name's forecasts and targets, both horizons, long by horizon."""
    iv_df = build_atm_iv(symbol, rates)
    if iv_df.height == 0:
        return pl.DataFrame()

    # Left join: a day with returns but no invertible chain keeps its returns
    # and loses only the IV forecast, and is dropped later by the row filter.
    aligned_df = returns_df.join(iv_df, on="date", how="left").sort("date")
    if aligned_df.height < MIN_TRADING_DAYS:
        return pl.DataFrame()

    returns = aligned_df["ret"].to_numpy()
    implied = aligned_df["atm_iv"].to_numpy()
    arch = fit_and_forecast_horizons(returns, HORIZONS, "ARCH", BURN_IN, REFIT_EVERY)
    garch = fit_and_forecast_horizons(returns, HORIZONS, "GARCH", BURN_IN, REFIT_EVERY)

    frames = []
    for horizon in HORIZONS:
        forward_iv = np.concatenate([implied[horizon:], np.full(horizon, np.nan)])
        frames.append(
            pl.DataFrame(
                {
                    "symbol": symbol,
                    "date": aligned_df["date"],
                    "horizon": horizon,
                    "target_rv": forward_realized_vol(returns, horizon),
                    "target_iv": forward_iv,
                    "RV": trailing_realized_vol(returns, horizon),
                    "ARCH": arch[horizon],
                    "GARCH": garch[horizon],
                    "IV": implied,
                }
            )
        )
    return pl.concat(frames)


def build_panel(symbols: list[str] | None = None, verbose: bool = True) -> pl.DataFrame:
    """Sweep the universe. Returns the panel with every incomplete row dropped."""
    returns_df = build_returns()
    if verbose:
        print(f"returns: {returns_df.height} rows, {returns_df['symbol'].n_unique()} trusted symbols")
    rate_df = dal.load_yields("13w").select("date", pl.col("yield").alias("rate"))
    rates = dict(zip(rate_df["date"].to_list(), rate_df["rate"].to_list()))

    wanted = symbols or paths.available_option_symbols(greeks=True)
    frames, skipped = [], []
    started = time.time()
    for position, symbol in enumerate(wanted, start=1):
        if verbose and position % 25 == 0:
            elapsed = time.time() - started
            remaining = elapsed / position * (len(wanted) - position)
            print(f"  {position}/{len(wanted)} symbols, {elapsed / 60:.1f} min in, ~{remaining / 60:.1f} min left")
        symbol_returns_df = returns_df.filter(pl.col("symbol") == symbol).select("date", "ret")
        if symbol_returns_df.height < MIN_TRADING_DAYS:
            skipped.append(symbol)
            continue
        frame = build_symbol(symbol, symbol_returns_df, rates)
        if frame.height == 0:
            skipped.append(symbol)
            continue
        frames.append(frame)

    panel_df = pl.concat(frames).drop_nulls().filter(
        pl.all_horizontal(
            pl.col(column).is_finite() for column in ["target_rv", "target_iv", "RV", "ARCH", "GARCH", "IV"]
        )
    )
    # A name that survives with a handful of origins adds noise, not breadth.
    counts_df = panel_df.group_by("symbol", "horizon").len()
    thin = counts_df.filter(pl.col("len") < MIN_ORIGINS)["symbol"].unique().to_list()
    panel_df = panel_df.filter(~pl.col("symbol").is_in(thin))

    if verbose:
        print(f"skipped {len(skipped)} symbols for short history or no invertible chain")
        print(f"dropped {len(thin)} symbols with fewer than {MIN_ORIGINS} origins")
        print(f"panel: {panel_df.height} rows, {panel_df['symbol'].n_unique()} symbols")
    return panel_df


def load_panel(refresh: bool = False) -> pl.DataFrame:
    """The cached panel, built on first use."""
    if PANEL_PATH.exists() and not refresh:
        return pl.read_parquet(PANEL_PATH)
    panel_df = build_panel()
    PANEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    panel_df.write_parquet(PANEL_PATH)
    return panel_df


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="rebuild the cached panel")
    parser.add_argument("--symbols", nargs="*", help="restrict the sweep (for a smoke test)")
    arguments = parser.parse_args()
    if arguments.symbols:
        print(build_panel(arguments.symbols))
    else:
        print(load_panel(refresh=arguments.refresh))
