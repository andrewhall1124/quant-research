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


# Statuses `symbology_check.parquet` can carry, worst first. `wrong_instrument`
# means the stored chain belongs to a different company than the universe names
# — a rename the vendor answered under the modern ticker. `suspect` landed
# between the clean and contaminated populations. `thin_overlap` means Yahoo
# could not arbitrate at all, which is unverified rather than wrong.
UNTRUSTED_STATUSES = ("wrong_instrument", "suspect")

# Symbol-years the check condemns where the *reference* is wrong, not the data.
# This is where human judgement lives: the store stays the raw vendor record and
# the verdict is overridden here, so nothing has to be re-pulled to revise it.
#
# COL 2017 — ThetaData serves Rockwell Collins at $89-136, which is exactly the
#   company the 2017 universe names; it was acquired in 2018 and Yahoo's modern
#   COL is an unrelated shell trading at $0.06-0.12. The check compares against
#   Yahoo, so it condemns the right data for having a wrong yardstick.
# DD 2017 — DowDuPont. Yahoo back-adjusts the 2019 three-way split into its
#   pre-merger closes and ThetaData does not, which is a restructuring artifact
#   rather than a different company; the median return difference is 0.0014,
#   just over the 0.001 line.
TRUSTED_OVERRIDES = {
    (2017, "COL"): "Rockwell Collins; Yahoo's modern COL is an unrelated shell",
    (2017, "DD"): "DowDuPont restructuring, not a different company",
}


class UntrustedSymbolYear(ValueError):
    """Raised when a caller asks for a symbol-year the symbology check condemns."""


def load_symbology_check(
    year: int | list[int] | None = None,
    status: str | list[str] | None = None,
) -> pl.DataFrame:
    """Per-symbol-year verdict on whether a stored chain is the right company.

    Written by `data_pipelines.symbology`, which compares the `underlying_price`
    on every stored greeks row to Yahoo's close for the same name — on returns,
    not levels, so splits cannot fool it.
    """
    frame = pl.scan_parquet(require(paths.SYMBOLOGY_CHECK, "data_pipelines.symbology --years 2025"))
    if year is not None:
        wanted = [year] if isinstance(year, int) else list(year)
        frame = frame.filter(pl.col("year").is_in(wanted))
    if status is not None:
        wanted = [status] if isinstance(status, str) else list(status)
        frame = frame.filter(pl.col("status").is_in(wanted))
    return frame.sort("year", "symbol").collect()


def untrusted_symbol_years(
    statuses: tuple[str, ...] = UNTRUSTED_STATUSES,
) -> set[tuple[int, str]]:
    """The (year, symbol) pairs a study should not read.

    Empty when the check has never been run, which is deliberate: a missing
    verdict must not silently empty a study. Run `data_pipelines.symbology`.
    """
    if not paths.SYMBOLOGY_CHECK.exists():
        return set()
    check_df = load_symbology_check(status=list(statuses))
    flagged = set(zip(check_df["year"].to_list(), check_df["symbol"].to_list()))
    return flagged - set(TRUSTED_OVERRIDES)


def corporate_action_symbol_years() -> pl.DataFrame:
    """Symbol-years holding a day the vendors disagree about by more than 5%.

    An unadjusted corporate action — most often a spinoff, which
    `corporate_actions.py` does not pull. The symbol is right; the return on
    that one day is not. See `data_store/README.md`.
    """
    if not paths.SYMBOLOGY_CHECK.exists():
        return pl.DataFrame(schema={"year": pl.Int32, "symbol": pl.String, "action_gap_days": pl.UInt32})
    return (
        load_symbology_check()
        .filter(pl.col("action_gap_days") > 0)
        .select("year", "symbol", "action_gap_days")
    )


