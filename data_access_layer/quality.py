"""Which symbol-years are the company the universe says they are.

`data_pipelines.symbology` writes the verdict; this module reads it and turns it
into the two screens a multi-year study applies. The option loaders no longer
apply either one themselves — they are pure reads — so a study that cares must
join against `usable_symbol_years` explicitly.
"""

import polars as pl

from data_access_layer import paths
from data_access_layer.filters import deliver, require

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


def load_symbology_check(lazy: bool = False) -> pl.LazyFrame | pl.DataFrame:
    """Per-symbol, per-year verdict on whether an option root is the right company.

    Keyed on (year, symbol), not date, so it takes no window — it is 4,613 rows.
    See `data_pipelines/symbology.py`. Statuses are:

    * `ok` — the option store's `underlying_price` returns match Yahoo's for
      that ticker, so it is the company the universe claims.
    * `thin_overlap` — fewer than 20 days of Yahoo overlap, so the check could
      not run. **Unverified, not wrong.** It falls disproportionately on names
      that were later delisted or acquired, because Yahoo stops serving them,
      which makes excluding it a survivorship filter rather than a quality one.
    * `suspect` — landed between the clean and contaminated populations.
    * `wrong_instrument` — returns do not correlate; a different company's
      chain filed under this ticker. Genuinely unusable.

    `action_gap_days` counts days the vendors disagree about by more than 5% —
    an unadjusted corporate action, usually a spinoff. The symbol is right; the
    return on that one day is not.
    """
    frame = pl.scan_parquet(
        require(paths.SYMBOLOGY_CHECK, "data_pipelines.symbology")
    ).sort("year", "symbol")
    return deliver(frame, lazy)


def usable_symbol_years(lazy: bool = False) -> pl.LazyFrame | pl.DataFrame:
    """(symbol, year) pairs whose option data is not known to be the wrong company.

    The survivorship-safe screen for a multi-year sample. Only
    `wrong_instrument` is excluded: `thin_overlap` means the check could not run
    rather than that it failed, and the names it covers are precisely the ones a
    survivorship filter would remove. The cost of keeping them is that their
    split adjustments are unverified — Yahoo has no history to check against —
    so a split in one of those names is not caught. Splits are rare enough that
    this is the better trade, but it is a trade.

    Semi-join a panel against it:

        panel_df.join(dal.usable_symbol_years(), on=['symbol', 'year'], how='semi')
    """
    frame = (
        load_symbology_check(lazy=True)
        .filter(pl.col("status") != "wrong_instrument")
        .select("symbol", "year")
        .unique()
    )
    return deliver(frame, lazy)


def untrusted_symbol_years(
    statuses: tuple[str, ...] = UNTRUSTED_STATUSES,
) -> set[tuple[int, str]]:
    """The (year, symbol) pairs a study should not read, as a plain set.

    Empty when the check has never been run, which is deliberate: a missing
    verdict must not silently empty a study. Run `data_pipelines.symbology`.
    """
    if not paths.SYMBOLOGY_CHECK.exists():
        return set()
    check_df = load_symbology_check().filter(pl.col("status").is_in(list(statuses)))
    flagged = set(zip(check_df["year"].to_list(), check_df["symbol"].to_list()))
    return flagged - set(TRUSTED_OVERRIDES)


def corporate_action_symbol_years(lazy: bool = False) -> pl.LazyFrame | pl.DataFrame:
    """Symbol-years holding a day the vendors disagree about by more than 5%.

    A separate warning from the status column: an unadjusted corporate action,
    most often a spinoff, which `corporate_actions.py` does not pull. The symbol
    is right; the return on that one day is not.
    """
    frame = (
        load_symbology_check(lazy=True)
        .filter(pl.col("action_gap_days") > 0)
        .select("year", "symbol", "action_gap_days")
    )
    return deliver(frame, lazy)
