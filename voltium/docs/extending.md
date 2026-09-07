# Extending

The ABCs were laid out so that the term-structure and skew books, and an
earnings adjustment, are additions rather than rewrites. This page is the
checklist for each.

## An earnings adjuster

`providers/surface.py: EarningsAdjuster.adjust(pillars_lf) -> pillars_lf`,
on `(date, symbol, expiration, dte, atm_iv)`, before interpolation.

1. Join the next announcement on or after `date` per symbol
   (`loaders.scan_earnings` gives `(symbol, date, session)`; an `amc`
   announcement moves the *next* session's close-to-close).
2. Estimate the jump variance `J` per name — the historical mean squared
   earnings-day move, or implied from the front-month vs the next.
3. For every expiration after the announcement, replace `atm_iv` with
   `sqrt(atm_iv² − J × 365 / dte)`, floored at a minimum.
4. Pass the instance to `build_surface_panel(..., earnings_adjuster=...)`.

The signal then compares a clean diffusion IV with the RV forecast, and the
book stops selling every name a week before it reports. The reference
returns are unaffected (they are P&L, not IV), so the risk model needs no
change.

## A term-structure book

A calendar spread is an `Instrument` whose unit has two legs in different
expirations with opposite signs.

1. Subclass `Instrument`. `select` returns
   `UnitSpec(symbol, legs=(front_call, front_put, back_call, back_put), ratios=(-1, -1, 1, 1))`
   — `UnitSpec.ratios` and multi-leg `legs` are already there; the straddle
   just uses `(1, 1)`. Pick the two monthlies nearest, say, 30 and 90 days.
2. `mark` sums `ratio × mid` across legs (and likewise delta, vega, theta),
   so `UnitMark.vega` is the *net* vega of the spread. Decide whether a
   "dollar vega" of a calendar should be net vega (small, sign-flipping) or
   the front leg's vega; the optimizer only needs it to be consistent with
   the reference returns.
3. `should_roll` on the front leg's DTE; `hedge_shares` unchanged.
4. Run `data_pipelines.cli vol-risk-model --target-dte ...` with the new
   instrument (add a flag selecting it), or use
   `StraddleReturnsProvider.from_store` with it in process. The risk model
   is then a covariance of calendar-spread P&L per unit of its vega.
5. The signal is the term slope from the surface panel: `ln(iv90) − ln(iv30)`
   residualised the same way; `CrossSectionalVRPSignal` with a different
   `compute_vrp` is fifteen lines.

The backtester, trade generator, portfolio and results need no change: they
hold `UnitSpec`s and `UnitMark`s and never look inside.

## A skew book

A risk reversal: `legs=(otm_put, otm_call)`, `ratios=(1, -1)` at, say,
25-delta. `select` needs the chain rows' delta (already canonical) to pick
strikes; the vendor's delta is per share and signed. The signal is the
25-delta put IV minus call IV from a widened surface builder (extend
`compute_atm_pillars` with a `target_delta` argument). Same steps as above.

## A signal

Any `PanelProvider` with `(date, symbol, signal)`; `ICScaledAlpha` and
`BacktestResults.decile_table` consume it unchanged, and the demo's
`run_book` runs a book on it. If it should be orthogonal to the risk
factors, residualise against `FactorSpec.factor_names(...)` the way
`CrossSectionalVRPSignal` does, and add a test like
`test_residuals_are_uncorrelated_with_each_factor`.

## A risk model

Subclass `RiskModel` with `symbols`, `factors`, `covariance`, `loadings`,
`idio_vol`, and `RiskModelConstructor.get_risk_model(date)`. The optimizer
uses all five; `FactorNeutral` needs `loadings` and `factors`, `ICScaledAlpha`
needs `idio_vol`. A model with no factors returns `(n, 0)` loadings and an
empty factor list, and `FactorNeutral` becomes a no-op.

To add a factor to the existing model — say a size-vol factor — extend
`build_factor_returns` and `FactorSpec.factor_names`; both the risk model and
the signal pick it up because they read the same spec.

## A cost model

`CostModel.compute(quantity, bid, ask, price) -> dollars`, per fill. A
volume-aware model can read the chain through the trade generator (it has
`quotes_df`) if `Trade` is extended to carry the leg volumes; today the
backtester passes only the straddle quote.

## A trading screen

Subclass `TradingConstraint.allow(quotes_df, unit, mark, is_opening) -> bool`
and pass the list to `TradeGenerator(constraints=[...])`. Screens run only
on opening or adding.

## An objective term or constraint

`Objective.build(weights, **kwargs) -> expression to maximise`,
`OptimizerConstraint.build(weights, **kwargs) -> constraint | list`. The
kwargs are fixed in `optimizer/base.py`; to pass something new, add it to
`MVO.optimize` and document it there. A multi-period turnover term (alpha
decay against amortised cost) is the most valuable addition; see
[optimizer.md](optimizer.md).

## A market-cap size control

`stock_features.py` computes `log_size` from dollar volume because the store
has no shares outstanding. With a `(date, symbol, market_cap)` panel, join it
in `compute_stock_features` and the signal's `controls` pick it up by name.

## Checklist for any change to the instrument

1. `tests/test_backtester.py` still passes (theta, delta, restart).
2. Rebuild the reference returns: `vol-risk-model --force` (stored) or
   delete `voltium/.cache/reference/` (in process; the config hash does this
   for you).
3. Re-estimate: the pipeline does it in the same run.
4. Rerun the decile table: it is the fastest check that the signal still
   orders forward P&L under the new marking.
