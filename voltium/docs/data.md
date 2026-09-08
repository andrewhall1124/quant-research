# Data

## The store

voltium reads the repo's `data_store/`, populated by `data_pipelines`. It
does not import `data_access_layer`; `loaders.py` knows the file layout and
the vendor's column names directly, so the package is installable on its
own. `DataPaths.from_repo()` walks up from the working directory to the
first `data_store/`.

| dataset | files | coverage | grain |
| --- | --- | --- | --- |
| option chains | `option_greeks_<year>/<SYMBOL>.parquet` (`option_greeks/` for 2025) | 2017–2025 | contract-day |
| open interest | `open_interest_<year>/<SYMBOL>.parquet` (`open_interest/` for 2025) | 2017–2025 | contract-day |
| index chains | `index_greeks_<year>/{SPX,SPXW,XSP,VIX}.parquet` | 2017–2025 | contract-day |
| stocks | `underlying_history.parquet` (2023-06 to 2024), `underlying_2025.parquet` | 2023-06–2025 | symbol-day OHLCV |
| universe | `universe_history.parquet` (2017–2024), `universe.parquet` (2025) | 2017–2025 | point-in-time membership |
| sectors | `sectors.parquet` | snapshot | ticker → GICS sector |
| yields | `yields.parquet` | 2017–2025 | CBOE 13w / 5y / 10y / 30y |
| corporate actions | `corporate_actions.parquet` | 2017– | splits (ex-date ratio) and dividends |
| earnings | `earnings.parquet` | 1999– | announcement dates with session |
| index levels | `indices.parquet` | 2024–2025 | SPX and the VIX complex, OHLC |
| vol risk model | `vol_reference_returns/`, `vol_factor_*.parquet`, `vol_idio_vol.parquet` | 2017–2025 | written by `vol-risk-model`, see [risk_model.md](risk_model.md) |

