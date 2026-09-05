"""Where every dataset lives on disk.

One source of truth: pipelines write to these paths, loaders read from them.
Nothing else in the repo should hardcode a path under `data_store/`.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_STORE = REPO_ROOT / "data_store"

# Single-file tables. Reference data (indices, yields, rates) is cheap to
# re-pull in full, so it is one unstamped table; the expensive per-symbol
# pulls stay year-stamped.
UNIVERSE = DATA_STORE / "universe.parquet"
UNDERLYING = DATA_STORE / "underlying_2025.parquet"
# Pre-sample stock history, so a model burn-in does not consume formation
# dates in the option window. Kept as its own file rather than merged into
# UNDERLYING so that every existing study loads exactly what it always did.
UNDERLYING_HISTORY = DATA_STORE / "underlying_history.parquet"
INDICES = DATA_STORE / "indices.parquet"
YIELDS = DATA_STORE / "yields.parquet"
RATES = DATA_STORE / "rates.parquet"

# Per-symbol-year verdict on whether a backfilled option root is the company
# the universe says it is. See data_pipelines/symbology.py.
SYMBOLOGY_CHECK = DATA_STORE / "symbology_check.parquet"

# Splits and dividends from Yahoo, plus the per-symbol close-agreement check
# that says which of those names can be trusted.
CORPORATE_ACTIONS = DATA_STORE / "corporate_actions.parquet"

# Earnings announcement dates, so a volatility signal can be separated from an
# earnings-timing signal.
EARNINGS = DATA_STORE / "earnings.parquet"

# Point-in-time membership for the backfill years, kept apart from
# `universe.parquet` so the 2025 sample every existing study loads is exactly
# the file it has always been.
UNIVERSE_HISTORY = DATA_STORE / "universe_history.parquet"

# One parquet per symbol; too large to keep in a single file.
OPEN_INTEREST_DIR = DATA_STORE / "open_interest"
OPTION_GREEKS_DIR = DATA_STORE / "option_greeks"

# The year the store was first built for. Every per-symbol option directory is
# year-stamped so a backfill year can never be confused with — or silently
# skipped because of — the original sample. The two directories that predate
# the stamping convention (`option_greeks`, `open_interest`) keep their bare
# names for 2025 rather than being renamed under running sessions.
SAMPLE_YEAR = 2025

OPTION_DIR_PREFIXES = {
    "open_interest": "open_interest",
    "option_greeks": "option_greeks",
    "index_greeks": "index_greeks",
}
UNSTAMPED_2025_DIRS = {"open_interest", "option_greeks"}


def option_dir(dataset: str, year: int = SAMPLE_YEAR) -> Path:
    """The directory holding one year of one per-symbol option dataset."""
    if dataset not in OPTION_DIR_PREFIXES:
        raise KeyError(
            f"unknown option dataset {dataset!r};"
            f" expected one of {sorted(OPTION_DIR_PREFIXES)}"
        )
    if year == SAMPLE_YEAR and dataset in UNSTAMPED_2025_DIRS:
        return DATA_STORE / OPTION_DIR_PREFIXES[dataset]
    return DATA_STORE / f"{OPTION_DIR_PREFIXES[dataset]}_{year}"


def available_years(dataset: str) -> list[int]:
    """Which years of a per-symbol option dataset are actually on disk."""
    prefix = OPTION_DIR_PREFIXES[dataset]
    years = []
    for candidate in DATA_STORE.glob(f"{prefix}_*"):
        if not candidate.is_dir():
            continue
        suffix = candidate.name[len(prefix) + 1 :]
        # Only a numeric suffix is a year: this must not treat a stray
        # `option_greeks_backup` as 2025's neighbour.
        if suffix.isdigit():
            years.append(int(suffix))
    if dataset in UNSTAMPED_2025_DIRS and (DATA_STORE / prefix).is_dir():
        years.append(SAMPLE_YEAR)
    return sorted(set(years))


DATASETS = {
    "universe": UNIVERSE,
    "universe_history": UNIVERSE_HISTORY,
    "underlying": UNDERLYING,
    "underlying_history": UNDERLYING_HISTORY,
    "indices": INDICES,
    "yields": YIELDS,
    "rates": RATES,
    "corporate_actions": CORPORATE_ACTIONS,
    "symbology_check": SYMBOLOGY_CHECK,
    "earnings": EARNINGS,
    "open_interest": OPEN_INTEREST_DIR,
    "option_greeks": OPTION_GREEKS_DIR,
}


def option_dataset_name(index: bool = False) -> str:
    """The per-symbol chain dataset for constituents, or for index roots.

    Index roots stay in their own directory rather than mixing into the
    constituent chains: they are not universe members, and a caller listing
    `available_option_symbols` wants one or the other, never both.
    """
    return "index_greeks" if index else "option_greeks"


def option_chain_path(
    symbol: str, index: bool = False, year: int = SAMPLE_YEAR
) -> Path:
    """Path to one symbol's chain for one year."""
    directory = option_dir(option_dataset_name(index), year)
    return directory / f"{symbol.upper()}.parquet"


def available_option_symbols(
    index: bool = False, year: int = SAMPLE_YEAR
) -> list[str]:
    directory = option_dir(option_dataset_name(index), year)
    if not directory.exists():
        return []
    return sorted(path.stem for path in directory.glob("*.parquet"))


def describe_store() -> str:
    """Human-readable inventory: what is present, what is missing, how big.

    Lists the year-stamped backfill directories too, which are not in
    `DATASETS` because that maps a name to a single path.
    """
    lines = []
    for dataset in OPTION_DIR_PREFIXES:
        for year in available_years(dataset):
            directory = option_dir(dataset, year)
            files = list(directory.glob("*.parquet"))
            size = sum(file.stat().st_size for file in files)
            lines.append(
                f"{dataset + ' ' + str(year):<20} {len(files):>4} files  {size / 1e9:6.2f} GB"
            )
    for name, path in DATASETS.items():
        if name in OPTION_DIR_PREFIXES:
            continue
        if not path.exists():
            lines.append(f"{name:<15} MISSING  {path}")
        elif path.is_dir():
            files = list(path.glob("*.parquet"))
            size = sum(file.stat().st_size for file in files)
            lines.append(f"{name:<15} {len(files):>4} files  {size / 1e9:6.2f} GB")
        else:
            lines.append(f"{name:<15}    1 file   {path.stat().st_size / 1e6:6.1f} MB")
    return "\n".join(lines)
