"""ThetaData end-of-day files -> the canonical voltium schema.

This is the only module that knows how ThetaData spells a column. Everything
above it depends on the canonical schemas below and nothing else.

What is on disk (see `DataPaths`): one parquet per symbol per year for option
greeks and for open interest; index roots (SPX, ...) in their own per-year
directories; a pair of stock files (history + current year); point-in-time
S&P 500 membership; a static GICS sector table; a treasury yield table.

Canonical option schema, one row per (date, symbol, expiration, strike, right):

    date: Date, symbol: Utf8, expiration: Date, strike: Float64,
    right: Utf8 ("C"/"P"), bid, ask, mid, iv, delta, gamma, theta, vega:
    Float64, underlying: Float64, oi: Int64, volume: Int64

Unit conventions fixed by this module:

* `iv` is a decimal (0.25 = 25%), null where the vendor's inversion failed
  (`|iv_error| > iv_error_tol`) or the vendor reports 0.
* `vega` is per share per **vol point** (a 0.01 change in `iv`). ThetaData
  quotes vega per 1.00 of IV; `detect_vega_convention` confirms this by
  repricing with Black-Scholes and the loader rescales accordingly. Contract
  dollar vega is therefore `vega * 100 * contracts`.
* `theta` is per share per calendar day (vendor convention, verified against
  Black-Scholes in the same check).
* `delta`, `gamma` are per share.
* `symbol` is the ThetaData *option* spelling everywhere (BRK.B -> BRKB),
  including the stock and universe frames, so the three join without a map.

Canonical stock schema: `date, symbol, open, high, low, close, volume`.
Universe schema: `date, symbol`.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from pathlib import Path

import numpy as np
import polars as pl

from voltium.utils import bs

log = logging.getLogger(__name__)

OPTION_SCHEMA: dict[str, pl.DataType] = {
    "date": pl.Date,
    "symbol": pl.Utf8,
    "expiration": pl.Date,
    "strike": pl.Float64,
    "right": pl.Utf8,
    "bid": pl.Float64,
    "ask": pl.Float64,
    "mid": pl.Float64,
    "iv": pl.Float64,
    "delta": pl.Float64,
    "gamma": pl.Float64,
    "theta": pl.Float64,
    "vega": pl.Float64,
    "underlying": pl.Float64,
    "oi": pl.Int64,
    "volume": pl.Int64,
}

STOCK_SCHEMA: dict[str, pl.DataType] = {
    "date": pl.Date,
    "symbol": pl.Utf8,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Int64,
}

UNIVERSE_SCHEMA: dict[str, pl.DataType] = {"date": pl.Date, "symbol": pl.Utf8}

CONTRACT_MULTIPLIER = 100

# Wikipedia ticker -> ThetaData option root, beyond dropping the dot. Mirrors
# data_pipelines.utils.symbols.TICKER_OVERRIDES["option"].
TICKER_OVERRIDES = {"BNY": "BK"}


def normalize_symbol(ticker: str) -> str:
    """Map a Wikipedia / stock-file ticker onto the option-root spelling."""
    return TICKER_OVERRIDES.get(ticker, ticker).replace(".", "")


class VegaConvention(Enum):
    """How the vendor scales vega. See `detect_vega_convention`."""

    PER_UNIT_IV = "per_1.00_iv"
    PER_VOL_POINT = "per_0.01_iv"


@dataclass(frozen=True)
class DataPaths:
    """Where the ThetaData files live and how the directories are stamped.

    The per-symbol option datasets are year-stamped (`option_greeks_2024/`)
    except for `sample_year`, whose two constituent directories kept their
    bare names (`option_greeks/`, `open_interest/`).
    """

    root: Path
    sample_year: int = 2025
    unstamped_datasets: frozenset[str] = field(
        default_factory=lambda: frozenset({"option_greeks", "open_interest"})
    )

    @classmethod
    def from_repo(cls, start: Path | None = None) -> "DataPaths":
        """Walk up from `start` (default: cwd) to the first `data_store/`."""
        here = (start or Path.cwd()).resolve()
        for candidate in (here, *here.parents):
            if (candidate / "data_store").is_dir():
                return cls(root=candidate / "data_store")
        raise FileNotFoundError("no data_store/ directory above " + str(here))

    def option_dir(self, dataset: str, year: int) -> Path:
        if year == self.sample_year and dataset in self.unstamped_datasets:
            return self.root / dataset
        return self.root / f"{dataset}_{year}"

    def available_years(self, dataset: str) -> list[int]:
        years = []
        for candidate in self.root.glob(f"{dataset}_*"):
            suffix = candidate.name[len(dataset) + 1 :]
            if candidate.is_dir() and suffix.isdigit():
                years.append(int(suffix))
        if dataset in self.unstamped_datasets and (self.root / dataset).is_dir():
            years.append(self.sample_year)
        return sorted(set(years))

    def available_symbols(self, dataset: str, year: int) -> list[str]:
        directory = self.option_dir(dataset, year)
        if not directory.is_dir():
            return []
        return sorted(p.stem for p in directory.glob("*.parquet"))

    def option_files(self, dataset: str, symbols: list[str], years: list[int]) -> list[Path]:
        files = []
        for year in years:
            directory = self.option_dir(dataset, year)
            for symbol in symbols:
                path = directory / f"{symbol}.parquet"
                if path.exists():
                    files.append(path)
        return files

    @property
    def stock_files(self) -> list[Path]:
        return [
            p
            for p in (self.root / "underlying_history.parquet", self.root / "underlying_2025.parquet")
            if p.exists()
        ]

    @property
    def universe_files(self) -> list[Path]:
        return [
            p
            for p in (self.root / "universe_history.parquet", self.root / "universe.parquet")
            if p.exists()
        ]

    @property
    def sectors_file(self) -> Path:
        return self.root / "sectors.parquet"

    @property
    def yields_file(self) -> Path:
        return self.root / "yields.parquet"

    @property
    def earnings_file(self) -> Path:
        return self.root / "earnings.parquet"

    # Tables written by `data_pipelines.cli vol-risk-model`.
    @property
    def reference_returns_dir(self) -> Path:
        return self.root / "vol_reference_returns"

    @property
    def factor_returns_file(self) -> Path:
        return self.root / "vol_factor_returns.parquet"

    @property
    def factor_loadings_file(self) -> Path:
        return self.root / "vol_factor_loadings.parquet"

    @property
    def factor_covariances_file(self) -> Path:
        return self.root / "vol_factor_covariances.parquet"

    @property
    def idio_vol_file(self) -> Path:
        return self.root / "vol_idio_vol.parquet"


def resolve_years(
    paths: DataPaths, dataset: str, start: dt.date | None, end: dt.date | None
) -> list[int]:
    years = paths.available_years(dataset)
    if start is not None:
        years = [y for y in years if y >= start.year]
    if end is not None:
        years = [y for y in years if y <= end.year]
    return years


def apply_window(frame: pl.LazyFrame, start: dt.date | None, end: dt.date | None) -> pl.LazyFrame:
    if start is not None:
        frame = frame.filter(pl.col("date") >= start)
    if end is not None:
        frame = frame.filter(pl.col("date") <= end)
    return frame


# --------------------------------------------------------------------------
# Options
# --------------------------------------------------------------------------


def scan_raw_options(paths: DataPaths, files: list[Path]) -> pl.LazyFrame:
    """One vendor-schema scan over many symbol-year files, plus a `date`."""
    return pl.scan_parquet(files).with_columns(
        pl.col("underlying_timestamp").dt.date().alias("date")
    )


def scan_options(
    paths: DataPaths,
    symbols: list[str] | None = None,
    start: dt.date | None = None,
    end: dt.date | None = None,
    index: bool = False,
    with_oi: bool = True,
    iv_error_tol: float = 1.0,
    vega_convention: VegaConvention | None = None,
) -> pl.LazyFrame:
    """Canonical option rows, lazily, for `symbols` (default: every root on disk).

    `index=True` reads the index-root directories (SPX, SPXW, XSP, VIX)
    instead of the constituent ones. Open interest is left-joined from its own
    files when `with_oi` is set; a contract without an OI row gets null.
    """
    dataset = "index_greeks" if index else "option_greeks"
    years = resolve_years(paths, dataset, start, end)
    if not years:
        raise FileNotFoundError(f"no {dataset} years on disk for {start}..{end}")
    if symbols is None:
        symbols = sorted({s for y in years for s in paths.available_symbols(dataset, y)})
    files = paths.option_files(dataset, symbols, years)
    if not files:
        raise FileNotFoundError(f"no {dataset} files for {symbols[:5]}... in {years}")
    if vega_convention is None:
        vega_convention = detect_vega_convention(files[0])
    vega_scale = 0.01 if vega_convention is VegaConvention.PER_UNIT_IV else 1.0

    frame = apply_window(scan_raw_options(paths, files), start, end).select(
        pl.col("date"),
        pl.col("symbol").cast(pl.Utf8),
        pl.col("expiration").str.to_date("%Y-%m-%d").alias("expiration"),
        pl.col("strike").cast(pl.Float64),
        pl.when(pl.col("right") == "CALL").then(pl.lit("C")).otherwise(pl.lit("P")).alias("right"),
        pl.col("bid").cast(pl.Float64),
        pl.col("ask").cast(pl.Float64),
        ((pl.col("bid") + pl.col("ask")) / 2.0).cast(pl.Float64).alias("mid"),
        pl.when((pl.col("iv_error").abs() <= iv_error_tol) & (pl.col("implied_vol") > 0))
        .then(pl.col("implied_vol"))
        .otherwise(None)
        .cast(pl.Float64)
        .alias("iv"),
        pl.col("delta").cast(pl.Float64),
        pl.col("gamma").cast(pl.Float64),
        pl.col("theta").cast(pl.Float64),
        (pl.col("vega") * vega_scale).cast(pl.Float64).alias("vega"),
        pl.col("underlying_price").cast(pl.Float64).alias("underlying"),
        pl.col("volume").cast(pl.Int64),
    )

    if with_oi and not index:
        oi = scan_open_interest(paths, symbols, start, end)
        frame = frame.join(oi, on=["date", "symbol", "expiration", "strike", "right"], how="left")
    else:
        frame = frame.with_columns(pl.lit(None, dtype=pl.Int64).alias("oi"))

    return frame.select(list(OPTION_SCHEMA))


def scan_open_interest(
    paths: DataPaths,
    symbols: list[str] | None = None,
    start: dt.date | None = None,
    end: dt.date | None = None,
) -> pl.LazyFrame:
    """`(date, symbol, expiration, strike, right, oi)`.

    The vendor stamps OI pre-open with the position standing after the
    previous close; that is what a trader at today's close knows, so `date`
    joins onto the same session's chain without a shift.
    """
    years = resolve_years(paths, "open_interest", start, end)
    if symbols is None:
        symbols = sorted({s for y in years for s in paths.available_symbols("open_interest", y)})
    files = paths.option_files("open_interest", symbols, years)
    if not files:
        return pl.LazyFrame(
            schema={
                "date": pl.Date,
                "symbol": pl.Utf8,
                "expiration": pl.Date,
                "strike": pl.Float64,
                "right": pl.Utf8,
                "oi": pl.Int64,
            }
        )
    frame = pl.scan_parquet(files).select(
        pl.col("timestamp").dt.date().alias("date"),
        pl.col("symbol").cast(pl.Utf8),
        pl.col("expiration").str.to_date("%Y-%m-%d").alias("expiration"),
        pl.col("strike").cast(pl.Float64),
        pl.when(pl.col("right") == "CALL").then(pl.lit("C")).otherwise(pl.lit("P")).alias("right"),
        pl.col("open_interest").cast(pl.Int64).alias("oi"),
    )
    return apply_window(frame, start, end).unique(
        subset=["date", "symbol", "expiration", "strike", "right"], keep="last"
    )


# --------------------------------------------------------------------------
# Stocks, universe, sectors, rates
# --------------------------------------------------------------------------


def scan_splits(paths: DataPaths) -> pl.LazyFrame:
    """`(date, symbol, split_ratio)` on ex-dates, from the corporate actions table."""
    return (
        pl.scan_parquet(paths.root / "corporate_actions.parquet")
        .filter(pl.col("action") == "split")
        .select(
            pl.col("date").cast(pl.Date),
            pl.col("symbol").cast(pl.Utf8).str.replace_all(r"\.", "").alias("symbol"),
            pl.col("value").cast(pl.Float64).alias("split_ratio"),
        )
        .unique(subset=["date", "symbol"])
    )


def scan_stocks(
    paths: DataPaths, start: dt.date | None = None, end: dt.date | None = None, with_splits: bool = False
) -> pl.LazyFrame:
    """Canonical stock rows. Drops the vendor's zero-priced delisting rows.

    Prices are as traded (unadjusted). `with_splits=True` appends a
    `split_ratio` column (1.0 except on an ex-date), which is what a
    close-to-close return needs: `close * split_ratio / prev_close - 1`.
    """
    frame = (
        pl.scan_parquet(paths.stock_files)
        .select(
            pl.col("created").dt.date().alias("date"),
            pl.col("symbol").cast(pl.Utf8).str.replace_all(r"\.", "").alias("symbol"),
            pl.col("open").cast(pl.Float64),
            pl.col("high").cast(pl.Float64),
            pl.col("low").cast(pl.Float64),
            pl.col("close").cast(pl.Float64),
            pl.col("volume").cast(pl.Int64),
        )
        .filter(pl.col("close") > 0)
    )
    frame = apply_window(frame, start, end).select(list(STOCK_SCHEMA))
    if with_splits:
        frame = frame.join(scan_splits(paths), on=["date", "symbol"], how="left").with_columns(
            pl.col("split_ratio").fill_null(1.0)
        )
    return frame


def scan_universe(
    paths: DataPaths, start: dt.date | None = None, end: dt.date | None = None
) -> pl.LazyFrame:
    """Point-in-time membership as `(date, symbol)` in option-root spelling."""
    frame = pl.scan_parquet(paths.universe_files).select(
        pl.col("date").cast(pl.Date),
        pl.col("ticker")
        .cast(pl.Utf8)
        .replace(TICKER_OVERRIDES)
        .str.replace_all(r"\.", "")
        .alias("symbol"),
    )
    return apply_window(frame, start, end).unique().select(list(UNIVERSE_SCHEMA))


def scan_sectors(paths: DataPaths) -> pl.LazyFrame:
    """`(symbol, sector)`; a static snapshot of today's GICS classification."""
    return pl.scan_parquet(paths.sectors_file).select(
        pl.col("symbol").cast(pl.Utf8), pl.col("sector").cast(pl.Utf8)
    )


