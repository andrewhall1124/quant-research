"""Shared helpers for the ThetaData pulls."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, TypeVar

import polars as pl
from thetadata import ThetaClient

T = TypeVar("T")

client_lock = threading.Lock()
shared_client: ThetaClient | None = None


def make_client() -> ThetaClient:
    """A single process-wide ThetaClient.

    ThetaData issues one session per account, so constructing a second client
    invalidates the first ("Invalid session ID"). Every thread shares this one
    and relies on the gRPC channel being thread-safe.
    """
    global shared_client
    with client_lock:
        if shared_client is None:
            shared_client = ThetaClient(dataframe_type="polars")
    return shared_client


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
# here. `corporate_actions.py` catches those by comparing closes instead.
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


def reset_client() -> None:
    """Drop the shared client so the next caller builds a fresh session.

    A multi-hour backfill outlives a ThetaData session: the server eventually
    answers UNAUTHENTICATED, and every later request on that channel fails the
    same way. Reconnecting is the only recovery, so `with_retries` calls this
    before retrying that one status.
    """
    global shared_client
    with client_lock:
        shared_client = None


def load_universe_tickers(
    universe_path: str, asset: str, limit: int | None = None, year: int | None = None
) -> list[str]:
    """Symbols in a universe file, spelled the way `asset` expects.

    `year` narrows a multi-year universe (`universe_history.parquet`) to the
    names that were actually members that year. Without it a backfill would
    request all 691 tickers the 2016-2024 window ever held on every one of its
    years, a third of them not listed at the time.
    """
    universe_df = pl.read_parquet(universe_path)
    if year is not None and "year" in universe_df.columns:
        universe_df = universe_df.filter(pl.col("year") == year)
    tickers = (
        universe_df
        .select("ticker")
        .unique()
        .sort("ticker")["ticker"]
        .to_list()
    )
    tickers = sorted(
        {
            normalize_ticker(ticker, asset)
            for ticker in tickers
            if ticker not in UNAVAILABLE[asset]
        }
    )
    return tickers[:limit] if limit else tickers


def with_retries(
    fetch: Callable[[str], T], symbol: str, attempts: int = 5
) -> T:
    """Retry with exponential backoff.

    The free tier caps concurrent requests and answers overflow with
    RESOURCE_EXHAUSTED; backing off is enough to get through.
    """
    for attempt in range(attempts):
        try:
            return fetch(symbol)
        except Exception as error:
            message = str(error)
            expired = "UNAUTHENTICATED" in message
            retryable = (
                expired
                or "RESOURCE_EXHAUSTED" in message
                or "UNAVAILABLE" in message
            )
            if not retryable or attempt == attempts - 1:
                raise
            if expired:
                reset_client()
            time.sleep(2**attempt)
    raise RuntimeError("unreachable")


def fetch_many(
    symbols: list[str], fetch: Callable[[str], T], workers: int
) -> list[T]:
    """Run `fetch` over symbols with a thread pool, logging progress and ETA."""
    results = []
    failures = []
    started = time.perf_counter()

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(with_retries, fetch, symbol): symbol for symbol in symbols
        }
        for done, future in enumerate(as_completed(futures), start=1):
            symbol = futures[future]
            try:
                results.append(future.result())
            except Exception as error:
                failures.append((symbol, error))

            elapsed = time.perf_counter() - started
            eta = elapsed / done * (len(symbols) - done)
            print(
                f"\r  {done}/{len(symbols)} | {elapsed:6.1f}s elapsed"
                f" | {eta:7.1f}s eta | {len(failures)} failed",
                end="",
                flush=True,
            )

    if failures:
        print(f"\n  failures: {[symbol for symbol, _ in failures][:20]}")
        print(f"  first error: {failures[0][1]}")

    return results
