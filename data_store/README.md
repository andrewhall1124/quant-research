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
| `universe_history.parquet` | `universe --history` | `load_universe(with_history=True)` | 1,016,878 | 2017-01-03 → 2024-12-31 |
| `underlying_2025.parquet` | `underlying` | `load_underlying` | 128,542 | 2025-01-02 → 2025-12-31 |
| `underlying_history.parquet` | `underlying --start 2023-06-01` | `load_underlying(with_history=True)` | 203,496 | 2023-06-01 → 2024-12-31 |
| `option_greeks/<SYM>.parquet` | `option_greeks` | `load_option_greeks` | 114,365,634 | 2025, 519 files, 8.5 GB |
| `option_greeks_2024/<SYM>.parquet` | `option_greeks --year 2024` | `load_option_greeks(years=2024)` | 104,511,492 | 2024, 516 files, 8.25 GB |
| `option_greeks_2023/<SYM>.parquet` | `option_greeks --year 2023` | `load_option_greeks(years=2023)` | 88,135,029 | 2023, 512 files, 7.05 GB |
| `option_greeks_2022/<SYM>.parquet` | `option_greeks --year 2022` | `load_option_greeks(years=2022)` | 85,061,633 | 2022, 513 files, 6.87 GB |
| `option_greeks_2021/<SYM>.parquet` | `option_greeks --year 2021` | `load_option_greeks(years=2021)` | 85,799,666 | 2021, 511 files, 6.92 GB |
| `open_interest/<SYM>.parquet` | `open_interest` | `load_open_interest` | 113,008,800 | 2025, 519 files, 438 MB |
| `open_interest_2024/<SYM>.parquet` | `open_interest --year 2024` | `load_open_interest(years=2024)` | 104,498,601 | 2024, 516 files, 0.41 GB |
| `open_interest_2023/<SYM>.parquet` | `open_interest --year 2023` | `load_open_interest(years=2023)` | 88,123,911 | 2023, 512 files, 0.38 GB |
| `open_interest_2022/<SYM>.parquet` | `open_interest --year 2022` | `load_open_interest(years=2022)` | 86,547,057 | 2022, 514 files, 0.37 GB |
| `open_interest_2021/<SYM>.parquet` | `open_interest --year 2021` | `load_open_interest(years=2021)` | 87,736,964 | 2021, 511 files, 0.19 GB |
| `index_greeks_2025/<ROOT>.parquet` | `option_greeks --symbols` | `load_option_greeks(index=True)` | 9,768,195 | 2025, 4 files, 0.79 GB |
| `indices.parquet` | `reference` | `load_indices`, `load_index_closes` | 5,531 | 2024-01-02 → 2025-12-31 |
| `yields.parquet` | `reference` | `load_yields` | 2,000 | 2024-01-02 → 2025-12-31 |
| `fred_rates.parquet` | `reference` | `load_fred_rates` | 2,430 | 2024-12-02 → 2025-12-31 |
| `corporate_actions.parquet` | `corporate_actions` | `load_corporate_actions` | 16,584 | 2016-01-04 → present |
| `symbology_check.parquet` | `symbology --years …` | read directly | — | one row per symbol-year pulled |
| `earnings.parquet` | `earnings` | `load_earnings`, `with_earnings_distance` | 45,060 | 1999-08-02 → 2026-12-09 |

Year-stamped names mark the expensive per-symbol pulls, pinned to the window
they were pulled for. Reference tables are cheap to re-pull in full and carry
no year. `option_greeks/` and `open_interest/` are 2025 under their bare names,
a convention that predates the stamping; every other year is
`<dataset>_<YYYY>/`. `paths.option_dir(dataset, year)` resolves either, and
`paths.available_years(dataset)` says what is on disk.

Loaders default to the 2025 sample rather than to everything on disk, so
landing a backfill year never silently changes what a study that asked for no
window already loads. Pass `years=None` for the full history.

**Only one pull may run at a time.** ThetaData issues one session per account,
so two pipeline processes fight over it and the loser gets UNAUTHENTICATED on
every request. Chain backfill years sequentially; put concurrency in
`--workers`, which shares one session across threads.

## `open_interest/`

One parquet per symbol, 519 files, 113.0M contract-days, 438 MB. Calendar 2025.
Written by `data_pipelines.open_interest`; read through the backtest's contract
panel (`tools/backtest/panel.py`), which joins it onto the greeks rows.