def scan_rates(
    paths: DataPaths, start: dt.date | None = None, end: dt.date | None = None, tenor: str = "13w"
) -> pl.LazyFrame:
    """`(date, rate)`: the CBOE 13-week yield index as the risk-free rate."""
    frame = (
        pl.scan_parquet(paths.yields_file)
        .filter(pl.col("tenor") == tenor)
        .select(pl.col("date").cast(pl.Date), pl.col("yield").cast(pl.Float64).alias("rate"))
    )
    return apply_window(frame, start, end)


def scan_earnings(paths: DataPaths) -> pl.LazyFrame:
    """`(symbol, date, session)`; announcement dates for the earnings hook."""
    return pl.scan_parquet(paths.earnings_file).select(
        pl.col("symbol").cast(pl.Utf8), pl.col("date").cast(pl.Date), pl.col("session").cast(pl.Utf8)
    )


# --------------------------------------------------------------------------
# Vega convention
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class VegaCheck:
    convention: VegaConvention
    rows: int
    share_per_unit: float
    share_per_point: float
    median_ratio: float
    theta_per_day_share: float


@lru_cache(maxsize=32)
def detect_vega_convention(sample_file: Path | str, rate: float = 0.04, rows: int = 400) -> VegaConvention:
    """Which vega scaling the file uses, by repricing with Black-Scholes.

    For each sampled contract, price at IV and at IV + 0.01. If the price
    change matches `vega * 0.01` the vendor quotes vega per 1.00 of IV; if it
    matches `vega` it quotes per vol point. The winner needs a two-thirds
    majority, otherwise this raises rather than guessing. Logged at INFO.
    """
    check = run_vega_check(Path(sample_file), rate=rate, rows=rows)
    log.info(
        "vega convention for %s: %s (per-unit share %.2f, per-point share %.2f, "
        "median dP/vega ratio %.4f, theta/day share %.2f)",
        sample_file,
        check.convention.value,
        check.share_per_unit,
        check.share_per_point,
        check.median_ratio,
        check.theta_per_day_share,
    )
    return check.convention


