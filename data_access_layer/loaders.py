"""Read and filter datasets out of `data_store/`.

Every loader returns an eager polars DataFrame with a `date` column of dtype
Date, whatever the raw file happens to carry, and accepts the same
`start` / `end` window. Filtering happens lazily, so asking for one symbol out
of a 1.5 GB chain directory only reads that symbol's row groups.
"""

from datetime import date

import polars as pl

from data_access_layer import paths

# Index option roots quote against a published index level rather than a stock.
# SPXW is the weekly root on the same underlying as SPX.
INDEX_ROOT_TO_SPOT = {"SPX": "SPX", "SPXW": "SPX", "XSP": "XSP"}


class MissingDataset(FileNotFoundError):
    """Raised with the pipeline command that would create the missing file."""


def require(path, pipeline: str):
    if not path.exists():
        raise MissingDataset(
            f"{path} not found. Create it with:\n  uv run python -m {pipeline}"
        )
    return path


def in_window(frame: pl.LazyFrame, start: date | None, end: date | None) -> pl.LazyFrame:
    if start is not None:
        frame = frame.filter(pl.col("date") >= start)
    if end is not None:
        frame = frame.filter(pl.col("date") <= end)
    return frame


def only_symbols(
    frame: pl.LazyFrame, symbols: str | list[str] | None, column: str = "symbol"
) -> pl.LazyFrame:
    if symbols is None:
        return frame
    wanted = [symbols] if isinstance(symbols, str) else list(symbols)
    return frame.filter(pl.col(column).is_in(wanted))


def load_universe(as_of: date | None = None) -> pl.DataFrame:
    """Point-in-time S&P 500 membership, one row per (date, ticker)."""
    frame = pl.scan_parquet(require(paths.UNIVERSE, "data_pipelines.universe"))
    if as_of is not None:
        frame = frame.filter(pl.col("date") == as_of)
    return frame.sort("date", "ticker").collect()


def load_underlying(
    symbols: str | list[str] | None = None,
    start: date | None = None,
    end: date | None = None,
) -> pl.DataFrame:
    """EOD stock prices. The raw `created` timestamp becomes a plain `date`."""
    frame = (
        pl.scan_parquet(require(paths.UNDERLYING, "data_pipelines.underlying"))
        .with_columns(pl.col("created").dt.date().alias("date"))
        .select("date", "symbol", "open", "high", "low", "close", "volume", "bid", "ask")
    )
    return in_window(only_symbols(frame, symbols), start, end).sort("date", "symbol").collect()


def load_indices(
    symbols: str | list[str] | None = None,
    start: date | None = None,
    end: date | None = None,
) -> pl.DataFrame:
    """EOD index levels: SPX, RUT, OEX, XSP and the VIX complex."""
    frame = pl.scan_parquet(require(paths.INDICES, "data_pipelines.reference"))
    return in_window(only_symbols(frame, symbols), start, end).sort("date", "symbol").collect()


def load_index_closes(
    symbols: list[str],
    start: date | None = None,
    end: date | None = None,
) -> pl.DataFrame:
    """Index closes pivoted wide — one column per symbol, one row per date.

    This is the shape most time-series work wants (e.g. SPX beside VIX).
    """
    long_df = load_indices(symbols, start, end)
    return long_df.pivot(on="symbol", index="date", values="close").sort("date")


def load_rates(start: date | None = None, end: date | None = None) -> pl.DataFrame:
    """SOFR overnight, as a decimal rate."""
    frame = pl.scan_parquet(require(paths.RATES, "data_pipelines.reference"))
    return in_window(frame, start, end).sort("date").collect()


def load_yields(
    tenors: str | list[str] | None = None,
    start: date | None = None,
    end: date | None = None,
) -> pl.DataFrame:
    """CBOE treasury yield indices (13w, 5y, 10y, 30y) as decimal yields."""
    frame = pl.scan_parquet(require(paths.YIELDS, "data_pipelines.reference"))
    return in_window(only_symbols(frame, tenors, "tenor"), start, end).sort("date", "tenor").collect()


def load_fred_rates(
    series: str | list[str] | None = None,
    start: date | None = None,
    end: date | None = None,
) -> pl.DataFrame:
    """FRED treasury and SOFR series, as decimal rates."""
    frame = pl.scan_parquet(require(paths.FRED_RATES, "data_pipelines.reference"))
    return in_window(only_symbols(frame, series, "series"), start, end).sort("date", "series").collect()


def load_option_chain(
    symbol: str,
    index: bool = False,
    start: date | None = None,
    end: date | None = None,
    rights: str | list[str] | None = None,
    min_dte: int | None = None,
    max_dte: int | None = None,
    with_spot: bool = False,
    max_moneyness: float | None = None,
    columns: list[str] | None = None,
) -> pl.DataFrame:
    """One symbol's EOD chain, with derived `date`, `dte` and `mid` columns.

    `with_spot` joins the underlying close and adds `moneyness`
    (strike / spot - 1); `max_moneyness` filters on its absolute value and
    implies `with_spot`. Set `index=True` for index roots (SPX, SPXW, XSP).
    """
    path = require(
        paths.option_chain_path(symbol, index),
        f"data_pipelines.options --symbols {symbol.upper()}",
    )
    frame = (
        pl.scan_parquet(path)
        .with_columns(
            pl.col("created").dt.date().alias("date"),
            pl.col("expiration").str.strptime(pl.Date, "%Y-%m-%d").alias("expiration"),
            ((pl.col("bid") + pl.col("ask")) / 2).alias("mid"),
        )
        .with_columns((pl.col("expiration") - pl.col("date")).dt.total_days().alias("dte"))
    )

    frame = in_window(frame, start, end)
    if rights is not None:
        wanted = [rights] if isinstance(rights, str) else list(rights)
        frame = frame.filter(pl.col("right").is_in([right.upper() for right in wanted]))
    if min_dte is not None:
        frame = frame.filter(pl.col("dte") >= min_dte)
    if max_dte is not None:
        frame = frame.filter(pl.col("dte") <= max_dte)

    if with_spot or max_moneyness is not None:
        frame = frame.join(spot_series(symbol, index).lazy(), on="date", how="inner")
        frame = frame.with_columns((pl.col("strike") / pl.col("spot") - 1).alias("moneyness"))
        if max_moneyness is not None:
            frame = frame.filter(pl.col("moneyness").abs() <= max_moneyness)

    if columns is not None:
        frame = frame.select(columns)
    return frame.sort("date", "expiration", "strike", "right").collect()


def spot_series(symbol: str, index: bool = False) -> pl.DataFrame:
    """Daily spot for an option root: index level for roots, stock close otherwise."""
    if index:
        underlying_symbol = INDEX_ROOT_TO_SPOT.get(symbol.upper(), symbol.upper())
        levels_df = load_indices(underlying_symbol)
    else:
        levels_df = load_underlying(symbol.upper())
    return levels_df.select("date", pl.col("close").alias("spot"))


def realized_volatility(
    close: pl.Series | pl.Expr, window: int, annualize: bool = True
) -> pl.Expr:
    """Rolling close-to-close realized vol from a price column.

    Kept here so every notebook computes RV the same way: standard deviation of
    log returns over `window` trading days, scaled by sqrt(252).
    """
    log_return = (pl.col(close) if isinstance(close, str) else close).log().diff()
    vol = log_return.rolling_std(window)
    return vol * (252**0.5) if annualize else vol
