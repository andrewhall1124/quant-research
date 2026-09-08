# Signals and alphas

## Surface (`providers/surface.py`)

`build_surface_panel(options_lf, SurfaceConfig(), earnings_adjuster=None)` →
`(date, symbol, iv30, iv60, iv90)`, lazily.

1. **Pillars.** For each `(date, symbol, expiration)`: among contracts with
   non-null `iv`, `bid > 0`, `ask > bid` and `|delta|` in `delta_band`
   (0.25–0.75), and `dte` in `[min_dte, max_dte]` (7–400), take the call and
   the put nearest `|delta| = 0.50` (ties broken by strike) and average
   their IVs. Both rights are required (`require_both_rights`).
2. **Earnings hook.** `EarningsAdjuster.adjust(pillars_lf) -> pillars_lf`
   runs here, on `(date, symbol, expiration, dte, atm_iv)`. The default is
   `NoOpEarningsAdjuster`; `providers/earnings.py: TermStructureEarningsAdjuster`
   strips the jump variance of every announcement a pillar spans, estimated
   from the pillars on either side of the event (`estimate_jump_variance`),
   with the name's own trailing median as the fallback. The companion
   `compute_close_to_close_panel(..., exclude_df=events)` drops the event
   sessions from realized vol. `research/earnings_adjusted_vrp` measures
   what the adjustment does to the VRP signal: the raw signal's calendar
   loading goes away, the decile-10 effect and the book P&L do not change.
3. **Interpolation.** Pillars are converted to total variance
   `iv² × dte / 365`, linearly interpolated in `dte` to each tenor, and
   converted back. A tenor outside the pillar range is **null** unless
   `extrapolate=True`, which holds the nearest pillar's IV flat (never a
   slope — term structures invert and a slope out of the last two pillars
   gives negative variance too easily). `utils/interpolation.py`.

`tests/test_surface.py`: a flat synthetic surface returns the input IV at
every tenor to 1e-12; total-variance interpolation is linear in variance,
not in vol.

`ConstantMaturitySurface.from_store(paths, symbols, start, end)` collects the
panel; the demos use `materialize_by_symbol` instead to bound memory.

## Realized vol (`providers/realized_vol.py`)

Two panel builders with identical output `(date, symbol, rv_1, rv_5, rv_22, rv_fwd)`,
all annualised **vols**:

* `compute_realized_vol_panel(stocks_lf)` — **Yang-Zhang** from OHLC:
  `σ² = σ_overnight² + k σ_close² + (1-k) σ_RS²`, `k = 0.34 / (1.34 + (n+1)/(n-1))`,
  over trailing windows of 1, 5 and 22 sessions (for `n = 1` the variances
  collapse to the day's squared overnight return plus Rogers-Satchell).
  Needs the stock file (2023-06 on). Split-adjusted when `split_ratio` is
  present.
* `compute_close_to_close_panel(closes_lf)` — mean squared split-adjusted
  log return over the same windows, from `(date, symbol, close)`. Fed by
  `scan_spot_from_chains`, it reaches 2017.

`rv_fwd` on date `d` is the estimator over sessions `d+1 .. d+60`, null
until that window has closed. It is the HAR target.

## HAR forecast

`HARForecaster(HARConfig(windows=(1,5,22), forward_window=60, min_fit_rows=1000, refit="monthly"))`
implements `RealizedVolForecaster.forecast(panel_df, apply_df=None) -> (date, symbol, rv_fcst)`.

Pooled across names: `log(rv_fwd²) ~ 1 + log(rv_1) + log(rv_5) + log(rv_22)`
by OLS. On the first session of each month the fit is redone on every row
whose date is at least `forward_window` sessions before the refit date —
those are the rows whose target is fully observed. Between refits the last
coefficients apply to each day's trailing RVs. The forecast is
`sqrt(exp(x'β + σ_e²/2))`, the lognormal correction turning a log-variance
prediction into a variance.

`apply_df` rows are scored but never fitted; that is how SPX gets a forecast
from the single-name regression.

**No lookahead** is asserted in `tests/test_realized_vol.py`: rows after a
cutoff are shuffled and scaled sevenfold, and every forecast dated on or
before the cutoff is unchanged to 1e-12.

The coefficient on `rv_22` is about 0.9 on this store with small weights on
the 1- and 5-session terms; the residual variance in log space is 0.2–0.45.
`HARForecaster.coefficients_df` keeps every refit.

## Stock features (`providers/stock_features.py`)

`compute_stock_features(stocks_lf, market_df)` → `(date, symbol, ret, beta, idio_vol, log_size, gap_freq)`:

| column | definition |
| --- | --- |
| `ret` | split-adjusted log return |
| `beta` | rolling 250-session OLS beta on the SPX log return |
| `idio_vol` | 60-session std of `ret - beta × market`, annualised |
| `log_size` | log of the 60-session mean of `close × volume`; **null** when there is no volume (chains) |
| `gap_freq` | share of the trailing 250 sessions with `|ret| > 3 × trailing-60 std` |

The SPX return comes from the index chain's `underlying` column
(`load_spx_closes`), which reaches 2017. `log_size` is a **proxy** for market
cap — the store has no shares outstanding — and is flagged as such wherever
it is used.

## The VRP signal (`CrossSectionalVRPSignal`)

```
vrp = ln(iv60) - ln(rv_fcst)
```

is regressed cross-sectionally every session, with intercept, on:

| regressor | from | why |
| --- | --- | --- |
| `market = beta × spx_vrp` | stock beta × SPX's own `ln(iv60) - ln(rv_fcst)` | a market-wide premium is not name richness. Falls back to `beta × mean vrp` when no SPX surface is given (`used_spx` records which) |
| `sector_median` | median `vrp` of the name's GICS sector that day | same, for the sector — the same factor set as the risk model, via `FactorSpec` |
| `log_size` | stock features | size control (dropped on the long-history path) |
| `idio_vol` | stock features | a high-idio name carries a structurally higher premium |

The residual is averaged over `smoothing_days` (5) sessions per name, then
z-scored across names each session and clipped at ±`winsor` (3). A session
with fewer than `min_names` (50) complete rows produces no scores.

`SignalConfig(tenor=60, smoothing_days=5, controls=("log_size", "idio_vol"), winsor=3.0, min_names=50)`.

`factor_correlations(date)` returns the cross-sectional correlation of the
residual with each regressor; `tests/test_signals.py` asserts they are zero
to 1e-8 on a synthetic panel with known factor structure.

**Sign.** Positive means implied is *rich* relative to the forecast after the
controls. The signal is left as richness; the alpha flips it.

## The time-series IV z-score (`TimeSeriesIVZScoreSignal`)

Each name's `log(iv60)` against its own trailing `window` (250) sessions:
`(x - mean) / std`, needing `min_periods` (120) observations, clipped at
±3. No forecast, no regression, no cross-sectional standardisation — it is
the classic "IV is high for this name" mean-reversion score, and a control
for the residualised premium. `TimeSeriesZScoreConfig(tenor=60, window=250, min_periods=120, winsor=3.0, use_log=True)`.

Over seven years the two behave differently; see [experiments.md](experiments.md).

## Past reference returns (`PastReturnSignal`)

Heston, Jones, Khorram, Li and Mo (2023): past delta-hedged straddle
returns predict future ones. The reference panel already holds that return
per name per day, so the signal is
`PastReturnConfig(window=252, skip=21, sign=-1)`: the sum of `pnl_per_vega`
over `window` sessions ending `skip` sessions ago, z-scored across names
each session and clipped at ±3. `sign=-1` puts it in the richness
convention for momentum (a high past return is *cheap*, the book buys it);
`sign=+1` is reversal. `min_periods` defaults to half the window.
`research/vol_momentum` and `research/vol_reversal` report what each
window earns.

## Alpha (`providers/alphas.py`)

```
alpha_i = -IC × sigma_idio_i × z_i
```

`ICScaledAlpha(signal_provider, risk_model_constructor, AlphaConfig(ic=0.04, short_rich=True))`.
`sigma_idio` is the risk model's idio vol for that date, so the alpha is in
per-vega P&L per day, the unit the optimizer's risk term is in (see
[units.md](units.md)). Names without a risk-model estimate get no alpha and
are not traded. `short_rich=False` keeps the raw sign.

## Adding a signal

Any `PanelProvider` with `(date, symbol, signal)` works as a
`SignalProvider`; `ICScaledAlpha` and the decile table take it unchanged.
The demo's `run_book(args, panels, signal, tag)` runs a full book on it. See
[extending.md](extending.md).
