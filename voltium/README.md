# voltium

An options-volatility backtesting framework, sibling to
[atium](https://github.com/Atium-Research/atium). Same pipeline shape —
providers → risk model → CVXPY optimizer → trade generator → backtester with a
cost model — for a **single-name volatility level book**: cross-sectionally
long cheap implied vol and short expensive implied vol via delta-hedged ATM
straddles on the S&P 500 universe. Polars end to end, lazy until the last
moment; ABCs first, one concrete implementation each.

This is v1: daily data only, an optional earnings adjustment, no skew or term books.
The extension points for those are listed at the end.

**Documentation** lives in [`docs/`](docs/README.md): one page per layer
(architecture, data, units, instrument and backtester, risk model, signals
and alphas, optimizer, trade generator and costs, results, running,
extending) plus a log of every experiment run so far. This README is the
summary.

```sh
uv sync                                          # from the repo root; voltium is a workspace member
uv run pytest voltium/tests                      # ~2s, no data needed for most
uv run python -m data_pipelines.cli vol-risk-model   # once: reference returns + risk model into data_store/ (~25 min)
uv run python voltium/demos/level_book.py        # six months of 2025, ~10 min
```

`pip install -e voltium` also works outside uv.

## Data

The loaders read the repo's `data_store/` (ThetaData end-of-day files pulled by
`data_pipelines`). `loaders.py` is the only module that knows a vendor column
name; everything above it sees the canonical schemas below.

**Option rows**, one per `(date, symbol, expiration, strike, right)`:

| column | type | note |
| --- | --- | --- |
| `date` | Date | session, from the vendor's `underlying_timestamp` |
| `symbol` | Utf8 | option-root spelling everywhere (`BRK.B` → `BRKB`, `BNY` → `BK`) |
| `expiration` | Date | |
| `strike` | Float64 | |
| `right` | Utf8 | `"C"` / `"P"` |
| `bid`, `ask`, `mid` | Float64 | per share |
| `iv` | Float64 | decimal; null where the vendor's inversion failed (`|iv_error| > 1`) or is 0 |
| `delta`, `gamma` | Float64 | per share |
| `theta` | Float64 | per share **per calendar day** |
| `vega` | Float64 | per share **per vol point** (see below) |
| `underlying` | Float64 | spot the greeks were struck against; equals the stock close |
| `oi` | Int64 | open interest, joined from its own files; stamped pre-open, so it is the position after the previous close and joins on the same `date` |
| `volume` | Int64 | |

**Stock rows**: `date, symbol, open, high, low, close, volume` (unadjusted;
`with_splits=True` appends an ex-date `split_ratio`).
**Universe**: `date, symbol`, genuinely point-in-time.
**Sectors**: `symbol, sector` — a static snapshot (see limitations).

Coverage that matters: option chains 2017–2025, stock OHLC only from 2023-06,
SPX index chains 2017–2025, SPX OHLC only from 2024. That is why the demo runs
in 2025.

## Units: dollar vega

Every position, target and optimizer weight is in **dollar vega**: P&L in
dollars per 1.00 vol point (0.01) change in implied vol.

```
dollar vega of a position = vega_per_share_per_point × 100 × contracts
```

ThetaData quotes vega per 1.00 of IV (a 100-point move). `loaders.detect_vega_convention`
verifies this empirically — it reprices a sample of contracts with
Black-Scholes at IV and IV + 0.01 and checks which scaling the price change
matches — and the loader divides by 100 so the canonical `vega` is per point.
The check also confirms theta is per calendar day. It is logged at INFO and
tested in `tests/test_loaders.py`.

Reference returns, alphas and the risk model are all "per dollar of vega":
`pnl_per_vega` is a unit's daily P&L divided by its dollar vega at inception,
so a value of 1.0 means the position made what a one-point IV rise would have
made. Daily standard deviations for single names are 1–4 in these units,
which is the gamma/theta noise of a hedged straddle, not IV changes.

## Pipeline

