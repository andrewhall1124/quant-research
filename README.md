# quant-research

EOD equity, index and options data from ThetaData, and research built on it.

## Layout

Three folders, in the order data moves through them:

```
data_pipelines/     pulls from vendors and writes parquet   (the only writers)
data_store/         the parquet lives here                  (gitignored)
data_access_layer/  loads and filters it back out           (the only readers)
research/           studies that import the access layer
```

The rule that keeps this navigable: **research never opens a file path.** It
calls `data_access_layer`, which is the single place that knows where anything
lives (`data_access_layer/paths.py`) and how to filter it
(`data_access_layer/loaders.py`).

## Pulling data

Run as modules from the repo root, so imports resolve:

```bash
uv run python -m data_pipelines.universe      # point-in-time S&P 500 membership
uv run python -m data_pipelines.underlying    # EOD stock prices for that universe
uv run python -m data_pipelines.options       # EOD option chains for that universe
uv run python -m data_pipelines.reference     # indices, VIX complex, yields, SOFR
uv run python -m data_pipelines.corporate_actions  # splits + dividends (Yahoo), and a vendor cross-check
uv run python -m data_pipelines.options --symbols SPX,SPXW,XSP \
    --output-dir data_store/index_options_2025
```

`underlying` and `options` take `--start`, `--end`, `--workers`, and `--limit`
(sample the first N tickers, for benchmarking). `options --symbols` takes a
comma-separated list of roots and bypasses the universe file, which is how the
index chains get pulled — they are not constituents.

`universe` reconstructs membership by walking Wikipedia's current constituent
list backwards through the changes table. The trading calendar comes from SPY's
EOD history, because ThetaData's calendar endpoint needs a paid tier.

`options` writes one parquet per symbol and skips symbols already on disk, so
it is resumable and never holds a full year of chains in memory.

Every dataset is documented in
**[`data_store/README.md`](data_store/README.md)** — schema, coverage, owning
pipeline, loader, and the gotchas specific to each one. Read that before
touching a table you have not used before.

## Reading data

```python
import data_access_layer as dal

dal.describe_store()                       # what is on disk, and what is missing
prices_df = dal.load_underlying(with_actions=True, in_universe=True)
returns_df = prices_df.with_columns(dal.split_adjusted_return().over("symbol"))
levels_df = dal.load_index_closes(["SPX", "VIX", "VIX9D", "VIX3M"])   # wide
chain_df  = dal.load_option_chain("SPXW", index=True, min_dte=25, max_dte=35,
                                  max_moneyness=0.05, with_spot=True)
```

Loaders return eager polars DataFrames with a `date` column of dtype `Date`,
whatever the raw file carries. `load_option_chain` adds `dte`, `mid`, and
optionally `spot` / `moneyness`, and filters lazily — asking for one symbol out
of the 1.6 GB chain directory reads only that file. A missing dataset raises
`MissingDataset` naming the pipeline command that would create it.

## Research

`research/<topic>/` — each has its own README, a script that regenerates every
figure, and a report:

- [`research/volatility/`](research/volatility/) — do realized, ARCH, GARCH and
  implied volatility forecast forward realized and forward implied vol?
- [`research/data_quality/`](research/data_quality/) — is the ThetaData EOD feed
  good enough to build on, and what is already wrong with the panel on disk?

## ThetaData constraints

Discovered while building this, and they shape the design:

- **One session per account.** Constructing a second `ThetaClient` — even in
  the same process — invalidates the first with `Invalid session ID`. All
  threads share the singleton in `data_pipelines/common.py`.
- **Concurrency is capped by tier**, as server-allocated threads: FREE 1,
  VALUE 1, STANDARD 2, PRO 4. On the free tier 2 workers run clean and 4
  return `RESOURCE_EXHAUSTED`, so requests retry with exponential backoff.
- **Flat files cover only the 7 most recent calendar days**, and are Pro-only.
  They are not a backfill path; per-symbol requests are the only option.
- **History depth is tiered, and the tier is quoted per request.** Index
  history from 2024-01-01 is free; a start in 2023 is refused as VALUE, 2022 as
  STANDARD, 2020 as PROFESSIONAL. Stock and option history reaches back to
  2023-06-01 on free.
- **No request may span more than 365 days** (`INVALID_ARGUMENT`). Longer
  windows have to be stitched from chunks, as `reference.py` does.
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

## Corporate actions

