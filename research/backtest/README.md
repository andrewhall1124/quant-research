# research/backtest

A shared backtesting framework for option strategies. Not a study — studies
live in `research/<topic>/` and import this.

## Why it is layered this way

The binding constraint is that `data_store/option_greeks/` is 8.5 GB across
519 files. A grid over OI thresholds and holding periods is dozens of runs, and
re-reading the store for each one is not affordable. So the store is read
twice, cached, and never touched again:

| Layer | Module | Cost | What it produces |
|---|---|---|---|
| 1 | `panel.py` | minutes, cached | a tight band of candidate contracts, plus targeted marks |
| 2 | everything else | seconds | one run of one configuration |

The two-pass split in layer 1 is the part that matters. A **selection** panel
keeps only contracts a structure could plausibly pick (near a target dte, near
the money). A **marks** panel then fetches daily quotes for exactly the
contracts that were selected, over exactly the days they are held. A single
band wide enough to do both jobs — a 30-day straddle held 21 days ends at 9 dte
and its strike can drift 30% away — would be ~47M rows and ~8 GB.

Everything a filter might want (`open_interest`, `volume`, `rel_spread`,
`iv_error`) is stored as a **column**, never applied as a filter when the panel
is built. That is what makes the eligibility sweep free.

## The pipeline

```
structure.select      PIT contract choice, per (symbol, date)
summarize_positions   legs -> one row per name-day, greeks netted
signal.attach         score from the traded contract's own IV
apply_filters         eligibility, at formation only
assign_quantiles      daily rank into n buckets of equal count
size_positions        signed dollar-vega target per name
build_holdings        expand into h overlapping cohorts
attach_marks          daily quotes for every held leg
truncate_at_failure   cut cohorts at a missing mark or a split
compute_pnl           option P&L + delta hedge, in dollars
```

Selection runs **before** the signal on purpose: the score is built from the
implied vol of the contract actually being traded, so the signal and the
instrument cannot drift apart.

## Usage

```python
from research.backtest import BacktestConfig, build_context, run
from research.backtest.structures import AtmStraddle
from research.backtest.signals import VrpSignal
from research.backtest.eligibility import MinOpenInterest, MaxRelativeSpread
from research.backtest import metrics

context = build_context(forecast_start=date(2023, 6, 1))   # expensive, once
config = BacktestConfig(
    signal=VrpSignal(forecast="GARCH", horizon=21),
    structure=AtmStraddle(target_dte=30, max_dte_error=7),
    eligibility=(MinOpenInterest(500), MaxRelativeSpread(0.10)),
    holding_days=21,
)
result = run(config, context)                              # cheap, repeat
metrics.summarize(result.daily_pnl, config.holding_days)
```

Build the layer-1 panel first:

```bash
uv run python -m research.backtest.panel --refresh
```

## Conventions this framework commits to

**Dollars, not returns.** A vega-weighted option book has no natural capital
base, and inventing one (premium, notional) adds a noisy scaling unrelated to
the signal. Each side carries a fixed dollar vega budget, split equally across
the names in its decile, so the book is vega-neutral by construction and every
figure is in dollars. Sharpe is scale-free and survives the choice.

**Greek units.** ThetaData quotes per share, per unit of vol. `mid`, `delta`
and `theta` need the ×100 contract multiplier; **`vega` does not** — as stored
it is already dollars per contract per vol point. Multiplying it scales the
whole book by 100.

**Filters bind at formation only.** A screen changing mid-hold is not a reason
you could have unwound, and re-applying it daily would use information the
trade did not have. Only a missing mark or a split ends a position early.

**Overlapping cohorts.** Daily formation with an `h`-day hold keeps `h` cohorts
live at once, turning the book over at 1/h per day. The reported series is
divided by `h`, so it is one book run at the target vega rather than `h` books
stacked.

**Newey-West, not Driscoll-Kraay.** `research/single_name_vol/` needed DK
because it scored a *pooled panel* sharing a market factor. This framework
aggregates the cross-section into one daily portfolio series before testing
anything, so the panel dimension is already collapsed and only the serial
correlation from overlapping cohorts remains. NW with `holding_days` lags is
the correct correction here; the two notes are two sides of the same rule.

## Not yet implemented

- **Transaction costs.** `rel_spread` is carried on every panel row so a
  half-spread model can be added without a rebuild, but no costs are charged
  today. Single-name option spreads are wide, so nothing here is a tradeable
  result until they are.
- **Earnings conditioning.** `dal.with_earnings_distance` is available and a
  cross-sectional IV sort is known to load on earnings timing; treating it is
  deferred, not solved.
- **Integer lot sizes.** Quantities are continuous.
