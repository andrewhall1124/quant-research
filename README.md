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

Full-year 2025, free tier at 2 workers:

| Pull | Wall time | Rows | Size |
|---|---|---|---|
| Universe | seconds | 126,002 | 10 KB |
| Underlying EOD (520 symbols) | 17 min (2.0 s/symbol) | 128,542 | 3.8 MB |
| Option chains (519 symbols) | 3.5 hr (24.2 s/symbol) | 110,324,355 | 1.58 GB |

Options cost roughly 12x the underlying, because each request pulls a full
chain rather than a single series. PRO's 4 threads would about halve the
option wall time; nothing on offer makes it fast.

## Ticker symbology

ThetaData's stock and option endpoints disagree, and getting this wrong is not
always loud:

- Options strip the dot from share classes (`BRK.B` -> `BRKB`, `BF.B` -> `BFB`);
  stocks keep it. Requesting the wrong spelling returns "No data found".
- `BNY` must be mapped to `BK` for **both** asset classes. The stock endpoint
  answers a `BNY` request with an unrelated ~$10 small-cap instead of an error,
  so an unmapped `BNY` silently fills the dataset with the wrong instrument.
  Sanity-check price levels when adding symbols.
- `NVR` trades but has no chain in ThetaData's 15,715-symbol option universe.
  `ECHO`, `MRSH` and `VMRK` are stale Wikipedia changes-table entries.

All of this lives in `TICKER_OVERRIDES` / `UNAVAILABLE` in `common.py`.

## Other asset classes

ThetaData covers **Options, Stocks, Indices, and Interest Rates**. There is no
futures data, and no FX, crypto, or commodities -- not in the client, not on
the pricing page. Those need a different vendor.

Indices and rates are EOD and free, and `reference.py` pulls all of it in
about 35 seconds:

| Table | Contents |
|---|---|
| `indices_2025.parquet` | SPX, RUT, OEX, XSP + VIX1D, VIX9D, VIX, VIX3M, VIX1Y, VVIX, SKEW |
| `yields_2025.parquet` | CBOE treasury yield indices: 13w, 5y, 10y, 30y |
| `rates_2025.parquet` | SOFR overnight |

Caveats:

- NDX is rejected with `INVALID_ARGUMENT`; DJI and MOVE return no data. These
  are licensed indices ThetaData does not redistribute. The CBOE family is
  fine.
- The yield indices are quoted at **10x the yield in percent** (TNX 43.0 =
  4.30%), so `reference.py` divides by 1000 to store a decimal yield.
- `interest_rate_history_eod` serves only `SOFR`; there are no other tenors.
  Use the yield indices for curve shape.

The full VIX term structure being free is worth noting -- it is the natural
companion to the option chains, and needs no paid tier.

## Intraday access

Probed empirically: on FREE, **every** intraday endpoint returns
`PERMISSION_DENIED`. There is no partial access.

| Endpoint group | Minimum tier |
|---|---|
| 1-minute bars/quotes (stocks + options) | Value ($40/mo) |
| `at_time` snapshots | Value |
| Open interest | Value |
| Tick trades | Standard ($80/mo) |
| Greeks + implied volatility | Standard |

For 1-minute backtesting, Value suffices only if you compute greeks yourself
from quotes plus a rate curve; Standard serves them.

Volume is the real constraint, not price. Measured from the EOD chains: 949
contracts per symbol-day, so full-universe 1-minute options is ~48B rows
(~6.6 days of downloading at our measured 10.3k rows/s). Filters cut it hard:

| Filter | Keep | Contracts/symbol-day |
|---|---|---|
| unfiltered | 100% | 949 |
| dte <= 45 | 40% | 380 |
| dte <= 45, +/-10% moneyness | 11.8% | 112 |
| dte <= 45, +/-5% moneyness | 6.0% | 57 |

Option volume is heavily concentrated -- the top 10 symbols are 58.9% of
dollar volume, the top 50 are 85.6% -- so narrowing the *symbol* list rather
than the date range is what makes intraday tractable. Rank on dollar volume
with a spread ceiling, not contract count: INTC is 4th by contracts but has a
10.5% median relative spread, and KVUE's 41% spread makes any fill assumption
fiction.
