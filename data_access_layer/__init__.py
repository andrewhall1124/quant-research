"""Read side of the repo: one import for every dataset in `data_store/`.

    from data_access_layer import load_index_closes, load_option_greeks

Pipelines write, this layer reads. Research code should never touch a parquet
path directly.
"""

from data_access_layer.loaders import (
    MissingDataset,
    load_fred_rates,
    load_index_closes,
    load_indices,
    load_open_interest,
    load_option_greeks,
    load_rates,
    load_corporate_actions,
    load_earnings,
    load_symbology_check,
    load_ticker_check,
    load_underlying,
    split_adjusted_return,
    trusted_symbols,
    usable_symbol_years,
    with_earnings_distance,
    load_universe,
    load_yields,
    realized_volatility,
    spot_series,
)
from data_access_layer.paths import (
    DATA_STORE,
    DATASETS,
    available_option_symbols,
    describe_store,
    option_chain_path,
)

__all__ = [
    "DATASETS",
    "DATA_STORE",
    "MissingDataset",
    "available_option_symbols",
    "describe_store",
    "load_fred_rates",
    "load_index_closes",
    "load_indices",
    "load_open_interest",
    "load_option_greeks",
    "load_rates",
    "load_corporate_actions",
    "load_earnings",
    "load_symbology_check",
    "load_ticker_check",
    "load_underlying",
    "split_adjusted_return",
    "trusted_symbols",
    "usable_symbol_years",
    "with_earnings_distance",
    "load_universe",
    "load_yields",
    "option_chain_path",
    "realized_volatility",
    "spot_series",
]