def check_trusted(symbol: str, years: list[int], dataset: str) -> None:
    """Refuse a symbol-year the symbology check condemns.

    Raising rather than silently dropping: a study that asks for META in 2021
    and gets a $15 stock is a wrong answer, and a study that asks and gets
    nothing back without being told is a different wrong answer.
    """
    untrusted = untrusted_symbol_years()
    hits = sorted(year for year in years if (year, symbol.upper()) in untrusted)
    if hits:
        raise UntrustedSymbolYear(
            f"{symbol.upper()} is not the company the universe names in {hits}"
            f" — see symbology_check.parquet. The raw {dataset} is still on disk;"
            f" pass trusted_only=False to read it anyway."
        )


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


def load_universe(
    as_of: date | None = None, with_history: bool = False
) -> pl.DataFrame:
    """Point-in-time S&P 500 membership, one row per (date, ticker).

    `with_history` prepends `universe_history.parquet`, the reconstruction for
    the backfill years. It is a separate file because membership that far back
    is walked further through Wikipedia's changes table and carries more
    accumulated error than the 2025 sample.
    """
    frame = pl.scan_parquet(require(paths.UNIVERSE, "data_pipelines.universe"))
    if with_history:
        history_path = require(
            paths.UNIVERSE_HISTORY, "data_pipelines.universe --history"
        )
        frame = pl.concat(
            [pl.scan_parquet(history_path), frame], how="vertical_relaxed"
        ).unique(["date", "ticker"])
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
    with_history: bool = False,
) -> pl.DataFrame:
    """EOD stock prices. The raw `created` timestamp becomes a plain `date`.

    `drop_zero_prices` removes the final row ThetaData emits for a delisted
    name, which carries open = high = low = close = 0 (sometimes with real
    volume attached) rather than being absent. Left in, it books a -100% day.

    `with_actions` adds `split_ratio` and `dividend` for that date from the
    corporate-actions table, so returns can be adjusted at the point of use.

    `with_history` prepends `underlying_history.parquet` (2023-06 to 2024-12)
    and, when `in_universe` is also set, widens the membership table to match
    so the extra years are not filtered straight back out. It
    which exists so a volatility model can burn in *before* the option sample
    starts instead of eating the first months of it. It is off by default so
    that every study written against the 2025 panel keeps loading exactly what
    it always did.

    `in_universe` keeps only (symbol, date) pairs that were actually in the
    index that day. Besides being what a universe-based study wants, it drops
    the rows ThetaData returns for a symbol *before* its listing: SOLS has four
    Jan-Apr 2025 rows priced at $0.0001, with volume, months before it began
    trading on 2025-10-30.
    """
    sources = [require(paths.UNDERLYING, "data_pipelines.underlying")]
    if with_history:
        sources.insert(
            0,
            require(
                paths.UNDERLYING_HISTORY,
                "data_pipelines.underlying --start 2023-06-01 --end 2024-05-30"
                " --output data_store/underlying_history.parquet",
            ),
        )
    frame = (
        pl.scan_parquet(sources)
        .with_columns(pl.col("created").dt.date().alias("date"))
        .select("date", "symbol", "open", "high", "low", "close", "volume", "bid", "ask")
    )
    if drop_zero_prices:
        frame = frame.filter(pl.col("close") > 0)
    if in_universe:
        # universe.parquet is spelled in Wikipedia tickers; underlying.parquet
        # in ThetaData's, so the membership table has to be mapped across.
        #
        # Membership follows the price window: asking for history and then
        # semi-joining against 2025-only membership silently drops every
        # pre-2025 row, which is a filter that looks like a data gap.
        members = (
            load_universe(with_history=with_history)
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


def load_symbology_check(
    years: int | list[int] | None = None,
    status: str | list[str] | None = None,
) -> pl.DataFrame:
    """Per-symbol, per-year verdict on whether an option root is the right company.

    See `data_pipelines/symbology.py`. Statuses are:

    * `ok` — the option store's `underlying_price` returns match Yahoo's for
      that ticker, so it is the company the universe claims.
    * `thin_overlap` — fewer than 20 days of Yahoo overlap, so the check could
      not run. **Unverified, not wrong.** It falls disproportionately on names
      that were later delisted or acquired, because Yahoo stops serving them,
      which makes excluding it a survivorship filter rather than a quality one.
    * `wrong_instrument` — returns do not correlate; a different company's
      chain filed under this ticker. Genuinely unusable.
    """
    frame = pl.scan_parquet(require(paths.SYMBOLOGY_CHECK, "data_pipelines.symbology"))
    if years is not None:
        wanted = [years] if isinstance(years, int) else list(years)
        frame = frame.filter(pl.col("year").is_in(wanted))
    return only_symbols(frame, status, "status").sort("year", "symbol").collect()


def usable_symbol_years(years: int | list[int] | None = None) -> pl.DataFrame:
    """(symbol, year) pairs whose option data is not known to be the wrong company.

    The survivorship-safe replacement for `trusted_symbols` on a multi-year
    sample. `trusted_symbols` reads `ticker_check`, which was built against the
    2025 universe, so applying it to earlier years silently drops the names that
    did not survive to 2025 — 84 of 511 symbols in 2021, and the bias grows the
    further back the sample reaches.

    Only `wrong_instrument` is excluded here. `thin_overlap` is kept, because it
    means the check could not run rather than that it failed, and the names it
    covers are precisely the ones a survivorship filter would remove. The cost
    of keeping them is that their split adjustments are unverified — Yahoo has
    no history to check against — so a split in one of those names is not
    caught. Splits are rare enough that this is the better trade, but it is a
    trade.
    """
    checks_df = load_symbology_check(years)
    return (
        checks_df.filter(pl.col("status") != "wrong_instrument")
        .select("symbol", "year")
        .unique()
    )


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


def load_yields(
    tenors: str | list[str] | None = None,
    start: date | None = None,
    end: date | None = None,
) -> pl.DataFrame:
    """CBOE treasury yield indices (13w, 5y, 10y, 30y) as decimal yields."""
    frame = pl.scan_parquet(require(paths.YIELDS, "data_pipelines.reference"))
    return in_window(only_symbols(frame, tenors, "tenor"), start, end).sort("date", "tenor").collect()


def load_rates(start: date | None = None, end: date | None = None) -> pl.DataFrame:
    """Overnight SOFR as a decimal rate. Tier-capped at 2024; see `load_yields`
    for a curve that reaches the whole option history."""
    frame = pl.scan_parquet(require(paths.RATES, "data_pipelines.reference"))
    return in_window(frame, start, end).sort("date", "symbol").collect()


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


def load_universe(
    as_of: date | None = None, with_history: bool = False
) -> pl.DataFrame:
    """Point-in-time S&P 500 membership, one row per (date, ticker).

    `with_history` prepends `universe_history.parquet`, the reconstruction for
    the backfill years. It is a separate file because membership that far back
    is walked further through Wikipedia's changes table and carries more
    accumulated error than the 2025 sample.
    """
    frame = pl.scan_parquet(require(paths.UNIVERSE, "data_pipelines.universe"))
    if with_history:
        history_path = require(
            paths.UNIVERSE_HISTORY, "data_pipelines.universe --history"
        )
        frame = pl.concat(
            [pl.scan_parquet(history_path), frame], how="vertical_relaxed"
        ).unique(["date", "ticker"])
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
    with_history: bool = False,
) -> pl.DataFrame:
    """EOD stock prices. The raw `created` timestamp becomes a plain `date`.

    `drop_zero_prices` removes the final row ThetaData emits for a delisted
    name, which carries open = high = low = close = 0 (sometimes with real
    volume attached) rather than being absent. Left in, it books a -100% day.

    `with_actions` adds `split_ratio` and `dividend` for that date from the
    corporate-actions table, so returns can be adjusted at the point of use.

    `with_history` prepends `underlying_history.parquet` (2023-06 to 2024-12)
    and, when `in_universe` is also set, widens the membership table to match
    so the extra years are not filtered straight back out. It
    which exists so a volatility model can burn in *before* the option sample
    starts instead of eating the first months of it. It is off by default so
    that every study written against the 2025 panel keeps loading exactly what
    it always did.

    `in_universe` keeps only (symbol, date) pairs that were actually in the
    index that day. Besides being what a universe-based study wants, it drops
    the rows ThetaData returns for a symbol *before* its listing: SOLS has four
    Jan-Apr 2025 rows priced at $0.0001, with volume, months before it began
    trading on 2025-10-30.
    """
    sources = [require(paths.UNDERLYING, "data_pipelines.underlying")]
    if with_history:
        sources.insert(
            0,
            require(
                paths.UNDERLYING_HISTORY,
                "data_pipelines.underlying --start 2023-06-01 --end 2024-05-30"
                " --output data_store/underlying_history.parquet",
            ),
        )
    frame = (
        pl.scan_parquet(sources)
        .with_columns(pl.col("created").dt.date().alias("date"))
        .select("date", "symbol", "open", "high", "low", "close", "volume", "bid", "ask")
    )
    if drop_zero_prices:
        frame = frame.filter(pl.col("close") > 0)
    if in_universe:
        # universe.parquet is spelled in Wikipedia tickers; underlying.parquet
        # in ThetaData's, so the membership table has to be mapped across.
        #
        # Membership follows the price window: asking for history and then
        # semi-joining against 2025-only membership silently drops every
        # pre-2025 row, which is a filter that looks like a data gap.
        members = (
            load_universe(with_history=with_history)
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


def load_symbology_check(
    years: int | list[int] | None = None,
    status: str | list[str] | None = None,
) -> pl.DataFrame:
    """Per-symbol, per-year verdict on whether an option root is the right company.

    See `data_pipelines/symbology.py`. Statuses are:

    * `ok` — the option store's `underlying_price` returns match Yahoo's for
      that ticker, so it is the company the universe claims.
    * `thin_overlap` — fewer than 20 days of Yahoo overlap, so the check could
      not run. **Unverified, not wrong.** It falls disproportionately on names
      that were later delisted or acquired, because Yahoo stops serving them,
      which makes excluding it a survivorship filter rather than a quality one.
    * `wrong_instrument` — returns do not correlate; a different company's
      chain filed under this ticker. Genuinely unusable.
    """
    frame = pl.scan_parquet(require(paths.SYMBOLOGY_CHECK, "data_pipelines.symbology"))
    if years is not None:
        wanted = [years] if isinstance(years, int) else list(years)
        frame = frame.filter(pl.col("year").is_in(wanted))
    return only_symbols(frame, status, "status").sort("year", "symbol").collect()


def usable_symbol_years(years: int | list[int] | None = None) -> pl.DataFrame:
    """(symbol, year) pairs whose option data is not known to be the wrong company.

    The survivorship-safe replacement for `trusted_symbols` on a multi-year
    sample. `trusted_symbols` reads `ticker_check`, which was built against the
    2025 universe, so applying it to earlier years silently drops the names that
    did not survive to 2025 — 84 of 511 symbols in 2021, and the bias grows the
    further back the sample reaches.

    Only `wrong_instrument` is excluded here. `thin_overlap` is kept, because it
    means the check could not run rather than that it failed, and the names it
    covers are precisely the ones a survivorship filter would remove. The cost
    of keeping them is that their split adjustments are unverified — Yahoo has
    no history to check against — so a split in one of those names is not
    caught. Splits are rare enough that this is the better trade, but it is a
    trade.
    """
    checks_df = load_symbology_check(years)
    return (
        checks_df.filter(pl.col("status") != "wrong_instrument")
        .select("symbol", "year")
        .unique()
    )


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
