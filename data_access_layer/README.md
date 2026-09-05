# Data access layer

Every dataset in `data_store/`, behind one import. Pipelines write, this layer
reads; research code should never open a parquet path itself.

```python
from datetime import date
import data_access_layer as dal

chain_df = dal.load_option_greeks('AAPL', date(2025, 1, 1), date(2025, 3, 31))
prices_df = dal.load_underlying(date(2025, 1, 1), date(2025, 12, 31))
```

Import names from the package, not from a submodule. The file split is an
implementation detail and a loader may move between modules.

Start by seeing what is actually on disk:

```sh
uv run python -c "import data_access_layer as dal; print(dal.describe_store())"
```

## One signature, and nothing else

```python
load_x(start=None, end=None, lazy=False)
```

A loader scans, filters to the inclusive `[start, end]` window, and returns a
LazyFrame when `lazy=True` or a collected DataFrame otherwise. `None` on either
side means open. That is the whole contract — **no screening, no joins, no
derived columns** beyond the `date` the window itself needs.

The two per-symbol option loaders take the symbol first, since it selects the
file rather than filtering rows:

```python
load_option_greeks(symbol, start=None, end=None, index=False, lazy=False)
load_open_interest(symbol, start=None, end=None, lazy=False)
```

Staying lazy is what makes a window cheap: polars pushes the date predicate into
the parquet scan, so `load_option_greeks('AAPL', date(2025, 1, 1), date(2025, 3,
31))` is ~0.04s against 141k rows, where the full nine-year history is 0.46s and
4.4M. Compose with `lazy=True` and collect once at the end.

```python
chain = dal.load_option_greeks('AAPL', start, end, lazy=True)
liquid_df = chain.filter(pl.col('bid') > 0).group_by('date').len().collect()
```

## What is loadable

| Call | Covers | Grain |
| --- | --- | --- |
| `load_universe()` | 2017–2025 | `date`, `ticker` — point-in-time S&P 500 membership |
| `load_underlying()` | 2023-06–2025 | `date`, `symbol` — EOD OHLCV plus closing bid/ask |
| `load_option_greeks(symbol)` | 2017–2025 | one symbol's EOD chain: greeks, IV, `underlying_price` |
| `load_open_interest(symbol)` | 2017–2025 | one symbol's EOD open interest |
| `load_indices()` | 2024–2025 | SPX, RUT, OEX, XSP and the VIX complex (`VIX1D`…`VIX1Y`, `VVIX`, `SKEW`) |
| `load_yields()` | 2017–2025 | CBOE treasury yield indices: `13w`, `5y`, `10y`, `30y` |
| `load_rates()` | 2024–2025 | overnight SOFR |
| `load_earnings()` | 1999–2026 | announcement dates with a `bmo`/`amc`/`unknown` session |
| `load_corporate_actions()` | 2017–2026 | splits and dividends, ex-date |
| `load_symbology_check()` | 2017–2025 | per-symbol-year verdict on whether the chain is the right company |

`load_universe` and `load_underlying` each read their current and historical
files together — the pair does not overlap, so the window alone decides what you
get and there is no history flag to remember. 2023-06 is the stock tier's floor,
which is why the chain loaders reach the full option history and the stock
loader cannot: `underlying_price` rides on every chain row.

`load_symbology_check` is the one loader with no window, because it is keyed on
(year, symbol) rather than date. It is 4,613 rows; filter it yourself.

519 symbols have 2025 chains; index roots (`SPX`, `SPXW`, `VIX`, `XSP`) live in
their own directory behind `index=True`. `available_option_symbols(year=...)`
and `available_years(dataset)` list what is there. The option loaders derive
which year directories to open from the window, so an open-ended call reads
everything on disk and a one-quarter call opens one directory.

## Transforms: what used to be a loader flag

The joins that a loader used to do behind a boolean are now named functions you
apply yourself. Each takes and returns **whichever of DataFrame or LazyFrame you
hand it**, so they compose either way.

| Transform | Adds |
| --- | --- |
| `filter_to_universe(prices)` | semi-join to index membership, mapping Wikipedia tickers to ThetaData's |
| `with_corporate_actions(prices)` | `split_ratio` (null → 1.0) and `dividend` (null → 0.0) |
| `with_earnings_distance(prices)` | `days_to_earnings`, `days_since_earnings`, calendar days |

And two expressions in [transforms.py](transforms.py), so every study computes
these the same way: `split_adjusted_return()` and `realized_volatility(col,
window)`.

A universe panel with split-adjusted returns and 20-day realized vol:

```python
prices_df = dal.load_underlying(date(2025, 1, 1), date(2025, 12, 31))
panel_df = dal.with_corporate_actions(
    dal.filter_to_universe(prices_df).filter(pl.col('close') > 0)
).sort('symbol', 'date').with_columns(
    dal.split_adjusted_return().over('symbol'),
    dal.realized_volatility('close', 20).over('symbol').alias('rv_20'),
)
```

A clean 20–40 DTE call chain:

```python
chain_df = dal.load_option_greeks('AAPL', date(2025, 1, 1), date(2025, 3, 31)).with_columns(
    ((pl.col('bid') + pl.col('ask')) / 2).alias('mid'),
    (pl.col('expiration') - pl.col('date')).dt.total_days().alias('dte'),
    (pl.col('strike') / pl.col('underlying_price') - 1).alias('moneyness'),
).filter(
    pl.col('right') == 'CALL',
    pl.col('dte').is_between(20, 40),
    pl.col('moneyness').abs() <= 0.1,
    pl.col('iv_error').abs() <= 1.0,
    pl.col('bid') > 0,
)
```

## Gotchas worth knowing before you trust a number

Because the loaders no longer screen anything, these are now yours to apply.

**Delisted names carry a zero row.** ThetaData emits a final row with
open = high = low = close = 0, sometimes with real volume attached, rather than
omitting it. Left in, it books a -100% day. `filter(pl.col('close') > 0)`.

**Rows exist before a symbol was listed.** SOLS has four Jan–Apr 2025 rows priced
at $0.0001, with volume, months before it began trading on 2025-10-30.
`filter_to_universe` removes them.

**`implied_vol` is only as good as `iv_error`.** About 3% of contract-days fail
to invert and come back pinned near 0.5 with an error of ±100. Screen on
`pl.col('iv_error').abs() <= 1.0` or tighter. Separately, contracts with no quote
are present, and that is also where `implied_vol` is 0.0 rather than null.

**The chain's session stamp is `underlying_timestamp`, not `timestamp`.** That is
what `date` is derived from: the stamp on the spot print the greeks were struck
against, defined even for a contract that never traded. `timestamp` is the
contract's own last trade.

**Open interest is settled and one day stale.** Stamped pre-open (~06:30 ET), it
reports the position standing after the *previous* close — which is what a trader
forming at today's close actually knows. So `date` joins straight onto a chain
row for the same session with no shift.

**Nothing checks that a symbol is the company the universe names.** Ask for META
in 2021 and you get a $15 stock, silently. Screen a multi-year sample:

```python
panel_df.join(dal.usable_symbol_years(), on=['symbol', 'year'], how='semi')
```

`usable_symbol_years()` excludes only `wrong_instrument` (18 of 4,613
symbol-years). It deliberately keeps `thin_overlap`, which means the check *could
not run* — that status falls disproportionately on names later delisted or
acquired, so excluding it is a survivorship filter, not a quality one. The cost
is that those names' split adjustments are unverified. `untrusted_symbol_years()`
returns the stricter set as a plain `{(year, symbol)}`, and `TRUSTED_OVERRIDES`
in [quality.py](quality.py) is where a verdict is overturned by hand for the
cases where the *reference* is wrong rather than the data (2017 COL is genuinely
Rockwell Collins; Yahoo's modern COL is an unrelated shell).

**`corporate_action_symbol_years()` is a separate warning.** Those 60
symbol-years hold a day the vendors disagree about by more than 5% — usually an
unadjusted spinoff. The symbol is right; the return on that one day is not.

**A missing verdict does not empty your study.** `untrusted_symbol_years()`
returns an empty set when `symbology_check.parquet` has never been built, on
purpose. Run `data_pipelines.symbology` rather than trusting the silence.

**A missing dataset tells you how to build it.** Every loader raises
`MissingDataset` carrying the exact pipeline command, and the option loaders also
name the years the store does hold.

## The modules

Dependencies run one way down this list; there are no cycles.

| Module | Holds |
| --- | --- |
| [paths.py](paths.py) | where each dataset lives, which years are on disk. The only place a `data_store/` path is spelled |
| [errors.py](errors.py) | `MissingDataset`, `UntrustedSymbolYear` |
| [filters.py](filters.py) | `require`, `in_window`, `deliver` — the three things every loader does |
| [quality.py](quality.py) | the symbology verdict and the screens built on it |
| [equities.py](equities.py) | stock prices, corporate actions, `with_corporate_actions` |
| [universe.py](universe.py) | index membership, `filter_to_universe` |
| [events.py](events.py) | earnings dates, `with_earnings_distance` |
| [reference.py](reference.py) | index levels, yields, rates |
| [options.py](options.py) | per-symbol chains, open interest, `spot_series` |
| [transforms.py](transforms.py) | split-adjusted return and realized vol as expressions |

## Adding a loader

1. Add the path to [paths.py](paths.py) and to the `DATASETS` map, so
   `describe_store()` reports it.
2. Put the loader in the module matching what it reads, and give it the
   `(start=None, end=None, lazy=False)` signature — `pl.scan_parquet` over a
   `require(path, pipeline)`, then `in_window`, then `deliver`. Resist adding a
   filter argument; a caller with `lazy=True` gets the same pushdown for free.
3. If it needs a non-trivial join, write it as a separate `with_*` or `filter_*`
   transform that takes and returns either frame type, rather than a loader flag.
4. Export it from `__init__.py` and add it to `__all__`.
