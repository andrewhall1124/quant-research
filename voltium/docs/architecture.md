# Architecture

## The pipeline

```
ThetaData files (data_store/)
        │
        ▼
loaders.py ──────────── canonical option / stock / universe / sector frames (lazy)
        │
        ├──► providers/surface.py ─────────── iv30 / iv60 / iv90 per (date, symbol)
        ├──► providers/realized_vol.py ────── rv_1 / rv_5 / rv_22 / rv_fwd, HAR forecast
        ├──► providers/stock_features.py ──── beta, idio_vol, log_size, gap_freq
        ├──► providers/straddle_returns.py ── per-unit-vega reference P&L (via the backtester)
        │            │
        │            ▼
        │    risk_model/ ──────────────────── FactorRiskModel: Sigma = B F B' + D²
        │            ▲
        │            │  data_pipelines.cli vol-risk-model writes the tables; risk_model/store.py reads them
        │
        ├──► providers/signals.py ─────────── z-score per (date, symbol)
        │            │
        │            ▼
        │    providers/alphas.py ──────────── alpha = -IC · sigma_idio · z
        │            │
        │            ▼
        │    optimizer/ ───────────────────── target dollar vega per symbol (CVXPY)
        │            │
        │            ▼
        │    trade_generator.py ───────────── straddle contracts (integer or fractional), screened
        │            │
        ├──► backtester.py ◄── instruments/straddle.py, costs.py, portfolio.py   (chain engine)
        │            │
        │    panel_backtester.py ◄── the reference panel                         (panel engine)
        │            │
        └────────────┴──► results.py ────────── summary, factor regression, decile table, plots
```

Everything left of the backtester is a **panel**: a small frame with one row
per `(date, symbol)`, built once from lazy scans and held in memory behind a
`Provider.get(date)`. There are two backtest engines over the same
`Strategy`. The **chain engine** scans the option chain one session at a
time and marks every leg; it is the ground truth and the only one that
knows about integer contracts and liquidity screens. The **panel engine**
holds fractional units of the stored reference straddle per name and reads
P&L, vega and costs off the reference panel, which makes a seven-year book
a matter of seconds plus the strategy calls; it reproduces the chain
engine's fractional, unscreened numbers to 1e-6 (see
[instrument_and_backtester.md](instrument_and_backtester.md)).

## Module map

```
src/voltium/
  loaders.py                 ThetaData → canonical schema; DataPaths; vega-convention check
  providers/
    base.py                  Provider / PanelProvider ABCs; materialize, materialize_by_symbol
    calendar.py              CalendarProvider ABC; TradingCalendar
    universe.py              UniverseProvider, PointInTimeUniverse; SectorProvider, StaticSectorProvider
    surface.py               SurfaceConfig, EarningsAdjuster / NoOpEarningsAdjuster, build_surface_panel,
    earnings.py              load_earnings_events, estimate_jump_variance, TermStructureEarningsAdjuster
                             SurfaceProvider, ConstantMaturitySurface
    realized_vol.py          Yang-Zhang and close-to-close panels; RealizedVolForecaster ABC; HARForecaster
    stock_features.py        compute_stock_features; StockFeaturesProvider; load_spx_closes
    chain.py                 ChainProvider ABC; StoreChainProvider (one lazy scan per session); FrameChainProvider
    straddle_returns.py      compute_reference_path; StraddleReturnsProvider; StoredStraddleReturns
    signals.py               SignalProvider ABC; CrossSectionalVRPSignal; TimeSeriesIVZScoreSignal; PastReturnSignal
    alphas.py                AlphaProvider ABC; ICScaledAlpha
  instruments/
    base.py                  LegSpec, UnitSpec, UnitMark; Instrument ABC
    straddle.py              StraddleConfig; DeltaHedgedStraddle
  risk_model/
    spec.py                  FactorSpec — the one factor definition
    base.py                  RiskModel / RiskModelConstructor ABCs
    factor.py                build_factor_returns, ledoit_wolf, FactorRiskModel, one-date and rolling estimators
    constructor.py           FactorRiskModelConstructor (estimates in process)
    store.py                 Factor{Loadings,Covariances}Provider, IdioVolProvider, StoredFactorRiskModelConstructor
  optimizer/
    base.py                  Optimizer / Objective / OptimizerConstraint ABCs; OptimizationError
    objectives.py            MaxUtility, TurnoverPenalty, JumpPenalty
    constraints.py           NetVegaNeutral, FactorNeutral, PerNameVegaCap, GrossShortVegaCap, GrossVegaCap
    mvo.py                   MVO
    strategy.py              OptimizationStrategy
  strategy.py                Strategy, ScoreStrategy ABCs; RankWeightedStrategy; FixedTargetStrategy; ReferenceUnitStrategy
  trade_generator.py         Trade, TradeGeneratorConfig, TradingConstraint ABC, MaxSpread, MinOpenInterest, TradeGenerator
  costs.py                   CostModel ABC; NoCost, HalfSpreadCost, PerShareCost
  portfolio.py               Position, Portfolio (JSON round-trip)
  backtester.py              BacktestConfig, Backtester, RECORD_SCHEMA          (chain engine)
  panel_backtester.py        PanelBacktestConfig, PanelBacktester, VegaBook      (panel engine)
  results.py                 BacktestResults
  utils/
    bs.py                    Black-Scholes price and greeks (vectorised)
    interpolation.py         total-variance interpolation to constant maturity
```

