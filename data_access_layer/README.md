# Data access layer

Every dataset in `data_store/`, behind one import. Pipelines write, this layer
reads; research code should never open a parquet path itself.

```python
import data_access_layer as dal

prices_df = dal.load_underlying(in_universe=True, with_actions=True)
chain_df = dal.load_option_greeks('AAPL', min_dte=20, max_dte=40, max_iv_error=1.0)
```

Import names from the package, not from a submodule. The file split is an
implementation detail and a loader may move between modules.

Start by seeing what is actually on disk:

```sh
uv run python -c "import data_access_layer as dal; print(dal.describe_store())"
```

## The shape everything comes back in

Every loader returns an **eager polars DataFrame** with a `date` column of dtype
`Date`, whatever the raw file carries, sorted. Filtering happens lazily inside,
so asking for one symbol out of a 1.5 GB chain directory reads only that
symbol's row groups — `load_option_greeks('AAPL')` is ~0.06s, not a full scan.

The same three arguments mean the same thing everywhere: `symbols` takes a
string or a list, `start` and `end` are inclusive `date` bounds, and `None`
means no filter.

## What is loadable

| Call | Covers | Grain |
| --- | --- | --- |
| `load_universe()` | 2025 | `date`, `ticker` — point-in-time S&P 500 membership |
| `load_underlying()` | 2025 | `date`, `symbol` — EOD OHLCV plus closing bid/ask |
| `load_option_greeks(symbol)` | 2017–2025 | one symbol's EOD chain: greeks, IV, `underlying_price` |
| `load_open_interest(symbol)` | 2017–2025 | one symbol's EOD open interest |
| `load_indices()` | 2024–2025 | SPX, RUT, OEX, XSP and the VIX complex (`VIX1D`…`VIX1Y`, `VVIX`, `SKEW`) |
| `load_yields()` | 2017–2025 | CBOE treasury yield indices: `13w`, `5y`, `10y`, `30y` |
| `load_rates()` | 2024–2025 | overnight SOFR |
| `load_earnings()` | 1999–2026 | announcement dates with a `bmo`/`amc`/`unknown` session |
| `load_corporate_actions()` | 2017–2026 | splits and dividends, ex-date |
| `load_symbology_check()` | 2017–2025 | per-symbol-year verdict on whether the chain is the right company |

519 symbols have 2025 chains; index roots (`SPX`, `SPXW`, `VIX`, `XSP`) live in
their own directory behind `index=True`. `available_option_symbols(year=...)`
and `available_years(dataset)` list what is there.

Two loaders reach further back than their default with a flag, because the
extra years are reconstructed differently and are opt-in rather than free:
`load_universe(with_history=True)` goes to 2017, and
`load_underlying(with_history=True)` to 2023-06 (the stock tier's floor —
which is why the chain loaders, whose `underlying_price` rides on the row,
reach the full option history and the stock loader cannot).

## The five things you will actually do

**A panel of stock returns, split-adjusted, universe-filtered.**
`in_universe=True` also drops the pre-listing rows ThetaData serves for a
not-yet-trading symbol at $0.0001.

```python
prices_df = dal.load_underlying(in_universe=True, with_actions=True)
prices_df = prices_df.with_columns(
    dal.split_adjusted_return().over('symbol'),
    dal.realized_volatility('close', 20).over('symbol').alias('rv_20'),
)
```

**A clean option chain.** `max_iv_error` is not optional in practice: about 3%
of contract-days fail to invert and come back pinned near 0.5 with an error of
±100. `quoted_only` drops contracts with no quote, which is also where
`implied_vol` returns 0.0 rather than null.

```python
chain_df = dal.load_option_greeks(
    'AAPL', min_dte=20, max_dte=40, max_moneyness=0.1,
    max_iv_error=1.0, quoted_only=True, rights='CALL',
)
```

`moneyness` (`strike / underlying_price - 1`), `dte`, `mid` and `date` are
derived for you. The session stamp is `underlying_timestamp`, not `timestamp` —
the former is the spot print the greeks were struck against, defined even for a
contract that never traded.