def run_vega_check(sample_file: Path, rate: float = 0.04, rows: int = 400) -> VegaCheck:
    frame = (
        pl.scan_parquet(sample_file)
        .with_columns(pl.col("underlying_timestamp").dt.date().alias("date"))
        .with_columns(
            (pl.col("expiration").str.to_date("%Y-%m-%d") - pl.col("date")).dt.total_days().alias("dte")
        )
        .filter(
            pl.col("bid") > 0,
            pl.col("iv_error").abs() <= 0.01,
            pl.col("implied_vol").is_between(0.05, 2.0),
            pl.col("dte").is_between(10, 365),
            pl.col("delta").abs().is_between(0.2, 0.8),
            pl.col("vega") > 0,
        )
        .head(rows)
        .collect()
    )
    if frame.height < 20:
        raise ValueError(f"too few clean rows in {sample_file} to check the vega convention")

    spot = frame["underlying_price"].to_numpy()
    strike = frame["strike"].to_numpy()
    tau = frame["dte"].to_numpy() / bs.DAYS_PER_YEAR
    sigma = frame["implied_vol"].to_numpy()
    is_call = (frame["right"] == "CALL").to_numpy()
    vendor_vega = frame["vega"].to_numpy()
    vendor_theta = frame["theta"].to_numpy()

    base = bs.price(spot, strike, tau, rate, sigma, is_call)
    bumped = bs.price(spot, strike, tau, rate, sigma + 0.01, is_call)
    price_change = bumped - base

    error_per_unit = np.abs(price_change - vendor_vega * 0.01)
    error_per_point = np.abs(price_change - vendor_vega)
    per_unit_wins = error_per_unit < error_per_point
    share_per_unit = float(per_unit_wins.mean())
    share_per_point = 1.0 - share_per_unit
    median_ratio = float(np.median(price_change / vendor_vega))

    model_theta = bs.theta(spot, strike, tau, rate, sigma, is_call)
    theta_per_day_share = float(
        (np.abs(vendor_theta - model_theta) < np.abs(vendor_theta - model_theta * bs.DAYS_PER_YEAR)).mean()
    )

    if share_per_unit >= 2 / 3:
        convention = VegaConvention.PER_UNIT_IV
    elif share_per_point >= 2 / 3:
        convention = VegaConvention.PER_VOL_POINT
    else:
        raise ValueError(
            f"vega convention ambiguous in {sample_file}: per-unit share {share_per_unit:.2f}"
        )
    return VegaCheck(
        convention=convention,
        rows=frame.height,
        share_per_unit=share_per_unit,
        share_per_point=share_per_point,
        median_ratio=median_ratio,
        theta_per_day_share=theta_per_day_share,
    )