Dependencies run downward in this list, with two exceptions that are
deliberate: `providers/straddle_returns.py` imports the backtester (it *runs*
it), and `optimizer/strategy.py` imports providers and the risk model (it
wires them). Nothing imports `demos/`.

## What carries over from atium

| atium | voltium | notes |
| --- | --- | --- |
| `Provider.get(date_) -> DataFrame` (Protocols) | `Provider` ABC, `PanelProvider` | same call shape; ABC so the panel-backed providers share one implementation |
| `CalendarProvider.get(start, end)` | `CalendarProvider.get(start, end)` | plus `shift`, `next`, `previous` on `TradingCalendar` |
| `AlphaProvider`, `FactorLoadingsProvider`, `FactorCovariancesProvider`, `IdioVolProvider` | same names, `providers/alphas.py`, `risk_model/store.py` | the risk-model providers read pipeline output, as in atium |
| `RiskModel.build_covariance_matrix()` / `tickers` | `RiskModel.covariance(symbols)` / `symbols`, plus `loadings`, `idio_vol`, `factors` | the optimizer needs loadings for factor neutrality and idio vol for alpha scaling |
| `RiskModelConstructor.get_risk_model(date)` | same | two implementations: in process, or from the store |
| `Objective.build(w, **kwargs)` | same | kwargs vocabulary fixed in `optimizer/base.py` |
| `OptimizerConstraint.build(w, **kwargs)` | same | |
| `MVO(objective, constraints)` | `MVO(objectives, constraints, solver)` | a list of objective terms, summed; raises on non-optimal status instead of returning zeros |
| `Strategy.generate_weights(date)` | `Strategy.generate_targets(date, chain_df, portfolio)` | see below |
| `TradeGenerator(constraints).apply(weights, capital)` | `TradeGenerator(instrument, config, constraints).generate(...)` | see below |
| `CostModel.compute_costs(old_w, new_w, capital)` | `CostModel.compute(quantity, bid, ask, price)` | see below |
| `Backtester.run(...) -> PositionResults` | `Backtester.run(portfolio) -> (records_df, Portfolio)` | restartable |
| `factor_model.estimate_factor_model` (RollingOLS) | `risk_model/factor.py` rolling estimators, run by a pipeline | |

## Where the equity abstraction stops

Three things have no equity counterpart, and each says so in its docstring.

**`Instrument`** (`instruments/base.py`). In atium a unit of exposure is a
share; here it is a bundle of listed legs plus a stock hedge that must be
*chosen* from a chain (which expiration, which strike), that decays, and
that has to be rolled. `Instrument.select / mark / should_roll / hedge_shares`
own those decisions. The backtester and the reference-return builder both go
through it, so marking logic exists in one place.

**`Strategy.generate_targets(date, chain_df, portfolio)`** (`strategy.py`).
The strategy sees today's chain and the current book because a vol target
only makes sense for a name with a tradeable straddle today, and a held
series is never swapped outside a roll.

**`CostModel.compute(quantity, bid, ask, price)`** (`costs.py`). Per fill,
not per weight vector: an option fill is priced off a quote in contracts, a
hedge fill per share. The backtester holds one model for each.

## Two design choices worth knowing up front

**Per-unit-vega returns come from the backtester itself.** The risk model
needs a daily return per name. Rather than a change in IV, it is the P&L of
holding one long unit of the instrument, delta-hedged and rolled exactly as
the book does, per dollar of vega bought — produced by running `Backtester`
with `ReferenceUnitStrategy` on each symbol. Whatever marking rule the book
uses, the risk model is estimated on the same thing. See [risk_model.md](risk_model.md).

**One `FactorSpec` for risk and signal.** The signal residualises the
variance premium against the same market and sector factors the risk model
prices, so what the optimizer is told is alpha is orthogonal to what it is
told is risk. Both read `risk_model/spec.py`. See [signals_and_alphas.md](signals_and_alphas.md).
