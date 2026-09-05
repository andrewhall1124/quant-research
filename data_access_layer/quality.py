"""Which symbol-years are the company the universe says they are.

`data_pipelines.symbology` writes the verdict; this module reads it and turns
it into the three things a study needs: the table itself, the set of pairs to
refuse, and the guard the option loaders call before handing back a chain.
"""

import polars as pl

from data_access_layer import paths
from data_access_layer.errors import UntrustedSymbolYear
from data_access_layer.filters import only_symbols, require


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


