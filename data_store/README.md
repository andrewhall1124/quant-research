# data_store — the data catalog

Everything here is pulled data, gitignored except this file. Nothing is
hand-edited: any file can be rebuilt by re-running the pipeline that owns it.
A fresh clone has an empty `data_store/` and this catalog, which is the
shopping list.

**Read these through `data_access_layer`, never by path.** The loaders exist
because most of the gotchas below have a fix baked into them, and reaching past
them means re-deriving each one by hand.

## Index

| File | Pipeline | Loader | Rows | Coverage |
|---|---|---|---|---|
| `universe.parquet` | `universe` | `load_universe` | 126,002 | 2025-01-02 → 2025-12-31 |
| `underlying_2025.parquet` | `underlying` | `load_underlying` | 128,542 | 2025-01-02 → 2025-12-31 |
| `options_2025/<SYM>.parquet` | `options` | `load_option_chain` | 114,366,912 | 2025, 519 files, 1.64 GB |
| `index_options_2025/<ROOT>.parquet` | `options --symbols` | `load_option_chain(index=True)` | 9,785,614 | 2025, 3 files, 0.16 GB |
| `indices.parquet` | `reference` | `load_indices`, `load_index_closes` | 5,531 | 2024-01-02 → 2025-12-31 |
| `yields.parquet` | `reference` | `load_yields` | 2,000 | 2024-01-02 → 2025-12-31 |
| `rates.parquet` | `reference` | `load_rates` | 731 | 2024-01-01 → 2025-12-31 |
| `fred_rates.parquet` | `reference` | `load_fred_rates` | 2,430 | 2024-12-02 → 2025-12-31 |
| `corporate_actions.parquet` | `corporate_actions` | `load_corporate_actions` | 2,728 | 2025-01-02 → present |
| `ticker_check.parquet` | `corporate_actions` | `load_ticker_check`, `trusted_symbols` | 523 | one row per universe ticker |

Year-stamped names mark the expensive per-symbol pulls, pinned to the window
they were pulled for. Reference tables are cheap to re-pull in full and carry
no year.

## Things that are true of every ThetaData table

Read these once; they explain most of the per-dataset notes.

- **There is no `date` column.** ThetaData stamps `created`, the timestamp of
  its EOD report, which is generated at 17:15 ET and trickles out to ~17:26 for
  the larger chains. The session date is `created.dt.date()`, which is what
  every loader derives.
- **Prices are raw.** Nothing is split- or dividend-adjusted, at any
  subscription tier. See `corporate_actions.parquet`.
- **A symbol request never errors politely.** ThetaData answers a bad symbol
  with *some* instrument rather than a 404 — `BNY` returns an unrelated ~$10
  small-cap. Yahoo does the same. Both are handled by the symbology table and
  the close-agreement check, not by trusting the vendor.
- **Nothing is null.** Missing data is encoded as `0.0`, which is far more
  dangerous, because it survives arithmetic silently.
- **Quotes are simultaneous with the close.** The option NBBO and the stock
  close come from the same 17:15 snapshot; put-call parity prices back to the
  cash close within 3–8 bp for liquid names. This is the property worth
  protecting, and it is why prices are never taken from a second vendor.

---

## `universe.parquet` — point-in-time S&P 500 membership

One row per (date, ticker) for every day a name was in the index.

| Column | Type | Notes |
|---|---|---|
| `date` | Date | trading day |
| `ticker` | String | Wikipedia spelling — `BRK.B`, `BF.B` |
| `year` | Int32 | partition helper |

523 distinct tickers over 2025; 504 on almost every day (505 on one). Built by walking
Wikipedia's current constituent list backwards through the changes table, so
membership is genuinely point-in-time rather than today's list projected
backwards. The trading calendar comes from SPY's EOD history, because
ThetaData's calendar endpoint needs a paid tier.

**Gotchas**

- The `ticker` column is in **Wikipedia** spelling, which matches neither
  ThetaData nor Yahoo. Map with `data_pipelines.common.normalize_ticker`.
- 523 tickers but only 520 have prices and 519 have chains. `BNY`, `ECHO`,
  `MRSH`, `NVR` and `VMRK` are the gap — see the symbology section below.
- Use it as a filter, not just a list: `load_underlying(in_universe=True)`
  restricts to days a name was actually a member, which drops rows ThetaData
  returns for a symbol *before* it listed.

## `underlying_2025.parquet` — EOD stock prices