```
loaders.py                 ThetaData → canonical (lazy scans)
providers/
  calendar.py              TradingCalendar: sessions, shift, next/previous
  universe.py              PointInTimeUniverse, StaticSectorProvider
  surface.py               ATM IV per expiration → total-variance interpolation → iv30/60/90
                           EarningsAdjuster hook (NoOpEarningsAdjuster) sits before interpolation
  realized_vol.py          Yang-Zhang RV panel; HARForecaster (expanding, refit monthly)
  stock_features.py        beta, idio vol, log_size (proxy), gap_freq from stock returns
  straddle_returns.py      per-unit-vega reference P&L, produced by running the backtester; StoredStraddleReturns
  chain.py                 per-date option cross-section (one lazy scan per session)
  signals.py               CrossSectionalVRPSignal: residualised, smoothed, z-scored
  alphas.py                ICScaledAlpha: alpha = -IC · sigma_idio · z
instruments/
  base.py                  Instrument ABC: select / mark / should_roll / hedge_shares
  straddle.py              DeltaHedgedStraddle
risk_model/
  spec.py                  FactorSpec — the one factor definition (market + GICS sectors)
  factor.py                FactorRiskModel, Ledoit-Wolf, factor construction
  constructor.py           FactorRiskModelConstructor.get_risk_model(date), estimated in process
  store.py                 StoredFactorRiskModelConstructor + loadings/covariance/idio providers over data_store/
optimizer/
  base.py                  Optimizer / Objective / OptimizerConstraint ABCs, OptimizationError
  objectives.py            MaxUtility, TurnoverPenalty, JumpPenalty
  constraints.py           NetVegaNeutral, FactorNeutral, PerNameVegaCap, GrossShortVegaCap, GrossVegaCap
  mvo.py                   MVO (Clarabel); raises on non-optimal status
  strategy.py              OptimizationStrategy
strategy.py                Strategy, ScoreStrategy ABCs; RankWeightedStrategy, FixedTargetStrategy, ReferenceUnitStrategy
trade_generator.py         target vega → contracts (integer or fractional); MaxSpread, MinOpenInterest, no-trade band
costs.py                   CostModel ABC; HalfSpreadCost (options), PerShareCost (hedge)
portfolio.py               Portfolio / Position, JSON save & load
backtester.py              the daily loop over the chain (chain engine)
panel_backtester.py        the same book off the reference panel, seconds not hours (panel engine)
results.py                 BacktestResults: summary, factor_regression, decile_table, plots
```

### Where the equity abstraction stops

atium's `Provider.get(date)` shape, `Objective.build(w, **kwargs)`,
`OptimizerConstraint.build`, `TradingConstraint`, `RiskModelConstructor` and
`Strategy` all carry over. Three things do not, and each says so in its
docstring:

* **`Instrument`** has no equity counterpart. A unit of exposure is a bundle of
  listed legs plus a stock hedge that must be chosen from a chain, decays, and
  is rolled. Both the backtester and the risk model's return builder go
  through it, so the marking logic exists once.
* **`Strategy.generate_targets(date, chain_df, portfolio)`** sees the chain and
  the book, because a vol target only makes sense for names with a tradeable
  straddle today, and held series are never swapped outside a roll.
* **`CostModel.compute(quantity, bid, ask, price)`** is per fill, not per weight
  vector, because contracts and hedge shares are priced differently.

### The daily loop

1. Mark every unit at mid; option P&L and close-to-close hedge P&L.
2. Roll any unit the instrument says to: at `roll_dte` (20), or when the strike
   has drifted more than 15% from spot. Close and reopen at mid, paying the
   option cost model twice.
3. On a rebalance session (`daily` / `weekly`): risk model → alphas →
   optimizer → trade generator → execute at mid, paying costs.
4. Re-hedge once on the final position, paying the hedge cost model on shares
   traded. (This is after the roll and rebalance, so a roll does not pay for a
   hedge it immediately undoes.)
5. Record per-symbol P&L, costs, vega, theta, book delta, `exit_cost`.

The book is a `Portfolio` (plain data, JSON round-trip); pass one in to
resume a run — `tests/test_backtester.py` checks that a split run equals a
continuous one.

### Reference returns and the risk model