**The full option history for one name.** The `years` default is the 2025
sample, not everything on disk, so landing a backfill year never silently
changes what an existing study loads:

```python
chain_df = dal.load_option_greeks('AAPL', years=None)        # 2017–2025
chain_df = dal.load_option_greeks('AAPL', years=[2023, 2024])
```

**Separating a vol signal from an earnings signal.** A cross-sectionally ranked
IV signal will sort largely on days-to-earnings unless it is controlled for:

```python
panel_df = dal.with_earnings_distance(prices_df)  # + days_to_earnings, days_since_earnings
```

**Screening a multi-year sample.** `usable_symbol_years()` is the
survivorship-safe filter: it excludes only `wrong_instrument`, keeping
`thin_overlap` (the check *could not run*, which falls disproportionately on
names that were later delisted — excluding them is a survivorship filter, not a
quality one).

```python
pairs_df = dal.usable_symbol_years([2023, 2024, 2025])
```

## Gotchas worth knowing before you trust a number

**A condemned symbol-year raises, it does not silently drop.** Asking for META
in 2021 and getting a $15 stock is a wrong answer; asking and getting an empty
frame without being told is a different wrong answer.

```python
dal.load_option_greeks('COR', years=2017)
# UntrustedSymbolYear: COR is not the company the universe names in [2017]
dal.load_option_greeks('COR', years=2017, trusted_only=False)  # raw record, on purpose
```

Of 4,613 symbol-years checked, 18 are `wrong_instrument` and 2 `suspect`.
`untrusted_symbol_years()` returns the refused set; `TRUSTED_OVERRIDES` in
[quality.py](quality.py) is where a verdict is overturned by hand, for the cases
where the *reference* is wrong rather than the data (2017 COL is genuinely
Rockwell Collins; Yahoo's modern COL is an unrelated shell).

**Open interest is settled and one day stale.** It is stamped pre-open (~06:30
ET) and reports the position standing after the *previous* close — which is what
a trader forming at today's close actually knows. So `date` joins straight onto
a chain row for the same session with no shift.

**`corporate_action_symbol_years()` is a separate warning from the symbology
check.** Those symbol-years hold a day the vendors disagree about by more than
5% — usually an unadjusted spinoff. The symbol is right; the return on that one
day is not.

**A missing verdict does not empty your study.** `untrusted_symbol_years()`
returns an empty set when `symbology_check.parquet` has never been built, on
purpose. Run `data_pipelines.symbology` rather than trusting the silence.

**A missing dataset tells you how to build it.** Every loader raises
`MissingDataset` carrying the exact pipeline command, so you never have to go
find out which script writes which file.

## The modules

Dependencies run one way down this list; there are no cycles.

| Module | Holds |
| --- | --- |
| [paths.py](paths.py) | where each dataset lives, which years are on disk. The only place a `data_store/` path is spelled |
| [errors.py](errors.py) | `MissingDataset`, `UntrustedSymbolYear` |
| [filters.py](filters.py) | `require`, `in_window`, `only_symbols` — the shared filtering vocabulary |
| [quality.py](quality.py) | the symbology verdict, and the guard the option loaders call |
| [universe.py](universe.py) | point-in-time index membership |
| [equities.py](equities.py) | stock prices and corporate actions |
| [events.py](events.py) | earnings dates and distance to them |
| [reference.py](reference.py) | index levels, yields, rates |
| [options.py](options.py) | per-symbol chains and open interest |
| [transforms.py](transforms.py) | split-adjusted return and realized vol, computed one agreed way |

## Adding a loader

1. Add the path to [paths.py](paths.py) and to the `DATASETS` map, so
   `describe_store()` reports it.
2. Put the loader in the module that matches what it reads, and give it the
   `symbols` / `start` / `end` signature by composing `only_symbols` and
   `in_window` over a `pl.LazyFrame`. Wrap the path in `require(path, pipeline)`
   so a missing file names the command that builds it.
3. Export it from `__init__.py` and add it to `__all__`.

Per-symbol datasets that grow a directory per year go through
`resolve_option_paths`, which defaults `years` to `paths.SAMPLE_YEAR` — keep
that default, and let a caller pass `years=None` to opt into the full history.
