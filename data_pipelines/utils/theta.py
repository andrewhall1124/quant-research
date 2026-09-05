"""The shared ThetaData session, and running a pull over many symbols."""

import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, TypeVar

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


def with_retries(fetch: Callable[[str], T], symbol: str, attempts: int = 8) -> T:
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
                # Every worker sees the dead session at once and would burn its
                # attempts in lockstep on a channel that needs time to come
                # back. Wait longer than the ordinary backoff, and jitter it so
                # the workers do not retry as a thundering herd.
                reset_client()
                time.sleep(min(60, 5 * 2**attempt) * (0.5 + random.random()))
                continue
            time.sleep(2**attempt)
    raise RuntimeError("unreachable")


def fetch_many(symbols: list[str], fetch: Callable[[str], T], workers: int) -> list[T]:
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
