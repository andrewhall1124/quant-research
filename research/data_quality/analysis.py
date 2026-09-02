"""Data-quality audit of the ThetaData EOD feed and of what is already on disk.

Two halves:

1. A live probe + fresh sample (five roots, two one-month windows) that measures
   what the feed itself looks like — coverage, quote pathologies, put-call
   parity against the cash close.
2. A sweep of the 2025 panel already in `data_store/`, looking for the failures
   that only show up once you hold a year of 500 names: unadjusted splits,
   delisting stubs, reused tickers.

The sample is cached under `sample/` so re-runs do not re-hit the API.
"""

import argparse
import datetime as dt
from pathlib import Path

import polars as pl
from dotenv import load_dotenv

from data_access_layer import paths
from data_pipelines.common import make_client

load_dotenv()

HERE = Path(__file__).parent
SAMPLE_DIR = HERE / "sample"
RESULTS_DIR = HERE / "results"

# One mega cap, one heavily traded name, one sleepy large cap, one mid, one of
# the least liquid chains in the index — quote quality is a liquidity story and
# a single name would hide that.
SYMBOLS = ["AAPL", "NVDA", "JNJ", "MOH", "NWSA"]

# June 2025 is the working window; July 2023 sits three weeks after the free
# tier's first accessible date and five months before the date the docs say
# EOD quote fields may be missing, so it tests both claims at once.
WINDOWS = {
    "2025-06": (dt.date(2025, 6, 2), dt.date(2025, 6, 30)),
    "2023-07": (dt.date(2023, 7, 3), dt.date(2023, 7, 31)),
}

# Endpoints worth knowing the tier for, as (label, callable) built at probe time.
PROBE_DAY = dt.date(2025, 6, 6)
PROBE_EXPIRATION = dt.date(2025, 6, 20)


def save(frame: pl.DataFrame, name: str) -> pl.DataFrame:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    frame.write_csv(RESULTS_DIR / f"{name}.csv")
    return frame


def probe_subscription() -> pl.DataFrame:
    """Ask the server which endpoints this account may actually call.

    The published tier table and the per-endpoint badges disagree; the
    PERMISSION_DENIED text is the only authority that cannot be misread.
    """
    client = make_client()
    probes = {
        "stock_history_eod": lambda: client.stock_history_eod(
            "AAPL", PROBE_DAY, PROBE_DAY
        ),
        "option_history_eod": lambda: client.option_history_eod(
            PROBE_DAY, PROBE_DAY, "AAPL", "*"
        ),
        "option_history_open_interest": lambda: client.option_history_open_interest(
            "AAPL", "*", date=PROBE_DAY
        ),
        "option_history_greeks_eod": lambda: client.option_history_greeks_eod(
            "AAPL", "*", PROBE_DAY, PROBE_DAY
        ),
        "option_history_greeks_implied_volatility": (
            lambda: client.option_history_greeks_implied_volatility(
                "AAPL", PROBE_EXPIRATION, "1h", date=PROBE_DAY
            )
        ),
        "option_history_greeks_first_order": (
            lambda: client.option_history_greeks_first_order(
                "AAPL", PROBE_EXPIRATION, "1h", date=PROBE_DAY
            )
        ),
    }

    rows = []
    for label, call in probes.items():
        try:
            frame = call()
            rows.append({"endpoint": label, "allowed": True, "detail": f"{frame.height} rows"})
        except Exception as error:  # noqa: BLE001 - the message is the result
            text = str(error)
            tier = "unknown"
            for candidate in ("value", "standard", "pro"):
                if f"requiring a {candidate} subscription" in text:
                    tier = candidate.upper()
            rows.append({"endpoint": label, "allowed": False, "detail": f"needs {tier}"})
    return save(pl.DataFrame(rows), "subscription_probe")


def pull_sample() -> None:
    """Fetch the sample once and cache it; later runs read the parquet."""
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    client = make_client()
    for tag, (start, end) in WINDOWS.items():
        chain_path = SAMPLE_DIR / f"chains_{tag}.parquet"
        stock_path = SAMPLE_DIR / f"stocks_{tag}.parquet"
        if chain_path.exists() and stock_path.exists():
            continue
        chains, stocks = [], []
        for symbol in SYMBOLS:
            chains.append(
                client.option_history_eod(start, end, symbol, "*").with_columns(
                    pl.lit(symbol).alias("root")
                )
            )
            stocks.append(
                client.stock_history_eod(symbol, start, end).with_columns(
                    pl.lit(symbol).alias("root")
                )
            )
        pl.concat(chains, how="vertical_relaxed").write_parquet(chain_path)
        pl.concat(stocks, how="vertical_relaxed").write_parquet(stock_path)


