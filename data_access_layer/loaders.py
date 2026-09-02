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

# Mirrors data_pipelines.common.TICKER_OVERRIDES["stock"]. Duplicated rather
# than imported so the access layer does not depend on the pipeline package.
THETA_STOCK_OVERRIDES = {"BNY": "BK"}


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
    drop_zero_prices: bool = True,
    with_actions: bool = False,
    in_universe: bool = False,
) -> pl.DataFrame:
    """EOD stock prices. The raw `created` timestamp becomes a plain `date`.

    `drop_zero_prices` removes the final row ThetaData emits for a delisted
    name, which carries open = high = low = close = 0 (sometimes with real
    volume attached) rather than being absent. Left in, it books a -100% day.

    `with_actions` adds `split_ratio` and `dividend` for that date from the
    corporate-actions table, so returns can be adjusted at the point of use.

    `in_universe` keeps only (symbol, date) pairs that were actually in the
    index that day. Besides being what a universe-based study wants, it drops
    the rows ThetaData returns for a symbol *before* its listing: SOLS has four
    Jan-Apr 2025 rows priced at $0.0001, with volume, months before it began
    trading on 2025-10-30.
    """
    frame = (
        pl.scan_parquet(require(paths.UNDERLYING, "data_pipelines.underlying"))
        .with_columns(pl.col("created").dt.date().alias("date"))
        .select("date", "symbol", "open", "high", "low", "close", "volume", "bid", "ask")
    )
    if drop_zero_prices:
        frame = frame.filter(pl.col("close") > 0)
    if in_universe:
        # universe.parquet is spelled in Wikipedia tickers; underlying.parquet
        # in ThetaData's, so the membership table has to be mapped across.
        members = (
            load_universe()
            .with_columns(
                pl.col("ticker").replace(THETA_STOCK_OVERRIDES).alias("symbol")
            )
            .select("date", "symbol")
            .unique()
        )
        frame = frame.join(members.lazy(), on=["date", "symbol"], how="semi")
    if with_actions:
        actions_df = load_corporate_actions()
        splits_df = (
            actions_df.filter(pl.col("action") == "split")
            .select("symbol", "date", pl.col("value").alias("split_ratio"))
        )
        dividends_df = (
            actions_df.filter(pl.col("action") == "dividend")
            .group_by("symbol", "date")
            .agg(pl.col("value").sum().alias("dividend"))
        )
        frame = (
            frame.join(splits_df.lazy(), on=["symbol", "date"], how="left")
            .join(dividends_df.lazy(), on=["symbol", "date"], how="left")
            .with_columns(
                pl.col("split_ratio").fill_null(1.0),
                pl.col("dividend").fill_null(0.0),
            )
        )
    return in_window(only_symbols(frame, symbols), start, end).sort("date", "symbol").collect()


def load_corporate_actions(
    kind: str | None = None,
    symbols: str | list[str] | None = None,
    start: date | None = None,
    end: date | None = None,
) -> pl.DataFrame:
    """Splits and dividends from Yahoo. `kind` is "split", "dividend" or None.

    Split values are ex-date ratios (ORLY 2025-06-10 is 15.0). Yahoo also
    encodes spinoff adjustment factors here as non-integer "splits" (DD 2.39 on
    the Qnity spinoff), which is what you want for the same reason.
    """
    frame = pl.scan_parquet(
        require(paths.CORPORATE_ACTIONS, "data_pipelines.corporate_actions")
    )
    if kind is not None:
        frame = frame.filter(pl.col("action") == kind)
    return in_window(only_symbols(frame, symbols), start, end).sort("symbol", "date").collect()


def load_ticker_check(status: str | list[str] | None = None) -> pl.DataFrame:
    """Per-symbol agreement between ThetaData and Yahoo closes.

    Status is "ok", "mismatch", "thin_overlap", "yahoo_missing" or
    "theta_missing". Anything but "ok" means the two vendors disagree about
    what that symbol is, or Yahoo has no usable history for it; those names
    should not carry a split adjustment you trust.
    """
    frame = pl.scan_parquet(
        require(paths.TICKER_CHECK, "data_pipelines.corporate_actions")
    )
    return only_symbols(frame, status, "status").sort("status", "symbol").collect()


def load_earnings(
    symbols: str | list[str] | None = None,
    start: date | None = None,
    end: date | None = None,
) -> pl.DataFrame:
    """Earnings announcement dates, one row per (symbol, date).

    `session` is "bmo", "amc" or "unknown". It matters: a before-the-open
    report moves that day's close-to-close return, an after-the-close report
    moves the *next* one.
    """
    frame = pl.scan_parquet(require(paths.EARNINGS, "data_pipelines.earnings"))
    return in_window(only_symbols(frame, symbols), start, end).sort("symbol", "date").collect()


def with_earnings_distance(prices_df: pl.DataFrame) -> pl.DataFrame:
    """Add `days_to_earnings` and `days_since_earnings` to a (symbol, date) panel.

    Both are calendar days, and `days_to_earnings` counts to the next
    announcement on or after the row's date. An implied-vol signal ranked
    cross-sectionally will sort largely on this column unless it is controlled
    for, which is the whole reason the table exists.
    """
    events_df = load_earnings().select(
        "symbol", pl.col("date").alias("earnings_date")
    )
    ordered = prices_df.sort("symbol", "date")
    events = events_df.sort("symbol", "earnings_date")
    forward = ordered.join_asof(
        events, left_on="date", right_on="earnings_date", by="symbol",
        strategy="forward", check_sortedness=False,
    ).select("symbol", "date", pl.col("earnings_date").alias("next_earnings"))
    backward = ordered.join_asof(
        events, left_on="date", right_on="earnings_date", by="symbol",
        strategy="backward", check_sortedness=False,
    ).select("symbol", "date", pl.col("earnings_date").alias("previous_earnings"))
    return (
        prices_df.join(forward, on=["symbol", "date"], how="left")
        .join(backward, on=["symbol", "date"], how="left")
        .with_columns(
            (pl.col("next_earnings") - pl.col("date")).dt.total_days().alias("days_to_earnings"),
            (pl.col("date") - pl.col("previous_earnings")).dt.total_days().alias("days_since_earnings"),
        )
    )


def trusted_symbols() -> list[str]:
    """Symbols whose split adjustment has been verified against a second source."""
    return load_ticker_check("ok")["symbol"].to_list()


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

    # Sort before narrowing: the sort keys are not necessarily in `columns`.
    frame = frame.sort("date", "expiration", "strike", "right")
    if columns is not None:
        frame = frame.select(columns)
    return frame.collect()


def spot_series(symbol: str, index: bool = False) -> pl.DataFrame:
    """Daily spot for an option root: index level for roots, stock close otherwise."""
    if index:
        underlying_symbol = INDEX_ROOT_TO_SPOT.get(symbol.upper(), symbol.upper())
        levels_df = load_indices(underlying_symbol)
    else:
        levels_df = load_underlying(symbol.upper())
    return levels_df.select("date", pl.col("close").alias("spot"))


def split_adjusted_return(
    close: str = "close", split_ratio: str = "split_ratio"
) -> pl.Expr:
    """Close-to-close simple return with the split applied on its ex-date.

    Only a split falling *between* the two closes matters to a return, so this
    needs the ex-date ratio rather than a cumulative back-adjustment factor —
    which also means a split after the sample ends is correctly irrelevant.
    Use over a symbol partition:

        prices_df.with_columns(split_adjusted_return().over("symbol"))
    """
    return (
        pl.col(close) * pl.col(split_ratio) / pl.col(close).shift(1) - 1
    ).alias("return")


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