The risk model needs a daily "return" per name. It is the P&L of holding one
long unit of the instrument, delta-hedged and rolled exactly as the book does,
divided by the unit's dollar vega at inception — built by running the
`Backtester` itself with `ReferenceUnitStrategy` on each symbol. Factors: the
SPX reference straddle (or the vega-weighted universe mean) and one factor
per GICS sector, orthogonalised to the market with a trailing beta. Loadings
from a rolling 250-session regression, idio vol from the residual, factor
covariance Ledoit-Wolf shrunk.

As in atium, the estimation is a pipeline and the backtest reads its output.
`data_pipelines.cli vol-risk-model` writes `vol_reference_returns/<SYMBOL>.parquet`,
`vol_factor_returns`, `vol_factor_loadings`, `vol_factor_covariances` and
`vol_idio_vol` to `data_store/` for 2017–2025 (all point-in-time; the
reference step is resumable per symbol). `risk_model/store.py` provides
`FactorLoadingsProvider`, `FactorCovariancesProvider`, `IdioVolProvider` and
`StoredFactorRiskModelConstructor` over those tables, and
`StoredStraddleReturns` over the reference files; `data_access_layer` exposes
the same tables as `load_vol_*`. The in-process
`FactorRiskModelConstructor` and `StraddleReturnsProvider.from_store` remain
for tests and for an instrument that has not been pipelined
(`demos/level_book.py --in-process`).

`FactorSpec` is shared with the signal, which residualises the variance
premium against the same market and sector factors. `tests/test_signals.py`
asserts the residuals are orthogonal to every regressor.

## Demo output

Two books share every input except the signal:

* `demos/level_book.py` — the residualised variance premium (`CrossSectionalVRPSignal`).
* `demos/iv_zscore_book.py` — each name's log 60-day ATM IV against its own
  trailing 250-session mean and std (`TimeSeriesIVZScoreSignal`), a
  mean-reversion control with no forecast and no cross-sectional regression.

Both run 2025-01-02 to 2025-06-30 on the full universe with the stored risk
model, weekly rebalance and a 15% re-strike band. The **default is the
research configuration**: no costs, no liquidity screens, fractional
contracts, and only the net-vega-neutral and gross-vega ($20k) constraints;
`--costs`, `--screens`, `--full-constraints` and `--jump-penalty` restore
the tradeability checks, and `--strategy rank` swaps the optimizer for a
centred-rank book with no risk model. Knobs are at the top of
`level_book.py` (λ = 1e-5, turnover 0.10/$vega, per-name cap $2k, gross
short cap $10k). Each prints `summary()`, the factor regression and the
60-session decile table, and writes `demos/figures/*_<tag>.png`; the last
logs are `demos/*_book.log`.

Research configuration, 2025-01-02 to 2025-06-30, 122 sessions, gross vega
$20k, no costs (logs: `demos/<signal>_book_<strategy>.log`):

| | VRP, MVO | VRP, rank | IV z, MVO | IV z, rank |
| --- | --- | --- | --- | --- |
| mean positions / gross vega | 493 / $19.6k | 493 / $19.8k | 497 / $19.7k | 495 / $19.8k |
| gross P&L | +$140k | +$80k | +$324k | +$171k |
| annualised gross P&L per $ gross vega | 14.8 | 8.4 | 33.9 | 17.9 |
| Sharpe (gross) | 1.02 | 2.35 | 2.29 | 3.74 |
| max drawdown | -$98k | -$28k | -$106k | -$30k |
| annual turnover / gross vega | 45x | 52x | 46x | 59x |
| P&L loading on market vol factor (t) | +0.15 (2.1) | +0.05 (2.8) | -0.07 (-0.8) | -0.06 (-2.7) |
| intercept, P&L per $ gross vega per day (t) | 0.07 (0.8) | 0.04 (1.8) | 0.07 (0.8) | 0.06 (2.2) |

The decile tables are unchanged by the strategy (they are a property of the
signal): VRP 4.4 vs 4.0 (t 5.1 / 4.9) for deciles 1 vs 10, IV z 4.9 vs 4.5
(t 6.4 / 5.2). Every book is positive gross over the half year; the rank
books earn roughly half the dollars of the MVO books with a third of the
drawdown, so the optimizer is adding return but not risk-adjusted return
on this window. Turnover is 45–60x gross vega a year in every case, which
is why the tradeable configuration below is cost-dominated.