| Column | Type | Notes |
|---|---|---|
| `created` | Datetime(ms, America/New_York) | 17:15 ET report stamp; the session date |
| `last_trade` | Datetime(ms, America/New_York) | timestamp of the closing print |
| `open` `high` `low` `close` | Float64 | consolidated session OHLC, **unadjusted** |
| `volume` `count` | Int64 | shares, and number of trades |
| `bid` `ask` `bid_size` `ask_size` | — | NBBO snapshot at report time |
| `bid_exchange` `ask_exchange` `bid_condition` `ask_condition` | Int64 | venue and condition codes |
| `symbol` | String | ThetaData stock spelling — `BRK.B`, and `BK` for BNY |

520 symbols, 250 trading days.

**Gotchas**

- **Delisted names get a zero row, not a missing row.** HES (2025-07-18), JNPR
  (2025-07-02) and K (2025-12-11) each end with `open = high = low = close = 0`
  — HES with 104,703 shares of volume attached. A naive return books −100%.
  `load_underlying` drops these by default (`drop_zero_prices=True`).
- **Six unadjusted splits in 2025**: ORLY 15:1, NFLX 10:1, NOW 5:1, IBKR 4:1,
  TPL 3:1, FAST 2:1. Never compute a return from bare `close.diff()`; use
  `load_underlying(with_actions=True)` with `split_adjusted_return()`.
- **The split session itself can be corrupt.** NVDA's 2024-06-10 10:1 carries a
  high of 195.95 against an actual post-split range near 117–123 — the print
  mixes pre- and post-split ticks.
- **Pre-listing stub rows.** SOLS has four Jan–Apr 2025 rows priced at
  $0.000001–$0.0001, with real volume, months before it began trading on
  2025-10-30. `in_universe=True` removes them.
- `close` is the last trade and the quote is a 17:15 snapshot that includes
  after-hours movement, so they differ by ~14 bp at the median. That is
  expected, not an error.

## `options_2025/<SYMBOL>.parquet` — EOD option chains

One file per root, because a full year of every listed expiration across 500
names does not fit in memory. 519 files, 114.4 M contract-days, 1.64 GB.

| Column | Type | Notes |
|---|---|---|
| `symbol` | String | option root, dots stripped — `BRKB`, `BFB` |
| `expiration` | **String** | `YYYY-MM-DD`; the loader parses it to Date |
| `strike` | Float64 | |
| `right` | String | `"CALL"` / `"PUT"` — spelled out, uppercase |
| `created` | Datetime(ms, America/New_York) | 17:15–17:26 ET report stamp |
| `last_trade` | Datetime(ms, America/New_York) | midnight when the contract never traded |
| `open` `high` `low` `close` | Float64 | **0.0, not null, when volume is 0** |
| `volume` `count` | Int64 | |
| `bid` `ask` `bid_size` `ask_size` + exchange/condition codes | — | NBBO at report time |

`load_option_chain` adds `date`, `dte`, `mid`, and optionally `spot` and
`moneyness`, and filters lazily — asking for one symbol reads only that file.

**Gotchas**

- **53% of contract-days never trade, and their OHLC is `0.0`.** Anything
  averaging or differencing `close` silently treats half the chain as a
  zero-priced option. Filter on `volume > 0`, or use `mid`.
- **There is no underlying price in the chain.** Join it from
  `underlying_2025.parquet`, which `with_spot=True` does.
- **Split-adjusted contracts have no flag.** After ORLY's 15:1, 62 of its 106
  strikes were adjusted contracts (45.33, 46.67 = 680/15) with non-standard
  deliverables, interleaved with the new standard grid for the remaining life
  of every pre-split expiration — months, not one day. Nothing in the schema
  distinguishes them; filter by strike-grid regularity or exclude the root.
- Defect rates over the full 114 M rows, for calibration: 8,723 crossed quotes
  (0.008%), 4,275 rows with no quote at all (0.004%), zero nulls, zero
  duplicates, zero negative prices, zero rows that traded at a zero close.
- Liquidity decays hard. ATM relative spreads run ~1.9% on NVDA, 3.2% on AAPL,
  18% on JNJ, 40% on NWSA. Any cross-sectional study needs a liquidity filter.

## `index_options_2025/<ROOT>.parquet` — EOD index chains

Same schema as the single-name chains. SPX 2.20 M rows, SPXW 4.27 M, XSP 3.32 M.

