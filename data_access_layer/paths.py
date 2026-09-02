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
INDICES = DATA_STORE / "indices.parquet"
YIELDS = DATA_STORE / "yields.parquet"
RATES = DATA_STORE / "rates.parquet"
FRED_RATES = DATA_STORE / "fred_rates.parquet"

# Splits and dividends from Yahoo, plus the per-symbol close-agreement check
# that says which of those names can be trusted.
CORPORATE_ACTIONS = DATA_STORE / "corporate_actions.parquet"
TICKER_CHECK = DATA_STORE / "ticker_check.parquet"

# Earnings announcement dates, so a volatility signal can be separated from an
# earnings-timing signal.
EARNINGS = DATA_STORE / "earnings.parquet"

# One parquet per symbol; too large to keep in a single file.
OPTIONS_DIR = DATA_STORE / "options_2025"
OPTION_GREEKS_DIR = DATA_STORE / "option_greeks"
INDEX_OPTIONS_DIR = DATA_STORE / "index_options_2025"

DATASETS = {
    "universe": UNIVERSE,
    "underlying": UNDERLYING,
    "indices": INDICES,
    "yields": YIELDS,
    "rates": RATES,
    "fred_rates": FRED_RATES,
    "corporate_actions": CORPORATE_ACTIONS,
    "ticker_check": TICKER_CHECK,
    "earnings": EARNINGS,
    "options": OPTIONS_DIR,
    "option_greeks": OPTION_GREEKS_DIR,
    "index_options": INDEX_OPTIONS_DIR,
}


def option_chain_path(symbol: str, index: bool = False, greeks: bool = False) -> Path:
    """Path to one symbol's chain.

    Index roots live in their own directory, and the greeks pull is a third
    directory rather than extra columns on the first: it is a separate,
    slower endpoint and is not expected to cover the same symbols.
    """
    if greeks:
        return OPTION_GREEKS_DIR / f"{symbol.upper()}.parquet"
    directory = INDEX_OPTIONS_DIR if index else OPTIONS_DIR
    return directory / f"{symbol.upper()}.parquet"


def available_option_symbols(index: bool = False, greeks: bool = False) -> list[str]:
    directory = OPTION_GREEKS_DIR if greeks else (INDEX_OPTIONS_DIR if index else OPTIONS_DIR)
    if not directory.exists():
        return []
    return sorted(path.stem for path in directory.glob("*.parquet"))


def describe_store() -> str:
    """Human-readable inventory: what is present, what is missing, how big."""
    lines = []
    for name, path in DATASETS.items():
        if not path.exists():
            lines.append(f"{name:<15} MISSING  {path}")
        elif path.is_dir():
            files = list(path.glob("*.parquet"))
            size = sum(file.stat().st_size for file in files)
            lines.append(f"{name:<15} {len(files):>4} files  {size / 1e9:6.2f} GB")
        else:
            lines.append(f"{name:<15}    1 file   {path.stat().st_size / 1e6:6.1f} MB")
    return "\n".join(lines)
