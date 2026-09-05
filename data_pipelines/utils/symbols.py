"""How each vendor spells the universe, and who was in it when.

Membership comes from the data access layer rather than from a parquet path:
`universe.py` writes it, `dal.load_universe` reads it, and every per-symbol
pull iterates what that returns for its own date window.
"""

from datetime import date

import polars as pl

import data_access_layer as dal

# Every vendor spells the universe differently, so the Wikipedia ticker has to
# be mapped per destination. ThetaData stocks keep the dot (BRK.B); ThetaData
# options strip it (BRKB, BFB); Yahoo wants a dash (BRK-B). Confirmed against
# all three.
#
# BNY is mapped for both ThetaData asset classes, and not just for convenience:
# the stock endpoint answers a BNY request with an unrelated ~$10 small-cap
# rather than an error, so leaving it unmapped silently fills the dataset with
# the wrong instrument. Bank of New York Mellon is BK there all year. Yahoo is
# the other way round — it serves the real company under BNY and 404s on BK —
# which is why the override table is keyed by destination rather than shared.
TICKER_OVERRIDES = {
    "stock": {"BNY": "BK"},
    "option": {"BNY": "BK"},
    "yahoo": {},
}

# Present in the Wikipedia universe but absent from ThetaData, so never worth
# requesting. ECHO, MRSH and VMRK are stale entries from the changes table and
# are missing for both asset classes; NVR trades but has no chain in
# ThetaData's option universe.
#
# Nothing is excluded for Yahoo. It answers almost any symbol with *something* —
# VMRK comes back as a price that does not match the name it reports, ECHO as a
# company delisted in 2021 — so the exclusion list cannot be written by hand
# here. `corporate_actions_pipeline.py` catches those by comparing closes.
UNAVAILABLE = {
    "stock": {"ECHO", "MRSH", "VMRK"},
    "option": {"ECHO", "MRSH", "VMRK", "NVR"},
    "yahoo": set(),
}


def normalize_ticker(ticker: str, asset: str) -> str:
    """Map a Wikipedia ticker onto the spelling `asset` expects."""
    ticker = TICKER_OVERRIDES[asset].get(ticker, ticker)
    if asset == "option":
        return ticker.replace(".", "")
    if asset == "yahoo":
        return ticker.replace(".", "-")
    return ticker


def universe_tickers(
    start: date | None = None, end: date | None = None
) -> list[str]:
    """Wikipedia tickers that were index members at some point in the window.

    The window is what keeps a backfill honest: without it a 2019 pull would
    request all 691 tickers the 2017-2025 span ever held, a third of them not
    listed at the time.
    """
    return (
        dal.load_universe(start, end)
        .get_column("ticker")
        .unique()
        .sort()
        .to_list()
    )


def universe_symbols(
    asset: str,
    start: date | None = None,
    end: date | None = None,
    limit: int | None = None,
) -> list[str]:
    """Universe members over the window, spelled the way `asset` expects."""
    symbols = sorted(
        {
            normalize_ticker(ticker, asset)
            for ticker in universe_tickers(start, end)
            if ticker not in UNAVAILABLE[asset]
        }
    )
    return symbols[:limit] if limit else symbols


def symbol_map(
    start: date | None = None, end: date | None = None
) -> pl.DataFrame:
    """Every universe name in all four spellings, one row each.

    Nothing is excluded. A name ThetaData cannot serve still gets a row, so a
    check that reads this records *why* a name is unusable rather than
    silently omitting it.
    """
    tickers = universe_tickers(start, end)
    return pl.DataFrame(
        {
            "ticker": tickers,
            "stock_symbol": [normalize_ticker(t, "stock") for t in tickers],
            "option_symbol": [normalize_ticker(t, "option") for t in tickers],
            "yahoo_symbol": [normalize_ticker(t, "yahoo") for t in tickers],
        }
    )