def load_sample(tag: str) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Chains and stock EOD for one window, with the derived columns the checks need.

    `created` is the only date the EOD endpoints carry — it is the timestamp of
    ThetaData's 17:15 ET report, so its calendar date is the session date.
    """
    chains_df = (
        pl.read_parquet(SAMPLE_DIR / f"chains_{tag}.parquet")
        .with_columns(
            pl.col("created").dt.date().alias("date"),
            pl.col("expiration").str.to_date(),
        )
        .with_columns(
            (pl.col("expiration") - pl.col("date")).dt.total_days().alias("dte"),
            (pl.col("ask") - pl.col("bid")).alias("spread"),
            ((pl.col("bid") + pl.col("ask")) / 2).alias("mid"),
        )
    )
    stocks_df = (
        pl.read_parquet(SAMPLE_DIR / f"stocks_{tag}.parquet")
        .with_columns(pl.col("created").dt.date().alias("date"))
        .select(
            "root",
            "date",
            pl.col("close").alias("spot"),
            pl.col("bid").alias("spot_bid"),
        )
    )
    return chains_df, stocks_df


def check_integrity(chains_df: pl.DataFrame, tag: str) -> pl.DataFrame:
    """Row-level pathologies in one window's chains."""
    rows = chains_df.height
    counts = chains_df.select(
        (pl.col("bid") > pl.col("ask")).sum().alias("crossed_quote"),
        ((pl.col("bid") == pl.col("ask")) & (pl.col("bid") > 0)).sum().alias("locked_quote"),
        (pl.col("bid") == 0).sum().alias("zero_bid"),
        ((pl.col("bid") == 0) & (pl.col("ask") == 0)).sum().alias("no_quote_at_all"),
        (pl.col("volume") == 0).sum().alias("no_trade"),
        ((pl.col("volume") == 0) & (pl.col("close") == 0)).sum().alias("no_trade_zero_close"),
        ((pl.col("volume") > 0) & (pl.col("close") == 0)).sum().alias("traded_zero_close"),
        (pl.col("dte") < 0).sum().alias("already_expired"),
    )
    frame = pl.DataFrame(
        {
            "window": [tag] * len(counts.columns),
            "check": counts.columns,
            "rows": list(counts.row(0)),
            "share": [value / rows for value in counts.row(0)],
        }
    )
    duplicates = (
        chains_df.group_by("root", "date", "expiration", "strike", "right")
        .len()
        .filter(pl.col("len") > 1)
        .height
    )
    nulls = sum(chains_df.null_count().row(0))
    return pl.concat(
        [
            frame,
            pl.DataFrame(
                {
                    "window": [tag, tag],
                    "check": ["duplicate_contract_days", "null_fields"],
                    "rows": [duplicates, nulls],
                    "share": [duplicates / rows, nulls / rows],
                }
            ),
        ]
    )


def check_parity(chains_df: pl.DataFrame, stocks_df: pl.DataFrame, tag: str) -> pl.DataFrame:
    """Put-call parity on ATM pairs, discounting the strike at SOFR.

    This is the sharpest test of whether the bid and ask on the two legs and the
    stock close were all struck at the same instant. A feed that stitches a
    stale option quote onto a fresh close cannot pass it.
    """
    sofr_df = (
        pl.read_parquet(paths.RATES)
        .filter(pl.col("symbol") == "SOFR")
        .select("date", pl.col("rate").alias("sofr"))
    )
    priced = (
        chains_df.filter((pl.col("bid") > 0) & pl.col("dte").is_between(20, 60))
        .join(stocks_df, on=["root", "date"])
        .join(sofr_df, on="date")
    )
    if priced.is_empty():
        # rates.parquet only reaches back to 2024; without a discount factor the
        # test would just be measuring carry.
        print(f"  parity: skipped for {tag}, no SOFR coverage in that window")
        return pl.DataFrame(
            schema={
                "root": pl.String, "pairs": pl.UInt32, "median_bp": pl.Float64,
                "abs_median_bp": pl.Float64, "abs_p95_bp": pl.Float64, "window": pl.String,
            }
        )
    pairs = (
        priced
        .select("root", "date", "expiration", "strike", "right", "mid", "spot", "dte", "sofr")
        .pivot(
            on="right",
            index=["root", "date", "expiration", "strike", "spot", "dte", "sofr"],
            values="mid",
        )
        .drop_nulls(["CALL", "PUT"])
        .filter((pl.col("strike") / pl.col("spot")).is_between(0.97, 1.03))
        .with_columns(
            (
                pl.col("CALL")
                - pl.col("PUT")
                + pl.col("strike") * (-pl.col("sofr") * pl.col("dte") / 365).exp()
            ).alias("implied_spot")
        )
        .with_columns(
            (1e4 * (pl.col("implied_spot") - pl.col("spot")) / pl.col("spot")).alias("error_bp")
        )
    )
    return (
        pairs.group_by("root")
        .agg(
            pl.len().alias("pairs"),
            pl.col("error_bp").median().alias("median_bp"),
            pl.col("error_bp").abs().median().alias("abs_median_bp"),
            pl.col("error_bp").abs().quantile(0.95).alias("abs_p95_bp"),
        )
        .with_columns(pl.lit(tag).alias("window"))
        .sort("root")
    )


