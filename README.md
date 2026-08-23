# quant-research

EOD data pulls for the S&P 500 and its options chains, via ThetaData.

## Pipelines

Run from the repo root:

```bash
uv run pipelines/universe.py                  # point-in-time S&P 500 membership
uv run pipelines/underlying.py                # EOD stock prices for that universe
uv run pipelines/options.py                   # EOD option chains for that universe
```

`underlying.py` and `options.py` take `--start`, `--end`, `--workers`, and
`--limit` (sample the first N tickers, for benchmarking).

`universe.py` reconstructs membership by walking Wikipedia's current
constituent list backwards through the changes table. The trading calendar
comes from SPY's EOD history, because ThetaData's calendar endpoint needs a
paid tier.

`options.py` writes one parquet per symbol and skips symbols already on disk,
so it is resumable and never holds a full year of chains in memory.

## ThetaData constraints

Discovered while building this, and they shape the design:

- **One session per account.** Constructing a second `ThetaClient` — even in
  the same process — invalidates the first with `Invalid session ID`. All
  threads share the singleton in `common.py`.
- **Concurrency is capped by tier**, as server-allocated threads: FREE 1,
  VALUE 1, STANDARD 2, PRO 4. On the free tier 2 workers run clean and 4
  return `RESOURCE_EXHAUSTED`, so requests retry with exponential backoff.
- **Flat files cover only the 7 most recent calendar days**, and are Pro-only.
  They are not a backfill path; per-symbol requests are the only option.
- **Free-tier history starts 2023-06-01.** Backfills earlier than that need a
  paid tier regardless of how long you are willing to wait.
- `option_history_eod(..., expiration="*")` returns a symbol's whole chain in
  one request, which is the efficient path available on any tier.

## Measured timings

Full-year 2025, 523 tickers, free tier at 2 workers:

| Pull | Wall time | Rows | Size |
|---|---|---|---|
| Underlying EOD | 14 min (1.61 s/symbol) | 128,542 | 3.8 MB |
| Option chains | ~4-5 hr (32.0 s/symbol) | ~173M | ~2.7 GB |

Options cost roughly 20x the underlying, entirely because each request pulls a
full chain rather than a single series. PRO's 4 threads would about halve the
option wall time; nothing on offer makes it fast.

`ECHO`, `MRSH`, and `VMRK` return no data — historical entries from the
Wikipedia changes table that are not in ThetaData's stock universe.