Seven years on the panel engine (`--rv-source close --start 2018-07-02
--end 2025-06-30`; a minute per book):

| | VRP, MVO | VRP, rank | IV z, MVO | IV z, rank |
| --- | --- | --- | --- | --- |
| mean positions / gross vega | 476 / $19.5k | 470 / $19.4k | 489 / $19.1k | 485 / $19.5k |
| gross P&L | +$2.97M | +$1.07M | +$1.58M | +$1.31M |
| annualised gross P&L per $ gross vega | 21.9 | 7.9 | 11.8 | 9.7 |
| Sharpe (gross) | 1.28 | 1.56 | 0.67 | 1.50 |
| max drawdown | -$280k | -$83k | -$597k | -$223k |
| annual turnover / gross vega | 40x | 50x | 35x | 51x |
| P&L loading on market vol factor (t) | +0.01 (0.3) | +0.01 (0.9) | -0.08 (-3.3) | -0.08 (-8.3) |
| intercept, P&L per $ gross vega per day (t) | 0.081 (3.2) | 0.030 (4.0) | 0.048 (2.0) | 0.040 (4.6) |
| backtest time | 63 s | 11 s | 64 s | 10 s |

Every intercept is significant over seven years; VRP beats the z-score as
the seven-year deciles say it should; the optimizer buys P&L with
concentration and does not beat the rank book on Sharpe. Details and the
chain-versus-panel agreement note are in [docs/experiments.md](docs/experiments.md).

The earlier *tradeable* configuration (costs, screens, full constraints,
jump penalty 1.0), i.e. today's `--costs --screens --full-constraints --jump-penalty 1`:

| | VRP book | IV z-score book |
| --- | --- | --- |
| mean positions / gross vega | 36 / $10.4k | 51 / $9.8k |
| gross P&L / costs / net | +$92k / $530k / -$438k | +$38k / $388k / -$349k |
| annual turnover / gross vega | 36x | 30x |
| decile 1 vs 10 forward 60-session gross P&L per $ vega | 4.4 vs 4.0 (t 5.1 / 4.9) | 4.9 vs 4.5 (t 6.4 / 5.2) |
| net P&L loading on market vol factor (t) | +0.01 (0.1) | -0.13 (-1.5) |
| intercept, net P&L per $ gross vega per day (t) | -0.31 (-2.4) | -0.29 (-3.0) |

Runs are reproducible bit for bit: every selection tie (two monthlies
equidistant from the target, two strikes equidistant in delta) is broken by
an explicit key, never by row order.

Read these as a demonstration of the machinery, not of an edge. Both gross
decile spreads have the expected sign and are small; both books are
dominated by costs: crossing the full half-spread on EOD single-name
straddle quotes is a few vol points of vega per round trip, every roll pays
it twice (rolls are ~70% of total cost), and a myopic weekly MVO turns the
book over far faster than a 30-session signal warrants. The time-series
signal spreads over more names and churns less, which is why its costs are
a third lower. The levers are all config: `TurnoverPenalty`, the no-trade
`band`, `rebalance` and the signal smoothing, `StraddleConfig(restrike_moneyness,
roll_dte)`, and `HalfSpreadCost(fraction)` if you ever want a half-way fill.

### Longer history

`--rv-source close` swaps the Yang-Zhang inputs for close-to-close realized
vol computed off the chains' `underlying` column (`loaders.scan_spot_from_chains`,
`compute_close_to_close_panel`), which reaches 2017; the size control is
dropped because the chains carry no stock volume. Everything else already
covered the whole option history. Panels are built one symbol at a time and
cached per symbol under `voltium/.cache/`, so memory stays at one chain and
a rerun skips the builds. Runtime is the per-session chain scan, ~6 s on the
full universe, so seven years is ~3.5 hours per book.

Both books, `--rv-source close --start 2018-07-02 --end 2025-06-30`
(659 names, 1,758 sessions; outputs tagged `_<signal>_2018_2025`):