| column | notes |
|---|---|
| `symbol`, `expiration`, `strike`, `right` | contract key; `right` is `CALL`/`PUT`, matching `option_greeks/` |
| `timestamp` | stamped pre-open, ~06:30 ET |
| `open_interest` | contracts standing after the *previous* session's close |

Three things to know:

- **It is a separate endpoint from the greeks.** `option_greeks/` carries
  quotes, greeks and IV but no open interest; this is
  `/v3/option/history/open_interest`, badged Value/Standard/Pro.
- **It accepts a date range**, unlike the EOD greeks endpoint that forces
  `expiration=*` a day at a time. A symbol-year is one request, so the whole
  universe took ~3 hours rather than a day.
- **The stamp is pre-open and the figure is one day stale.** OI for session
  *d* is not known until after *d* closes, so the print stamped on *d* reports
  the position standing after *d-1*. That is the number available to someone
  forming a position at *d*'s close, so it joins on `date` with no shift — but
  do not read it as live.

Median open interest on a near-money 30-day contract is low: 19 contracts on
the thinner leg of an ATM straddle, 119 at the 75th percentile. A floor of 500
leaves ~15 eligible names on the median day across the S&P 500.


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

## The 2021-2024 backfill

363.5 M contract-days added over five pulls, ~29 GB, with **zero failed
requests** in roughly 520,000. Each year took ~2 hours for greeks and ~1.5
hours for open interest, run strictly one at a time.

**Coverage is not the full universe, and the gap is renames.** The universe
carries today's ticker at every historical date, so a name that has since been
renamed is requested under a symbol that did not exist then. Most fail safely —
NoDataFound, no file written — leaving 2 to 6 constituents absent per year:

| Year | Members | Pulled | Absent |
|---|---|---|---|
| 2021 | 521 | 511 | BALL, EG, ELV, PSKY, RVTY, SW + the 4 deleted below |
| 2022 | 520 | 513 | COR, EG, MBC, PSKY, RVTY, SW + DOC |
| 2023 | 515 | 512 | PSKY, SW + DOC |
| 2024 | 518 | 516 | FISV, PSKY |

**Six symbol-years were pulled, found to be a different company, and deleted.**
This is the failure that does *not* announce itself, and `symbology.py` is the
only thing standing between it and a study:

| Deleted | ThetaData served | The universe meant |
|---|---|---|
| META 2021 | $13.90–16.90 | Facebook at $245–382, then `FB` |
| GEN 2021 | $0.42–1.14 | Gen Digital at $19.51–28.67, then `NLOK` |
| COR 2021 | $0.00–172.51 | Cencora at $96.50–133.77, then `ABC` |
| DOC 2021, 2022, 2023 | $13.71–19.55 | Healthpeak at $21.61–37.36, then `PEAK` |

Healthpeak took the `DOC` ticker on 2024-03-01 on merging with Physicians
Realty Trust, which is what ThetaData was serving; DOC 2024 is clean. **Always
consult `symbology_check.parquet` before using a backfill year.**

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

## `option_greeks/<SYMBOL>.parquet` — EOD chains with greeks, IV and spot

The only option chain store. 43 columns: 20 trade and quote fields plus 23
more. 519 files, 114.4 M contract-days, 8.5 GB for 2025.

One file per root, because a full year of every listed expiration across 500
names does not fit in memory. `symbol` is the option root with dots stripped
(`BRKB`, `BFB`); `expiration` is a **String** the loader parses to Date;
`right` is `"CALL"` / `"PUT"`, spelled out and uppercase.

| Extra column | Notes |
|---|---|
| `delta` `theta` `vega` `rho` `epsilon` `lambda` `gamma` | 1st order |
| `vanna` `charm` `vomma` `veta` `vera` | 2nd order |
| `speed` `zomma` `color` `ultima` | 3rd order |
| `d1` `d2` `dual_delta` `dual_gamma` | Black-Scholes intermediates |
| `implied_vol` `iv_error` | `iv_error` is the inversion residual; its median is 0.0 |
| `underlying_price` `underlying_timestamp` | spot, struck at the same instant as the quote |

**Verified against the price-only pull this replaced**, on all 114 M rows,
before that pull was deleted: 517 of 519 symbols matched exactly on row count,
trading-day count, and the sum of bid, ask, volume and close.
`underlying_price` agrees with `underlying_2025.close` to 1e-14. The two
studies that read the old store rebuild their ATM IV series identically to the
last bit — 250 days for AAPL, KO and NEM, 247 for SPXW.