**Gotchas**

- **SPX vs SPXW.** The `SPX` root holds *only* third-Friday monthlies — 34
  expirations, every Friday one falling on day 15–21. Every weekly and
  end-of-month expiration is under `SPXW` (291 expirations). Asking `SPX` for a
  30-dte chain returns empty on most days; **use `SPXW` for anything
  dte-targeted.**
- The three non-Friday SPX expirations (2025-04-17, 2026-06-18, 2027-06-17) are
  months where the third Friday is a market holiday, so the monthly rolls back
  to Thursday. Not an error.
- **Two corrupt rows in SPX**: expirations of 2022-12-01 and 2022-12-28 appear
  in 2025 sessions, one quoting a 3000-strike put at 29,956 / 30,044. Filter
  `dte >= 0`.
- Index roots settle against a published level, not a stock, so `spot_series`
  routes them to `indices.parquet` via `INDEX_ROOT_TO_SPOT`.

## `indices.parquet` — index levels and the VIX complex

Long format: one row per (date, symbol). 11 symbols — SPX, RUT, OEX, XSP,
VIX1D, VIX9D, VIX, VIX3M, VIX1Y, VVIX, SKEW.

| Column | Type |
|---|---|
| `date` `symbol` | Date, String |
| `open` `high` `low` `close` | Float64 |

**Gotchas**

- **VIX prints on market holidays where SPX does not** — 15 such dates across
  2024–25 (MLK, Presidents' Day, Memorial Day, Juneteenth, July 4,
  Labor Day, Thanksgiving, and the 2025-01-09 national day of mourning). VIX
  has 517 rows to SPX's 502. The long table has no nulls; the holes only appear
  once you pivot, so **always drop rows where the SPX level is null before
  computing returns**, or you book a spurious zero-return day.
- SKEW is separately missing 6 days that SPX has (2024-01-19, 2024-05-22,
  2024-11-29, 2025-02-20, 2025-09-05, 2025-12-24).
- NDX, DJI and MOVE are not available at any tier — licensed indices ThetaData
  does not redistribute. The CBOE family is free.
- Coverage starts 2024-01-02 because that is the free tier's index history
  floor; 2023 is refused as VALUE, 2022 as STANDARD, 2020 as PROFESSIONAL.

## `yields.parquet` — CBOE treasury yield indices

| Column | Type | Notes |
|---|---|---|
| `date` | Date | |
| `tenor` | String | `13w`, `5y`, `10y`, `30y` |
| `yield` | Float64 | **decimal** — 0.0430 is 4.30% |

**Gotcha.** The raw indices are quoted at 10x the yield in percent (TNX 43.0 =
4.30%), so `reference.py` divides by 1000 on the way in. The stored value is
already a decimal; do not scale it again.

## `rates.parquet` — SOFR overnight

`date`, `symbol` (always `SOFR`), `rate` (decimal). Calendar days, not trading
days — 731 rows for two years.

**Gotcha.** `interest_rate_history_eod` serves *only* SOFR; there are no other
tenors. Use `yields.parquet` or `fred_rates.parquet` for curve shape.

## `fred_rates.parquet` — FRED treasury and SOFR series

| Column | Type | Notes |
|---|---|---|
| `date` | Date | |
| `series` | String | DGS1MO, DGS3MO, DGS6MO, DGS1, DGS2, SOFR, SOFR30/90/180DAYAVG |
| `family` | String | `treasury`, `sofr`, `sofr_avg` |
| `tenor_y` | Float64 | tenor in years, for interpolation |
| `rate` | Float64 | decimal |

Starts 2024-12-02, later than the other reference tables — this is the shortest
history in the store, so check coverage before joining it to a 2024 study.

## `corporate_actions.parquet` — splits and dividends

Sourced from Yahoo, because ThetaData serves raw prices and its client exposes
no splits endpoint.

| Column | Type | Notes |
|---|---|---|
| `symbol` | String | ThetaData stock spelling, to join against `underlying` |
| `yahoo_symbol` | String | Yahoo spelling, for tracing |
| `date` | Date | ex-date |
| `action` | String | `split` or `dividend` |
| `value` | Float64 | split ratio, or dividend per share |

**Gotchas**

- **Yahoo is the calendar only, never the prices.** Substituting Yahoo's
  consolidated close breaks the same-snapshot property that makes
  close-to-close option P&L trustworthy.
