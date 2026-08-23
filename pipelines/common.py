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


# ThetaData's stock and option endpoints use different symbologies, so the
# Wikipedia ticker has to be mapped per asset class. Stocks keep the dot
# (BRK.B); options strip it (BRKB, BFB). Confirmed against both endpoints.
#
# BNY is mapped for BOTH asset classes, and not just for convenience: the stock
# endpoint answers a BNY request with an unrelated ~$10 small-cap rather than
# an error, so leaving it unmapped silently fills the dataset with the wrong
# instrument. Bank of New York Mellon is BK there all year.
TICKER_OVERRIDES = {"BNY": "BK"}

# Present in the Wikipedia universe but absent from ThetaData, so never worth
# requesting. ECHO, MRSH and VMRK are stale entries from the changes table and
# are missing for both asset classes; NVR trades but has no chain in
# ThetaData's option universe.
UNAVAILABLE = {
    "stock": {"ECHO", "MRSH", "VMRK"},
    "option": {"ECHO", "MRSH", "VMRK", "NVR"},
}


def normalize_ticker(ticker: str, asset: str) -> str:
    """Map a Wikipedia ticker onto ThetaData's spelling for `asset`."""
    ticker = TICKER_OVERRIDES.get(ticker, ticker)
    return ticker if asset == "stock" else ticker.replace(".", "")


def load_universe_tickers(
    universe_path: str, asset: str, limit: int | None = None
) -> list[str]:
    tickers = (
        pl.read_parquet(universe_path)
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
            retryable = "RESOURCE_EXHAUSTED" in str(error) or "UNAVAILABLE" in str(error)
            if not retryable or attempt == attempts - 1:
                raise
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
