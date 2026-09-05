"""Read side of the repo: one import for every dataset in `data_store/`.

    import data_access_layer as dal

    chain_df = dal.load_option_greeks('AAPL', date(2025, 1, 1), date(2025, 3, 31))
    prices = dal.load_underlying(lazy=True)

Pipelines write, this layer reads. Research code should never touch a parquet
path directly — `paths` is the one place a `data_store/` path is spelled.

Every loader has the same shape and nothing else:

    load_x(start=None, end=None, lazy=False)

It scans, filters to the inclusive `[start, end]` window, and returns a
LazyFrame when `lazy=True` or a collected DataFrame otherwise. No screening, no
joins, no derived columns beyond the `date` the window itself needs. The two
per-symbol option loaders take the symbol first, since it selects the file.

What used to be a loader flag is now a named transform you apply yourself —
`filter_to_universe`, `with_corporate_actions`, `with_earnings_distance`, each
taking and returning whichever of DataFrame or LazyFrame you hand it.

The modules, in dependency order:

* `paths`      — where each dataset lives, and which years are on disk
* `errors`     — `MissingDataset`, `UntrustedSymbolYear`
* `filters`    — `require`, `in_window`, `deliver`: what every loader does
* `quality`    — the symbology verdict: which symbol-years are the right company
* `equities`   — stock prices, corporate actions
* `universe`   — point-in-time index membership
* `events`     — earnings dates, and distance to them
* `reference`  — index levels, yields, rates
* `options`    — per-symbol chains: greeks and open interest
* `transforms` — returns and realized vol, computed one agreed way

Import a name from here, not from the submodule: the split is an implementation
detail and a loader may move between modules.
"""

from data_access_layer.equities import (
    THETA_STOCK_OVERRIDES,
    load_corporate_actions,
    load_underlying,
    with_corporate_actions,
)
from data_access_layer.errors import MissingDataset, UntrustedSymbolYear
from data_access_layer.events import load_earnings, with_earnings_distance
from data_access_layer.filters import deliver, in_window, require
from data_access_layer.options import (
    INDEX_ROOT_TO_SPOT,
    load_open_interest,
    load_option_greeks,
    option_paths,
    spot_series,
)
from data_access_layer.paths import (
    DATASETS,
    DATA_STORE,
    SAMPLE_YEAR,
    available_option_symbols,
    available_years,
    describe_store,
    option_chain_path,
    option_dir,
)
from data_access_layer.quality import (
    TRUSTED_OVERRIDES,
    UNTRUSTED_STATUSES,
    corporate_action_symbol_years,
    load_symbology_check,
    untrusted_symbol_years,
    usable_symbol_years,
)
from data_access_layer.reference import load_indices, load_rates, load_yields
from data_access_layer.transforms import realized_volatility, split_adjusted_return
from data_access_layer.universe import filter_to_universe, load_universe

__all__ = [
    # paths
    "DATASETS",
    "DATA_STORE",
    "SAMPLE_YEAR",
    "available_option_symbols",
    "available_years",
    "describe_store",
    "option_chain_path",
    "option_dir",
    # errors
    "MissingDataset",
    "UntrustedSymbolYear",
    # loader plumbing
    "deliver",
    "in_window",
    "require",
    # loaders
    "load_corporate_actions",
    "load_earnings",
    "load_indices",
    "load_open_interest",
    "load_option_greeks",
    "load_rates",
    "load_symbology_check",
    "load_underlying",
    "load_universe",
    "load_yields",
    "spot_series",
    # panel transforms
    "filter_to_universe",
    "with_corporate_actions",
    "with_earnings_distance",
    # expressions
    "realized_volatility",
    "split_adjusted_return",
    # quality screens
    "TRUSTED_OVERRIDES",
    "UNTRUSTED_STATUSES",
    "corporate_action_symbol_years",
    "untrusted_symbol_years",
    "usable_symbol_years",
    # option file resolution
    "INDEX_ROOT_TO_SPOT",
    "THETA_STOCK_OVERRIDES",
    "option_paths",
]