- **Coverage runs to *today*, not to the end of the price panel.** Yahoo
  back-adjusts its closes for every split up to the present, so a pull that
  stopped at the panel's end would omit later splits and mis-verify the name —
  BKNG's 25:1 on 2026-04-06 makes its 2025 closes look 25x too high. This is
  why the pipeline has no `--end`.
- **Non-integer "splits" are spinoff adjustment factors**, which is what you
  want: DD 2.39 on the Qnity spinoff, HON 1.061, FTV 1.327, WDC 1.323.
- Use `split_adjusted_return()`, which applies the ex-date ratio between
  consecutive closes rather than a cumulative back-adjustment. Only a split
  falling *between* two closes affects a return, so this also makes splits
  after the sample end correctly irrelevant.

## `ticker_check.parquet` — which symbols can be trusted

One row per universe ticker. ThetaData's raw closes are back-adjusted with the
pulled splits and compared to Yahoo's own; agreement means the ticker mapping
and the split factors are both right.

| Column | Type | Notes |
|---|---|---|
| `symbol` `yahoo_symbol` | String | |
| `theta_days` `yahoo_days` `overlap_days` | UInt32 | |
| `median_difference` `p99_difference` | Float64 | relative close difference |
| `status` | String | see below |

| Status | Count | Meaning |
|---|---|---|
| `ok` | 507 | median difference ≤ 0.5%; safe to adjust |
| `yahoo_missing` | 10 | Yahoo purges delisted names — HES, JNPR, K, DFS all 404 |
| `theta_missing` | 3 | ECHO, MRSH, VMRK — never pulled from ThetaData |
| `thin_overlap` | 2 | AVB, EA — fewer than 20 overlapping days |
| `mismatch` | 1 | MNST — the two sources disagree |

`trusted_symbols()` returns the `ok` list.

**Gotchas**

- A `mismatch` means "these two sources disagree about this symbol", **not**
  "ThetaData is wrong". MNST's 2026-08-11 2:1 is in Yahoo's splits series but
  is not applied to its own 2025 closes — Yahoo contradicting itself.
- `yahoo_missing` is where Yahoo is *worse* than ThetaData: it deletes delisted
  tickers entirely, which is textbook survivorship bias. Those names need
  excluding on point-in-time membership, not adjusting.

---

## Symbology

Three spellings for the same universe, and getting it wrong is not always loud.

| Universe (Wikipedia) | ThetaData stock | ThetaData option | Yahoo |
|---|---|---|---|
| `BRK.B` | `BRK.B` | `BRKB` | `BRK-B` |
| `BF.B` | `BF.B` | `BFB` | `BF-B` |
| `BNY` | `BK` | `BK` | `BNY` |

`BNY` is the cautionary one and it cuts both ways: ThetaData answers a `BNY`
request with an unrelated ~$10 small-cap instead of an error, while Yahoo
serves the real company under `BNY` and 404s on `BK`. That is why
`TICKER_OVERRIDES` in `data_pipelines/common.py` is keyed by destination
(`"stock"`, `"option"`, `"yahoo"`) rather than shared.

`UNAVAILABLE` in the same module lists what ThetaData cannot serve: `NVR`
trades but has no chain in the 15,715-symbol option universe; `ECHO`, `MRSH`
and `VMRK` are stale Wikipedia changes-table entries. There is no hand-written
equivalent for Yahoo, because a list written by hand cannot anticipate a vendor
that answers every symbol — `ticker_check.parquet` is the machine-generated
substitute.

## Rebuilding

```bash
uv run python -m data_pipelines.universe            # seconds
uv run python -m data_pipelines.reference           # ~1 min
uv run python -m data_pipelines.corporate_actions   # ~30 s
uv run python -m data_pipelines.underlying          # 17 min
uv run python -m data_pipelines.options             # 3.5 hr, resumable
uv run python -m data_pipelines.options --symbols SPX,SPXW,XSP \
    --output-dir data_store/index_options_2025
```

Timings are free tier at 2 workers. `options` skips symbols already on disk, so
an interrupted pull can just be re-run. Nothing else is resumable, but nothing
else takes long enough to matter.

Order matters in one place: `underlying`, `options` and `corporate_actions` all
read `universe.parquet`, so build that first.

## Where the evidence lives

Every defect rate quoted here is reproducible:
[`research/data_quality/`](../research/data_quality/) re-probes the
subscription, re-pulls a sample, and sweeps all 114 M stored contract-days.