ThetaData serves **raw, unadjusted prices** and its client has no splits
endpoint, so 2025 alone contains six splits that a naive return reads as a
crash — ORLY 15:1 (-93%), NFLX 10:1, NOW 5:1, IBKR 4:1, TPL 3:1, FAST 2:1 —
plus three delisted names (HES, JNPR, K) whose final row is
`open = high = low = close = 0`, with volume attached, rather than absent.

`corporate_actions.py` fills the gap from Yahoo, which is used for the split
and dividend *calendar* only. Prices stay ThetaData's: the option quotes and
the stock close are the same 17:15 ET snapshot, and mixing a second vendor's
close into that breaks the one property that makes close-to-close option P&L
trustworthy.

Because Yahoo answers almost any symbol with *something*, every name is
verified rather than trusted. ThetaData's raw closes are back-adjusted with the
pulled splits and compared to Yahoo's own; agreement means the ticker mapping
and the split factors are both right. Of 523 universe tickers, 507 agree, 10
have no Yahoo history at all (Yahoo purges delisted names — HES, JNPR, K and
DFS all 404, so it is *worse* than ThetaData there), 3 are absent from
ThetaData, and 3 disagree. `dal.trusted_symbols()` returns the clean list and
`dal.load_ticker_check()` says why the rest failed.

Two gotchas found the hard way:

- **Yahoo back-adjusts closes for every split up to today**, not up to the end
  of the requested window, so a pull that stops at the end of the price panel
  silently omits later splits and the whole name disagrees. BKNG's 25:1 on
  2026-04-06 makes its 2025 closes look 25x too high. The pull always runs to
  the present.
- **Yahoo's price and split series can disagree with each other** for very
  recent splits (MNST's 2026-08-11 2:1 is in its splits series but not applied
  to its 2025 closes). The check flags this as `mismatch`, which is the right
  outcome: a `mismatch` means "these two sources disagree about this symbol",
  not necessarily "ThetaData is wrong".

`split_adjusted_return()` applies the ex-date ratio between consecutive closes
rather than a cumulative factor, which is both simpler and correct: only a
split falling *between* two closes affects a return, so splits after the sample
ends are properly irrelevant. `load_underlying(in_universe=True)` additionally
restricts to point-in-time membership, which drops rows ThetaData returns for a
symbol before it listed — SOLS has four Jan-Apr 2025 rows at $0.0001, with
volume, months before it began trading on 2025-10-30.

After all of it, the 2025 panel has seven daily moves above 35%, and every one
is a real news event.

## Ticker symbology

Every vendor spells the universe differently, and getting this wrong is not
always loud:

- Options strip the dot from share classes (`BRK.B` -> `BRKB`, `BF.B` -> `BFB`);
  stocks keep it. Requesting the wrong spelling returns "No data found".
- Yahoo wants a dash instead (`BRK-B`, `BF-B`).
- `BNY` must be mapped to `BK` for **both** ThetaData asset classes. The stock
  endpoint answers a `BNY` request with an unrelated ~$10 small-cap instead of
  an error, so an unmapped `BNY` silently fills the dataset with the wrong
  instrument. Yahoo is the exact opposite — it serves the real company under
  `BNY` and 404s on `BK` — which is why the override table is keyed by
  destination rather than shared.
- `NVR` trades but has no chain in ThetaData's 15,715-symbol option universe.
  `ECHO`, `MRSH` and `VMRK` are stale Wikipedia changes-table entries.

All of this lives in `TICKER_OVERRIDES` / `UNAVAILABLE` in
`data_pipelines/common.py`, keyed by `"stock"`, `"option"` and `"yahoo"`. The
hand-written exclusion list only covers ThetaData; for Yahoo the equivalent is
produced by the close-agreement check, because a list written by hand cannot
anticipate a vendor that answers every symbol.

## Other asset classes

ThetaData covers **Options, Stocks, Indices, and Interest Rates**. There is no
futures data, and no FX, crypto, or commodities -- not in the client, not on
the pricing page. Those need a different vendor.

Indices and rates are EOD and free, and `reference.py` pulls two years of it
in about a minute:

| Table | Contents |
|---|---|
| `indices.parquet` | SPX, RUT, OEX, XSP + VIX1D, VIX9D, VIX, VIX3M, VIX1Y, VVIX, SKEW |
| `yields.parquet` | CBOE treasury yield indices: 13w, 5y, 10y, 30y |
| `rates.parquet` | SOFR overnight |

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
