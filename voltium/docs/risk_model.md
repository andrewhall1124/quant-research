# Risk model

## What it prices

The optimizer trades a vector `w` of dollar vega and needs the daily
covariance of P&L per dollar of vega across names. `RiskModel`
(`risk_model/base.py`):

```python
class RiskModel(ABC):
    symbols: list[str]                          # sorted
    factors: list[str]                          # ["market", <sector>, ...]
    covariance(symbols) -> (n, n) ndarray       # daily, per-vega P&L
    loadings(symbols) -> (n, k) ndarray
    idio_vol(symbols) -> (n,) ndarray           # daily std of the residual
```

`FactorRiskModel` implements `Sigma = B F B' + D²` with `B` the loadings,
`F` the shrunk factor covariance and `D` the idio vols. `covariance` is
symmetrised; positive definiteness is checked in the tests.

## The return series

Everything is estimated on the **reference straddle return**: for each
name, the daily P&L of holding one long unit of the instrument, delta-hedged
and rolled exactly as the book does, divided by the unit's dollar vega at
inception (see [units.md](units.md)). It is produced by running the
`Backtester` itself with `ReferenceUnitStrategy` on the symbol's chain
(`providers/straddle_returns.py: compute_reference_path`), with the same
`DeltaHedgedStraddle` the book uses, no liquidity screens, full half-spread
costs recorded but not subtracted from `pnl_per_vega`.

The reference panel, one row per `(date, symbol)` (`REFERENCE_SCHEMA`):

| column | meaning |
| --- | --- |
| `pnl_per_vega` | `(option_pnl + hedge_pnl) / entry_vega`; null on a unit's first session |
| `cost_per_vega` | `(option_cost + hedge_cost) / entry_vega` — rolls and daily hedging |
| `exit_cost_per_vega` | today's half-spread to close the unit / its inception vega |
| `dollar_vega` | one unit's vega at today's close (current, not entry) |
| `entry_vega` | dollar vega, at inception, of the unit held into today |
| `dollar_theta`, `mid`, `underlying`, `dte`, `event` | as in the records |

On a roll day the P&L and both half-spreads are scaled by the *old* unit's
inception vega.

Because the same instrument and marking code produce both the book's P&L
and the reference series, the covariance the optimizer uses is a covariance
of exactly the thing the book earns. Change the roll rule and both change
together.

## Factors (`risk_model/spec.py`, `risk_model/factor.py`)

`FactorSpec` is the one definition, read by the risk model *and* the signal:

| field | default | meaning |
| --- | --- | --- |
| `market_source` | `"spx"` | the SPX reference straddle's per-vega P&L; `"universe"` is the dollar-vega-weighted mean across names |
| `use_sectors` | True | one factor per GICS sector |
| `window` | 250 | trailing sessions for every rolling estimate |
| `min_observations` | 120 | sessions a name needs before it gets an estimate |
| `shrinkage` | `"ledoit_wolf"` | or `"none"` |

`build_factor_returns(returns_df, sectors_df, spec, market_df, rolling_window)`
returns `(date, factor, ret)`:

* **market**: SPX's `pnl_per_vega` if given and `market_source == "spx"`,
  else the vega-weighted universe mean.
* **sector s**: the vega-weighted mean of its members' `pnl_per_vega`, then
  regressed on the market and replaced by the residual, so a sector loading
  measures exposure beyond the market. With `rolling_window=None` the
  orthogonalising beta is estimated over the frame handed in (the in-process
  constructor already passes one estimation window); with a window it is a
  trailing beta, which is what the pipeline uses to keep a full-history
  series point-in-time.

A name loads on the market and on **its own sector only**; every other
sector loading is zero. `FactorRiskModel.factors` is `["market"]` followed
by the sectors in sorted order.

## Estimation

Per name, OLS of `pnl_per_vega` on `[1, market, own_sector]` over the
trailing window; the slopes are the loadings, the residual standard
deviation (ddof = number of regressors + 1) is the idio vol. The factor
covariance is the sample covariance of the factor series over the same
window, shrunk with Ledoit-Wolf toward the scaled identity
(`ledoit_wolf(sample, n_obs)`; a numpy implementation of the 2004 estimator
with the usual plug-in for the fourth moments — the test checks it stays
PSD, preserves the trace and shrinks the off-diagonal mass).

