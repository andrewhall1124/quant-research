# research/data_quality

Is the ThetaData EOD feed good enough to build on, and what is already wrong
with the 2025 panel on disk? Findings are in **[REPORT.md](REPORT.md)**.

## Run it

```bash
uv run python -m research.data_quality.analysis                     # ~4 min
uv run python -m research.data_quality.analysis --skip-store-sweep  # ~40 s
```

The first run pulls a five-root, two-window sample and caches it under
`sample/` (6.6 MB, gitignored); later runs read the parquet. The store sweep
reads all 1.6 GB of `data_store/options_2025/`, which is the slow part.

Needs `underlying_2025.parquet`, `options_2025/`, `universe.parquet` and
`rates.parquet` (SOFR, for the parity discount factor).

## Files

| File | What it is |
|---|---|
| `analysis.py` | the whole audit: subscription probe, sample pull, every check |
| `results/*.csv` | the numbers behind the report's tables |
| `sample/*.parquet` | cached API sample, not committed |

## What each check is for

- **`probe_subscription`** — the published tier table and the per-endpoint
  badges disagree about greeks and open interest. The server's
  `PERMISSION_DENIED` text settles it.
- **`check_integrity`** — crossed and locked quotes, missing quotes, duplicate
  contract-days, nulls, rows for contracts that already expired.
- **`check_parity`** — put-call parity on ATM pairs with the strike discounted
  at SOFR. The only test that catches a feed stitching a stale option quote
  onto a fresh cash close: a stale leg cannot price back to spot.
- **`check_spreads`** — relative spread and trade frequency near the money, to
  show how fast quote quality decays down the liquidity ladder.
- **`check_underlying_store`** — day-over-day moves past 35%, split-classified
  by whether the price ratio sits on a whole number.
- **`check_chain_store`** — the same row-level checks over all 114 M stored
  contract-days, because a rate of 0 in a 300 k sample is not a rate of 0.
