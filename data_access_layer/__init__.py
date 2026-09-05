"""Read side of the repo: one import for every dataset in `data_store/`.

    import data_access_layer as dal

    chain_df = dal.load_option_greeks("AAPL", min_dte=20, max_dte=40)
    prices_df = dal.load_underlying(in_universe=True, with_actions=True)

Pipelines write, this layer reads. Research code should never touch a parquet
path directly — `paths` is the one place a `data_store/` path is spelled, and
every loader here goes through it.

The modules, in dependency order:

* `paths`      — where each dataset lives, and which years are on disk
* `errors`     — `MissingDataset`, `UntrustedSymbolYear`
* `filters`    — the `start`/`end`/`symbols` vocabulary every loader shares
* `quality`    — the symbology verdict: which symbol-years are the right company
* `universe`   — point-in-time index membership
* `equities`   — stock prices and corporate actions
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
)
from data_access_layer.errors import MissingDataset, UntrustedSymbolYear
from data_access_layer.events import load_earnings, with_earnings_distance
from data_access_layer.filters import in_window, only_symbols, require
from data_access_layer.options import (
    INDEX_ROOT_TO_SPOT,
    load_open_interest,
    load_option_greeks,
    resolve_option_paths,
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
    check_trusted,
    corporate_action_symbol_years,
    load_symbology_check,
    untrusted_symbol_years,
    usable_symbol_years,
)
from data_access_layer.reference import (
    load_index_closes,
    load_indices,
    load_rates,
    load_yields,
)
from data_access_layer.transforms import realized_volatility, split_adjusted_return
from data_access_layer.universe import load_universe

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
    # filters
    "in_window",
    "only_symbols",
    "require",
    # quality
    "TRUSTED_OVERRIDES",
    "UNTRUSTED_STATUSES",
    "check_trusted",
    "corporate_action_symbol_years",
    "load_symbology_check",
    "untrusted_symbol_years",
    "usable_symbol_years",
    # universe
    "load_universe",
    # equities
    "THETA_STOCK_OVERRIDES",
    "load_corporate_actions",
    "load_underlying",
    # events
    "load_earnings",
    "with_earnings_distance",
    # reference
    "load_index_closes",
    "load_indices",
    "load_rates",
    "load_yields",
    # options
    "INDEX_ROOT_TO_SPOT",
    "load_open_interest",
    "load_option_greeks",
    "resolve_option_paths",
    "spot_series",
]
