"""Per-symbol option chains: greeks, implied vol, and open interest.

These are the only loaders that read a year-stamped directory, so they are also
the only ones that resolve years and check the symbology verdict before handing
anything back.
"""

from datetime import date

import polars as pl

from data_access_layer import paths
from data_access_layer.equities import load_underlying
from data_access_layer.errors import MissingDataset
from data_access_layer.filters import in_window
from data_access_layer.quality import check_trusted
from data_access_layer.reference import load_indices

# Index option roots quote against a published index level rather than a stock.
# SPXW is the weekly root on the same underlying as SPX.
INDEX_ROOT_TO_SPOT = {"SPX": "SPX", "SPXW": "SPX", "XSP": "XSP"}


def resolve_option_paths(
    symbol: str,
    dataset: str,
    years: int | list[int] | None,
    pipeline: str,
    resolved_years: list | None = None,
) -> list:
    """The parquet files holding one symbol across the requested years.

    `years` defaults to `paths.SAMPLE_YEAR` at every call site rather than to
    "everything on disk", so landing a backfill year never silently changes
    what a study that asked for no window already loads. Pass `years=None` to
    opt in to the full history.
    """
    if resolved_years is None:
        resolved_years = []
    wanted = paths.available_years(dataset) if years is None else (
        [years] if isinstance(years, int) else list(years)
    )
    found = [
        path
        for path in (
            paths.option_dir(dataset, year) / f"{symbol.upper()}.parquet"
            for year in sorted(set(wanted))
        )
        if path.exists()
    ]
    resolved_years[:] = [
        year
        for year in sorted(set(wanted))
        if (paths.option_dir(dataset, year) / f"{symbol.upper()}.parquet").exists()
    ]
    if not found:
        # Name the years actually asked for, since the usual cause is a year
        # that was never backfilled rather than a symbol that does not exist.
        raise MissingDataset(
            f"no {dataset} on disk for {symbol.upper()} in"
            f" {sorted(set(wanted)) or 'any year'}. Create it with:\n"
            f"  uv run python -m {pipeline}"
        )
    return found


def load_option_greeks(
    symbol: str,
    start: date | None = None,
    end: date | None = None,
    rights: str | list[str] | None = None,
    min_dte: int | None = None,
    max_dte: int | None = None,
    max_moneyness: float | None = None,
    max_iv_error: float | None = None,
    quoted_only: bool = False,
    columns: list[str] | None = None,
    years: int | list[int] | None = paths.SAMPLE_YEAR,
    index: bool = False,
    trusted_only: bool = True,
) -> pl.DataFrame:
    """One symbol's EOD chain with greeks, IV and the underlying price.

    This is the only option loader: the price-only EOD pull it replaced
    returned a strict subset of these columns. Three things to know:

    - The session stamp is `underlying_timestamp`, not `created`. It is the
      stamp on the spot print the greeks were struck against, so it is defined
      even for a contract that never traded; `timestamp` is the contract's own
      last trade. The two agree on the date throughout the 2025 store, checked
      symbol by symbol, but only the former is guaranteed to.
    - `moneyness` needs no join, because `underlying_price` is on the row and
      was struck at the same instant as the option quote.
    - `implied_vol` is only as good as `iv_error`. About 3% of contract-days
      fail to invert and come back pinned near 0.5 with an error of +/-100;
      `max_iv_error` drops them.

    `quoted_only` drops contracts with no quote, which is also where
    `implied_vol` comes back as 0.0 rather than null.

    `years` defaults to the 2025 sample; pass `years=None` for every
    backfilled year on disk. Because `underlying_price` rides on the row, this
    loader needs no stock tier at all, and so is usable across the whole
    option history — which the free stock tier, stopping at 2023-06-01, is not.
    """
    dataset = paths.option_dataset_name(index)
    read_years: list[int] = []
    greek_paths = resolve_option_paths(
        symbol, dataset, years,
        f"data_pipelines.option_greeks --symbols {symbol.upper()}"
        + (f" --output-dir {paths.option_dir(dataset, paths.SAMPLE_YEAR)}" if index else ""),
        resolved_years=read_years,
    )
    if trusted_only and not index:
        check_trusted(symbol, read_years, "chain")
    frame = (
        pl.scan_parquet(greek_paths)
        .with_columns(
            pl.col("underlying_timestamp").dt.date().alias("date"),
            pl.col("expiration").str.strptime(pl.Date, "%Y-%m-%d").alias("expiration"),
            ((pl.col("bid") + pl.col("ask")) / 2).alias("mid"),
        )
        .with_columns(
            (pl.col("expiration") - pl.col("date")).dt.total_days().alias("dte"),
            (pl.col("strike") / pl.col("underlying_price") - 1).alias("moneyness"),
        )
    )

    frame = in_window(frame, start, end)
    if rights is not None:
        wanted = [rights] if isinstance(rights, str) else list(rights)
        frame = frame.filter(pl.col("right").is_in([right.upper() for right in wanted]))
    if min_dte is not None:
        frame = frame.filter(pl.col("dte") >= min_dte)
    if max_dte is not None:
        frame = frame.filter(pl.col("dte") <= max_dte)
    if max_moneyness is not None:
        frame = frame.filter(pl.col("moneyness").abs() <= max_moneyness)
    if max_iv_error is not None:
        frame = frame.filter(pl.col("iv_error").abs() <= max_iv_error)
    if quoted_only:
        frame = frame.filter((pl.col("bid") > 0) & (pl.col("implied_vol") > 0))

    frame = frame.sort("date", "expiration", "strike", "right")
    if columns is not None:
        frame = frame.select(columns)
    return frame.collect()


