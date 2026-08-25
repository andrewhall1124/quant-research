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

# One parquet per symbol; too large to keep in a single file.
OPTIONS_DIR = DATA_STORE / "options_2025"
INDEX_OPTIONS_DIR = DATA_STORE / "index_options_2025"

DATASETS = {
    "universe": UNIVERSE,
    "underlying": UNDERLYING,
    "indices": INDICES,
    "yields": YIELDS,
    "rates": RATES,
    "fred_rates": FRED_RATES,
    "options": OPTIONS_DIR,
    "index_options": INDEX_OPTIONS_DIR,
}


def option_chain_path(symbol: str, index: bool = False) -> Path:
    """Path to one symbol's chain. Index roots live in their own directory."""
    directory = INDEX_OPTIONS_DIR if index else OPTIONS_DIR
    return directory / f"{symbol.upper()}.parquet"


def available_option_symbols(index: bool = False) -> list[str]:
    directory = INDEX_OPTIONS_DIR if index else OPTIONS_DIR
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