def check_spreads(chains_df: pl.DataFrame, stocks_df: pl.DataFrame, tag: str) -> pl.DataFrame:
    """Relative spread and trade frequency near the money, by root."""
    near = (
        chains_df.filter(pl.col("dte").is_between(20, 45) & (pl.col("bid") > 0))
        .join(stocks_df, on=["root", "date"])
        .filter((pl.col("strike") / pl.col("spot")).is_between(0.95, 1.05))
    )
    return (
        near.group_by("root")
        .agg(
            pl.len().alias("rows"),
            (pl.col("spread") / pl.col("mid")).median().alias("rel_spread_median"),
            (pl.col("spread") / pl.col("mid")).quantile(0.9).alias("rel_spread_p90"),
            pl.col("volume").median().alias("volume_median"),
            (pl.col("volume") == 0).mean().alias("no_trade_share"),
        )
        .with_columns(pl.lit(tag).alias("window"))
        .sort("root")
    )


def check_underlying_store() -> tuple[pl.DataFrame, pl.DataFrame]:
    """Corporate-action damage in the 2025 underlying panel already on disk."""
    prices_df = pl.read_parquet(paths.UNDERLYING).with_columns(
        pl.col("created").dt.date().alias("date")
    )
    jumps = (
        prices_df.sort("symbol", "date")
        .with_columns(
            (pl.col("close") / pl.col("close").shift(1).over("symbol") - 1).alias("return")
        )
        .filter(pl.col("return").abs() > 0.35)
        .select("symbol", "date", "close", "return")
        .with_columns((1 / (1 + pl.col("return"))).alias("ratio"))
        .with_columns(
            # A split shows up as a price ratio sitting on a whole number; a
            # real crash lands anywhere. 3% of the ratio is wide enough to
            # absorb the genuine move on the split day itself and still
            # separate 15:1 from a 93% collapse that never happened.
            (
                (pl.col("ratio") - pl.col("ratio").round()).abs() / pl.col("ratio") < 0.03
            ).alias("split_candidate")
        )
        .sort("return")
    )
    broken = prices_df.filter(
        (pl.col("close") <= 0)
        | (pl.col("low") <= 0)
        | (pl.col("close") > pl.col("high"))
        | (pl.col("close") < pl.col("low"))
    ).select("symbol", "date", "open", "high", "low", "close", "volume")
    return jumps, broken


def check_chain_store() -> pl.DataFrame:
    """Sweep every stored 2025 chain for the same pathologies as the sample."""
    files = sorted(paths.OPTION_GREEKS_DIR.glob("*.parquet"))
    scanned = pl.scan_parquet(files)
    return scanned.select(
        pl.len().alias("rows"),
        (pl.col("bid") > pl.col("ask")).sum().alias("crossed_quote"),
        ((pl.col("bid") == 0) & (pl.col("ask") == 0)).sum().alias("no_quote_at_all"),
        ((pl.col("volume") == 0) & (pl.col("close") == 0)).sum().alias("no_trade_zero_close"),
        ((pl.col("volume") > 0) & (pl.col("close") == 0)).sum().alias("traded_zero_close"),
        (pl.col("close") < 0).sum().alias("negative_close"),
        (pl.col("bid") < 0).sum().alias("negative_bid"),
    ).collect()


def run(skip_store_sweep: bool) -> None:
    print("=== subscription probe " + "=" * 40)
    print(probe_subscription())

    pull_sample()
    integrity, parity, spreads = [], [], []
    for tag in WINDOWS:
        chains_df, stocks_df = load_sample(tag)
        print(f"\n=== {tag}: {chains_df.height:,} contract-days " + "=" * 26)
        integrity.append(check_integrity(chains_df, tag))
        parity.append(check_parity(chains_df, stocks_df, tag))
        spreads.append(check_spreads(chains_df, stocks_df, tag))
        print(integrity[-1])
        print(parity[-1])
        print(spreads[-1])
    save(pl.concat(integrity), "chain_integrity")
    save(pl.concat(parity), "put_call_parity")
    save(pl.concat(spreads), "atm_spreads")

    print("\n=== underlying_2025 corporate actions " + "=" * 25)
    jumps, broken = check_underlying_store()
    save(jumps, "underlying_jumps")
    save(broken, "underlying_broken_rows")
    print(jumps)
    print(broken)

    if not skip_store_sweep:
        print("\n=== full 2025 chain sweep " + "=" * 37)
        sweep = save(check_chain_store(), "chain_store_sweep")
        print(sweep)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--skip-store-sweep",
        action="store_true",
        help="skip the 8.5 GB pass over data_store/option_greeks",
    )
    run(parser.parse_args().skip_store_sweep)