def load_open_interest(
    symbol: str,
    start: date | None = None,
    end: date | None = None,
    rights: str | list[str] | None = None,
    min_open_interest: int | None = None,
    years: int | list[int] | None = paths.SAMPLE_YEAR,
    trusted_only: bool = True,
) -> pl.DataFrame:
    """One symbol's EOD open interest, with a derived `date` and `expiration`.

    Open interest is stamped pre-open (~06:30 ET) and reports the position
    standing after the *previous* close, which is the number a trader forming
    at today's close actually knows. So `date` joins straight onto a chain row
    for the same session with no shift, but the figure is settled and one day
    stale — see `data_store/README.md`.

    `min_open_interest` is the liquidity screen most strategy code wants; it
    drops contracts nobody holds rather than merely ones that did not trade.
    """
    read_years: list[int] = []
    oi_paths = resolve_option_paths(
        symbol, "open_interest", years,
        f"data_pipelines.open_interest --symbols {symbol.upper()}",
        resolved_years=read_years,
    )
    if trusted_only:
        check_trusted(symbol, read_years, "open interest")
    frame = pl.scan_parquet(oi_paths).with_columns(
        pl.col("timestamp").dt.date().alias("date"),
        pl.col("expiration").str.strptime(pl.Date, "%Y-%m-%d").alias("expiration"),
    )

    frame = in_window(frame, start, end)
    if rights is not None:
        wanted = [rights] if isinstance(rights, str) else list(rights)
        frame = frame.filter(pl.col("right").is_in([right.upper() for right in wanted]))
    if min_open_interest is not None:
        frame = frame.filter(pl.col("open_interest") >= min_open_interest)

    return frame.sort("date", "expiration", "strike", "right").collect()


def spot_series(symbol: str, index: bool = False) -> pl.DataFrame:
    """Daily spot for an option root: index level for roots, stock close otherwise."""
    if index:
        underlying_symbol = INDEX_ROOT_TO_SPOT.get(symbol.upper(), symbol.upper())
        levels_df = load_indices(underlying_symbol)
    else:
        levels_df = load_underlying(symbol.upper())
    return levels_df.select("date", pl.col("close").alias("spot"))