Two entry points produce the same numbers:

* `estimate_factor_risk_model(window_df, factor_returns_df, sectors_df, spec)`
  — one date, on a frame the caller has already windowed.
* `estimate_rolling_loadings(...)` and `estimate_rolling_factor_covariances(...)`
  — every session, expanding until the window is full. These are what the
  pipeline writes. `tests/test_risk_model.py` checks the two agree on the
  last date of a synthetic panel to 0.1 on loadings (the tolerance covers
  the rolling-vs-window sector orthogonalisation).

With about 15 names per sector or fewer, the sector factor carries a
noticeable share of each member's idiosyncratic noise, and the recovered
idio vol comes out below the truth. The real universe has 20–80 names per
sector; the synthetic test uses 15.

## Two constructors

**In process** — `FactorRiskModelConstructor(returns_df, sectors_df, spec, market_df)`
holds the whole reference panel, windows it per date, estimates and
memoises. Use it for tests, synthetic data, and an instrument that has not
been pipelined (`demos/level_book.py --in-process`).

**From the store** — `StoredFactorRiskModelConstructor(paths, start, end)`
reads the tables below through three atium-style providers
(`FactorLoadingsProvider`, `FactorCovariancesProvider`, `IdioVolProvider`,
each a `PanelProvider` over one table) and assembles a `FactorRiskModel`
per date. It also exposes `factor_returns(date)` for the results regression.
This is the default in the demos and the faster path: one filter per
rebalance date instead of 500 regressions.

Both are memoised per date.

## The pipeline: `data_pipelines.cli vol-risk-model`

```
uv run python -m data_pipelines.cli vol-risk-model                # 2017-01-01 .. 2025-12-31, every universe member
uv run python -m data_pipelines.cli vol-risk-model --skip-reference   # re-estimate the tables only
uv run python -m data_pipelines.cli vol-risk-model --force            # rebuild reference files that exist
```

Flags: `--start`, `--end`, `--symbols`, `--limit`, `--target-dte` (60),
`--roll-dte` (20), `--window` (250).

Steps and outputs (all under `data_store/`):

| step | output | shape | time |
| --- | --- | --- | --- |
| reference returns, one file per symbol plus `SPX` | `vol_reference_returns/<SYMBOL>.parquet` | `REFERENCE_SCHEMA` | ~0.25 s per symbol-year; ~20 min for 677 symbols × 9 years; resumable (an existing file is skipped) |
| factor returns | `vol_factor_returns.parquet` | `(date, factor, ret)` | seconds |
| factor covariances | `vol_factor_covariances.parquet` | `(date, factor_1, factor_2, covariance)`, every session from the 120th | seconds |
| loadings, idio vol | `vol_factor_loadings.parquet`, `vol_idio_vol.parquet` | `(date, symbol, factor, loading)`, `(date, symbol, idio_vol)` | ~25 s |

Everything is point-in-time: the sector orthogonalisation uses a trailing
beta, every estimate at date `t` uses sessions `≤ t`. The first loadings are
on 2017-06-26 (120 sessions in), so no book can start before then.

`data_access_layer` exposes the same tables as `load_vol_reference_returns(symbol)`,
`load_vol_factor_returns()`, `load_vol_factor_loadings()`,
`load_vol_factor_covariances()`, `load_vol_idio_vol()`; the build plan runs
the step last, after the symbology check.

The reference step is the one that must be rerun when the instrument
changes (a different roll rule changes every return); the estimation steps
rerun in a minute. `StraddleReturnsProvider.from_store(..., cache_dir=...)`
keeps a per-symbol cache keyed on the instrument config for the in-process
path, for the same reason.

## Reading a loading

A market loading of 1.0 means the name's hedged straddle earns, per dollar
of vega, what the SPX straddle earns per dollar of its vega. Typical values
on this store are 0.7–1.5 on the market and 0.5–1.5 on the own-sector
factor; idio vols are 1–4 per-vega points per day, the same order as the
total, because single-name hedged straddle P&L is mostly idiosyncratic gamma
and theta.