| | VRP book | IV z-score book |
| --- | --- | --- |
| mean positions / gross vega | 41 / $12.0k | 58 / $12.3k |
| gross P&L / costs / net | +$952k / $7.09M / -$6.13M | -$112k / $6.63M / -$6.74M |
| annual turnover / gross vega | 30x | 25x |
| net P&L loading on market vol factor (t) | -0.11 (-3.4) | -0.25 (-8.6) |
| forward 60-session gross P&L per $ vega, deciles 1 → 10 | 2.04, 2.29, 2.34, 2.26, 2.23, 2.25, 2.01, 1.76, 1.69, **0.21** | **1.42**, 1.96, 2.00, 1.99, 1.95, 2.06, 2.19, 2.26, 2.15, **1.42** |

Over seven years the VRP deciles show what the signal is built to find:
long vol paid in every decile, fading from decile 6 up and collapsing in
decile 10, the names the signal calls richest (t 6.5 in decile 1, 0.7 in
decile 10). The time-series z-score is U-shaped instead: its top decile is
no worse than its bottom one, and the middle is best — high-for-itself IV
is as often the start of a vol episode as the end of one, and the book that
shorts it carries a significant short-vol tilt (market loading -0.25). Both
books lose on costs for the reasons above; the VRP book is the one with a
gross edge.

## Known v1 limitations

* **Earnings adjustment is opt-in.** `providers/earnings.py:
  TermStructureEarningsAdjuster` strips the jump variance from the pillars;
  the demos still use `NoOpEarningsAdjuster` because
  `research/earnings_adjusted_vrp` found it removes the signal's calendar
  loading without changing its P&L.
* **Sectors are a static snapshot.** GICS sectors come from today's Wikipedia
  constituent table (`data_pipelines.cli sectors`). A name that left the index
  has no sector and drops out of the sector factor; a name that changed sector
  carries its current one. The universe itself *is* point-in-time.
* **`log_size` is average dollar volume, not market cap.** The store has no
  shares outstanding. The size control in the signal regression is the log of
  60-session mean close × volume. Swap in a market-cap panel in
  `stock_features.py` when one exists.
* **SPX is used** for the market factor and the SPX variance premium. The
  fallback (vega-weighted universe mean) is implemented and selected by
  `FactorSpec(market_source="universe")` or automatically when no SPX surface
  is available.
* **Realized vol starts 2023-06** (stock tier floor), so the HAR and the signal
  cannot be run earlier even though chains reach 2017. The reference straddle
  P&L and the risk model can.
* **Corporate actions on held contracts.** A split changes the vendor's option
  root; the held contract stops being quoted and is closed at its last mark
  after `max_stale_days` (flagged `forced_close`). Returns on the stock side
  are split-adjusted.
* **Rates and dividends.** Black-Scholes repricing uses a flat 4% and no
  dividend; it only serves the vega check and gap marking.
* **The optimizer is myopic.** The L1 turnover penalty is quoted per session,
  so it has to be the round-trip cost amortised over the expected holding
  period (see the demo's comment), not the cost of a trade.

## Extension points

* **`EarningsAdjuster.adjust(pillars) -> pillars`** (`providers/surface.py`):
  subtract jump variance from expirations spanning an announcement before
  interpolation.
* **`Instrument`** (`instruments/base.py`): a term-structure book is a calendar
  spread instrument (two expirations, ratios `(+1, -1)`), a skew book a
  risk-reversal (two strikes). `UnitSpec` already carries multiple legs and
  ratios; the backtester and reference-return builder are instrument-agnostic.
* **`SignalProvider`** (`providers/signals.py`): any `(date, symbol, signal)`
  panel; the term slope or the 25-delta skew from the same surface builder.
* **`RiskModel` / `RiskModelConstructor`** (`risk_model/base.py`): `covariance`,
  `loadings`, `idio_vol` over any per-unit-vega return series — feed it the
  reference returns of the new instrument.
* **`Objective` / `OptimizerConstraint`**: composable; add a term and pass it
  in the list.
* **`CostModel`**: per-fill; a volume-aware model gets the same `(quantity,
  bid, ask, price)`.