**Gotchas**

- **The session stamp is `underlying_timestamp`**, the stamp on the spot print
  the greeks were struck against, so it is defined even for a contract that
  never traded. `timestamp` is the contract's own last trade.
- **53% of contract-days never trade, and their OHLC is `0.0`, not null.**
  Anything averaging or differencing `close` silently treats half the chain as
  a zero-priced option. Filter on `volume > 0`, or use `mid`.
- **`moneyness` needs no join.** `underlying_price` is on the row, which also
  removes a dependency on the stock-side ticker mapping — and is the only spot
  available before 2023-06-01, where the free stock tier stops.
- **`implied_vol` is `0.0`, not null, where there is no quote** — about 5% of
  rows. `quoted_only=True` on the loader drops them.
- **Two symbols had fewer rows than the plain chain, and the greeks store is
  the *better* one.** ANSS loses its last two sessions (2025-07-17/18) and WBA
  its last one (2025-08-28); both were acquired in 2025. Every dropped row is
  100% no-trade and 100% no-quote with `bid = ask = close = 0` — the greeks
  endpoint declines to price a dead chain where the plain EOD endpoint emits
  all-zero stubs. Volume checksums are identical, confirming nothing real was
  lost.
- Pulling this costs ~250 requests per symbol-year (`expiration=*` is
  day-at-a-time), so 2.6 hours for 2025 at 4 workers. Standard accepted 4
  workers with zero `RESOURCE_EXHAUSTED` retries. Earlier years are smaller:
  AAPL's 2017 chain is 0.55x the size of its 2025 one, 2022 is 0.81x.

## `index_greeks_2025/<ROOT>.parquet` — EOD index chains

Same schema as the single-name chains, pulled by the same pipeline with
`--symbols SPX,SPXW,XSP,VIX --output-dir data_store/index_greeks_2025`. They
live apart because they are not universe members and a caller listing
`available_option_symbols` wants one set or the other.

SPX 2.13 M rows, SPXW 4.15 M, XSP 3.22 M, VIX 0.27 M.

`underlying_price` on these rows is the **index level**, which is the only way
to get SPX or VIX before 2024-01-01 — the index EOD endpoint refuses anything
earlier on the free index tier. The `VIX` root is what makes that work for the
volatility complex itself: its `underlying_price` is the VIX level.

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

## `earnings.parquet` — announcement dates

From Yahoo, ~100 announcements per name. 45,060 rows across 521 of the 523
universe tickers, reaching back to 1999 — deeper than any option history
ThetaData sells, so this is never the binding constraint. 19,834 of them fall
in 2016-2025.

| Column | Type | Notes |
|---|---|---|
| `symbol` `yahoo_symbol` | String | |
| `date` | Date | announcement date |
| `session` | String | `bmo` (26,582), `amc` (17,897), `unknown` (581) |
| `announced_at` | Datetime(ms, America/New_York) | scheduled time, which is where `session` comes from |
| `eps_estimate` `reported_eps` `surprise_pct` | Float64 | null for announcements not yet reported |

**Gotchas**

- **`session` changes which day the move lands on.** A before-the-open report
  moves that day's close-to-close return; an after-the-close report moves the
  *next* one. Two thirds of the table is `bmo`, so getting this backwards
  misaligns most of the sample by a day.
- **This is what separates a volatility signal from an earnings-timing
  signal.** Implied vol lifts into a report, so any cross-sectional ranking on
  implied-minus-realized sorts largely on days-to-announcement unless it is
  controlled for. `with_earnings_distance()` adds `days_to_earnings` and
  `days_since_earnings` to any (symbol, date) panel.
- Dates for future quarters are Yahoo's *estimates* and get revised. Past dates
  are firm.
- Two symbols have no earnings data at all. Announcement dates are scheduled in
  advance, so using them as an ex-ante filter is not lookahead — but the EPS
  columns are, and must not be.

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
uv run python -m data_pipelines.earnings            # ~3 min
uv run python -m data_pipelines.underlying          # 17 min
uv run python -m data_pipelines.option_greeks       # 2.6 hr, resumable
uv run python -m data_pipelines.option_greeks       # 2.6 hr, resumable
uv run python -m data_pipelines.option_greeks --symbols SPX,SPXW,XSP,VIX \
    --output-dir data_store/index_greeks_2025
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
