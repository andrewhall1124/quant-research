"""Everything the pipelines share: the vendor sessions, the spellings, the calendar.

    from data_pipelines.utils import fetch_many, make_client, universe_symbols

Nothing in here pulls a dataset of its own. `symbols` reads membership through
`data_access_layer`, which is the only way a pipeline is allowed to read what
another pipeline wrote — no module here or in a `*_pipeline.py` opens a path
under `data_store/` for reading.

* `theta`            — the one shared ThetaClient, retries, the worker pool
* `symbols`          — ticker spellings per vendor, and who was a member when
* `trading_calendar` — exchange sessions, and splitting a window into requests
* `yahoo`            — the batched close/split/dividend download
* `store`            — atomic parquet writes
"""

from data_pipelines.utils.store import write_atomic
from data_pipelines.utils.symbols import (
    TICKER_OVERRIDES,
    UNAVAILABLE,
    normalize_ticker,
    symbol_map,
    universe_symbols,
    universe_tickers,
)
from data_pipelines.utils.theta import (
    fetch_many,
    make_client,
    reset_client,
    with_retries,
)
from data_pipelines.utils.trading_calendar import (
    MAX_REQUEST_DAYS,
    date_chunks,
    trading_sessions,
)
from data_pipelines.utils.yahoo import fetch_yahoo

__all__ = [
    # theta
    "fetch_many",
    "make_client",
    "reset_client",
    "with_retries",
    # symbols
    "TICKER_OVERRIDES",
    "UNAVAILABLE",
    "normalize_ticker",
    "symbol_map",
    "universe_symbols",
    "universe_tickers",
    # calendar
    "MAX_REQUEST_DAYS",
    "date_chunks",
    "trading_sessions",
    # yahoo
    "fetch_yahoo",
    # store
    "write_atomic",
]