# --------------------------------------------------------------------------
# Risk model tables (written by data_pipelines.cli vol-risk-model)
# --------------------------------------------------------------------------


def require_table(path: Path) -> Path:
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Create it with:\n  uv run python -m data_pipelines.cli vol-risk-model")
    return path


def scan_reference_returns(
    paths: DataPaths,
    symbols: list[str] | None = None,
    start: dt.date | None = None,
    end: dt.date | None = None,
) -> pl.LazyFrame:
    """Per-unit-vega reference straddle P&L, one file per symbol (SPX included)."""
    directory = require_table(paths.reference_returns_dir)
    if symbols is None:
        files = sorted(directory.glob("*.parquet"))
    else:
        files = [directory / f"{s}.parquet" for s in symbols if (directory / f"{s}.parquet").exists()]
    if not files:
        raise FileNotFoundError(f"no reference returns for {symbols} in {directory}")
    return apply_window(pl.scan_parquet(files), start, end)


def scan_factor_returns(paths: DataPaths, start: dt.date | None = None, end: dt.date | None = None) -> pl.LazyFrame:
    """`(date, factor, ret)`."""
    return apply_window(pl.scan_parquet(require_table(paths.factor_returns_file)), start, end)


def scan_factor_loadings(paths: DataPaths, start: dt.date | None = None, end: dt.date | None = None) -> pl.LazyFrame:
    """`(date, symbol, factor, loading)`."""
    return apply_window(pl.scan_parquet(require_table(paths.factor_loadings_file)), start, end)


def scan_factor_covariances(paths: DataPaths, start: dt.date | None = None, end: dt.date | None = None) -> pl.LazyFrame:
    """`(date, factor_1, factor_2, covariance)`."""
    return apply_window(pl.scan_parquet(require_table(paths.factor_covariances_file)), start, end)


def scan_idio_vol(paths: DataPaths, start: dt.date | None = None, end: dt.date | None = None) -> pl.LazyFrame:
    """`(date, symbol, idio_vol)`."""
    return apply_window(pl.scan_parquet(require_table(paths.idio_vol_file)), start, end)