The two coverage limits that shape everything: **stock OHLC starts 2023-06**
(ThetaData's stock tier floor), and **SPX OHLC starts 2024**. Option chains
carry the underlying close on every row back to 2017, which is how the
long-history path gets around both (see `scan_spot_from_chains`).

## Canonical option schema

`loaders.scan_options(paths, symbols, start, end, index=False, with_oi=True)`
returns a `LazyFrame` with exactly these columns, one row per
`(date, symbol, expiration, strike, right)`:

| column | type | meaning |
| --- | --- | --- |
| `date` | Date | the session, taken from the vendor's `underlying_timestamp` (the stamp on the spot print the greeks were struck against), not from the contract's last trade |
| `symbol` | Utf8 | option-root spelling everywhere: `BRK.B` → `BRKB`, `BNY` → `BK` |
| `expiration` | Date | |
| `strike` | Float64 | |
| `right` | Utf8 | `"C"` or `"P"` (vendor: `CALL` / `PUT`) |
| `bid`, `ask` | Float64 | NBBO at the close, per share; `0.0` where unquoted |
| `mid` | Float64 | `(bid + ask) / 2` |
| `iv` | Float64 | decimal; **null** where `|iv_error| > iv_error_tol` (default 1.0) or the vendor reports `0.0` |
| `delta`, `gamma` | Float64 | per share |
| `theta` | Float64 | per share **per calendar day** |
| `vega` | Float64 | per share **per vol point** (rescaled from the vendor's per-1.00; see below) |
| `underlying` | Float64 | spot the greeks were struck against; equals the stock close where both exist |
| `oi` | Int64 | open interest, left-joined from its own files; null when absent |
| `volume` | Int64 | |

Open interest is stamped pre-open with the position after the *previous*
close, which is what a trader forming at today's close knows, so it joins on
the same `date` with no shift.

The vendor's other columns (higher-order greeks, `d1`, `d2`, exchange codes,
OHLC of the contract) are dropped. Add them in `scan_options` if a new
instrument needs them.

## Other canonical frames

**Stocks** — `scan_stocks(paths, start, end, with_splits=False)`:
`date, symbol, open, high, low, close, volume`, unadjusted, with the
vendor's zero-priced delisting rows dropped. `with_splits=True` appends
`split_ratio` (1.0 except on an ex-date), which is what a close-to-close
return needs: `close * split_ratio / prev_close - 1`.

**Spot off the chains** — `scan_spot_from_chains(paths, symbols, start, end, index=False)`:
`date, symbol, close[, split_ratio]` from the chains' `underlying` column.
Reaches 2017; no open/high/low/volume.

**Universe** — `scan_universe(paths, start, end)`: `date, symbol`, genuinely
point-in-time. `PointInTimeUniverse` additionally drops members with no
chain file on disk for the year (`require_chain=True`).

**Sectors** — `scan_sectors(paths)`: `symbol, sector`. A snapshot of today's
GICS classification from Wikipedia (`data_pipelines.cli sectors`). A name
that has left the index has no row; a name that changed sector carries its
current one.

**Rates** — `scan_rates(paths, start, end, tenor="13w")`: `date, rate`. Only
the Black-Scholes utilities use a rate, and they default to a flat 4%.

**Splits** — `scan_splits(paths)`: `date, symbol, split_ratio` on ex-dates.

**Earnings** — `scan_earnings(paths)`: `symbol, date, session`.
`providers/earnings.py: load_earnings_events` maps each to the session whose
return carries the move (`bmo` → same session, `amc` → next). 507 of the
659 names in the 2017–2025 universe have dates; the missing ones are names
that have left the index, so the table is survivorship-biased.

## The vega-convention check

The spec asked for the vendor's vega convention to be confirmed
empirically. `loaders.run_vega_check(sample_file)` takes 400 clean contracts
from one chain (bid > 0, `|iv_error| ≤ 0.01`, 10–365 DTE, |delta| 0.2–0.8),
reprices each with Black-Scholes at IV and at IV + 0.01, and compares the
price change with `vega * 0.01` (vega per 1.00 of IV) and with `vega` (vega
per vol point). The hypothesis that wins on at least two thirds of the rows
is the convention; anything less raises. The same pass checks theta against
per-day and per-year Black-Scholes theta.

Result on this store, logged at INFO by `detect_vega_convention` and
asserted in `tests/test_loaders.py`: **vega is per 1.00 of IV** (median
`dP / vega` = 0.0100, 100% of rows) and **theta is per calendar day**. The
loader divides vega by 100 so the canonical column is per vol point. If a
future pull changes the convention, the check will say so and the loader
will rescale accordingly.

## Gotchas inherited from the vendor

* About 3% of contract-days fail to invert; they come back with IV pinned
  near 0.5 and `iv_error` of ±100. The loader nulls those. Screen on `iv`
  being non-null, never on `implied_vol` alone.
* Unquoted contracts are present with `bid = ask = 0`. `Instrument.mark`
  refuses a leg with `ask = 0`; the surface builder requires `bid > 0`.
* The index files for 2020–2021 had unquoted sessions repaired from the
  15:59 intraday bar; those rows have no second-order greeks. Nothing here
  reads second-order greeks.
* A split changes the vendor's option root. The old contract stops being
  quoted; the backtester carries it for `max_stale_days` and then closes it
  at the last mark, flagged `forced_close`. Stock-side returns are
  split-adjusted.
* 18 symbol-years in the store are a different company than the universe
  names (`data_access_layer.usable_symbol_years`). Nothing in voltium
  applies that screen yet.

## Laziness

Every loader returns a `LazyFrame`. The date predicate is pushed into the
parquet scan, and the files are sorted by `underlying_timestamp`, so a
single-session query over all 516 constituent files touches only the row
groups it needs: about 1.6 s for a cross-section, 0.04 s for one symbol's
year. A full-history query over the whole universe in one plan is the one
thing to avoid — it is what `materialize_by_symbol` exists for (see
[running.md](running.md)).
