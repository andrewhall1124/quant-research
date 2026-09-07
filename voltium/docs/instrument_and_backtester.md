# Instrument and backtester

## The instrument (`instruments/`)

### Types

```python
LegSpec(expiration, strike, right)              # one listed contract, unsigned
UnitSpec(symbol, legs: tuple[LegSpec], ratios)   # one unit of exposure; a straddle is two legs, ratios (1, 1)
UnitMark(date, mid, bid, ask, delta, vega, theta, underlying, dte, complete)
```

`UnitMark` is per share for one unit: for a straddle, `mid` is call mid plus
put mid, `delta` the sum of the two deltas, `vega` the sum of the two
per-point vegas. Multiply by 100 for one contract in dollars.
`UnitMark.half_spread` is `(ask - bid) / 2`.

### The ABC

```python
class Instrument(ABC):
    def select(self, quotes_df, date_) -> UnitSpec | None      # choose legs to enter today
    def mark(self, quotes_df, unit, date_) -> UnitMark | None  # mark at mid; None if any leg unusable
    def should_roll(self, unit, mark) -> bool                  # close and reopen today?
    def hedge_shares(self, mark, contracts) -> float           # stock position that neutralises delta
```

`quotes_df` is canonical option rows for **one symbol on one date**. The
instrument never reads the store.

### `DeltaHedgedStraddle`

`StraddleConfig` defaults:

| field | default | meaning |
| --- | --- | --- |
| `target_dte` | 60 | calendar days to expiration to aim for on entry |
| `roll_dte` | 20 | roll when `dte <= roll_dte` |
| `min_dte` / `max_dte` | 25 / 120 | never enter a series outside this band |
| `monthly_only` | True | only third-Friday-style expirations are eligible |
| `max_moneyness` | 0.10 | the entry strike must be within 10% of spot |
| `restrike_moneyness` | 0.15 | roll when the held strike drifts more than 15% from spot; `None` disables |

**Selection.** Among quoted contracts (`bid > 0`, `ask > bid`) in the DTE
band, keep monthly expirations (dated the 15th–21st on a Thursday or
Friday; the Thursday case covers Good Friday). Keep strikes where both a
call and a put are quoted and `|K/S - 1| <= max_moneyness`. Then sort by
`(|dte - target|, -dte, |K/S - 1|, strike)` and take the first: nearest to
target, the longer of two equidistant series, the strike nearest spot, the
lower strike. Every key is explicit so the choice never depends on row
order — two monthlies equidistant from the target is common (46 vs 74 days),
and until this was made explicit the winner depended on which parquet
thread finished first.

**Marking.** Filter to the unit's expiration, strike and rights; require
every leg to have `ask > 0` and `ask >= bid >= 0`; sum mids, bids, asks,
deltas, vegas, thetas. A missing or zero-ask leg returns `None`, and the
backtester treats the day as stale.

**Rolling.** `dte <= roll_dte`, or `|K/S - 1| > restrike_moneyness`. The
re-strike is a v1 addition to the spec's time-only rule: a straddle 15% from
the money is a gamma position, its dollar vega collapses, and a book that
sizes in dollar vega would keep adding contracts of it to hold target vega.
Fifteen percent is roughly a one-sigma 60-day move for a 30-vol name, so it
triggers on a genuinely large drift, not on noise.

**Hedging.** `-delta × 100 × contracts` shares. Reset once per session, at
the close, on the final position of the day.

## The book (`portfolio.py`)

```python
Position(unit, contracts, hedge_shares, entry_date,
         last_mid, last_underlying, last_vega, last_delta, stale_days)
Portfolio(date, positions: dict[symbol, Position], cumulative_pnl, cumulative_cost)
```

Plain data. `Portfolio.save(path)` / `Portfolio.load(path)` round-trip
through JSON, and `Backtester.run(portfolio=...)` resumes from the session
after `portfolio.date`. `tests/test_backtester.py` checks that a run split in
two and stitched equals a continuous run on every P&L and cost column.

`Portfolio.dollar_vega(symbol)` is `last_vega × 100 × contracts` — the
mark-to-market vega, which is what the optimizer receives as `previous`.

## The daily loop (`backtester.py`)

```python
Backtester(calendar, chain, instrument, strategy, trade_generator, config,
           option_cost=NoCost(), hedge_cost=NoCost())
BacktestConfig(start, end, rebalance="weekly" | "daily", max_stale_days=5)
records_df, portfolio = backtester.run(portfolio=None, progress=True)
```

For each session, in this order:

1. **Mark and P&L.** For every held position: `mark = instrument.mark(...)`.
   If it succeeds, `option_pnl = (mid - last_mid) × 100 × contracts` and
   `hedge_pnl = hedge_shares × (underlying - last_underlying)`, and the
   `last_*` fields are updated. If it fails, the day is stale: option P&L is
   zero, the hedge is still marked against the symbol's underlying if any
   row exists, and `stale_days` increments. Past `max_stale_days`, or at
   expiration, the position is closed at its last mark with no option cost,
   the hedge is unwound (paying the hedge cost model), and `event` is
   `forced_close`.
2. **Roll.** For every marked position where `instrument.should_roll`:
   pay `option_cost.compute(contracts, bid, ask, mid)` on the old unit,
   `select` a new one, `mark` it, pay again to open, keep the contract
   count, set `entry_date`. If no new unit qualifies, the position is closed
   (`roll_failed_closed`).
3. **Rebalance** (first session of each ISO week for `weekly`, every session
   for `daily`): `strategy.generate_targets(date, chain_df, portfolio)` →
   `trade_generator.generate(...)` → `execute` each trade at mid, paying the
   option cost model on every contract traded. A held name absent from the
   targets is a target of zero.
4. **Re-hedge.** On the final position of the day, set the hedge to
   `instrument.hedge_shares(mark, contracts)` and pay the hedge cost model on
   the shares traded. Positions whose contracts went to zero are removed
   and their hedge unwound. The hedge is done last, after roll and
   rebalance, so a roll never pays for a hedge it immediately undoes; the
   spec listed it earlier in the sequence.
5. **Record** one row per symbol that was held at any point in the session.

The book's realised P&L at the end of the session is the sum of option and
hedge P&L less costs; `Portfolio.cumulative_pnl` and `cumulative_cost`
accumulate it.

### Records schema

`RECORD_SCHEMA`, one row per `(date, symbol)`:

| column | meaning |
| --- | --- |
| `expiration`, `strike` | the unit held at the close (after any roll) |
| `contracts` | signed, at the close |
| `hedge_shares` | at the close |
| `mid`, `underlying` | the close marks |
| `option_pnl`, `hedge_pnl` | dollars, this session |
| `option_cost`, `hedge_cost` | dollars charged this session (rolls, trades, hedges) |
| `dollar_vega` | `last_vega × 100 × contracts` at the close |
| `dollar_theta` | `theta × 100 × contracts`, dollars per calendar day |
| `exit_cost` | half-spread × 100 × |contracts| — informational |
| `book_delta` | `delta × 100 × contracts + hedge_shares`; zero by construction after the hedge |
| `dte` | calendar days to the held expiration |
| `stale` | no usable mark today |
| `event` | `""`, `open`, `trade`, `close`, `roll`, `roll_failed_closed`, `forced_close` |

`results.BacktestResults` aggregates these to a book series; see
[results.md](results.md).

## Chain providers (`providers/chain.py`)

The backtester asks `ChainProvider.get(date)` once per session for canonical
rows across every symbol it might hold or trade.

* `StoreChainProvider(paths, symbols, band=ChainBand(max_dte=130, max_moneyness=0.30))`
  runs one lazy scan per call; the date predicate is pushed into every
  symbol-year file and the band keeps the collected frame to tens of
  thousands of rows. 1–3 s per session on the full universe, and that is
  most of a backtest's runtime.
* `FrameChainProvider(chain_df)` holds a frame in memory (tests).
* `PartitionedChainProvider(chain_df)` (in `straddle_returns.py`) partitions
  one symbol's chain by date once, for the reference-path builder.

The band matters: a held straddle drifts, so the moneyness band has to be
wider than the entry band (0.30 vs 0.10) or the mark for a drifted unit
disappears and it goes stale.

## Strategies (`strategy.py`, `optimizer/strategy.py`)

`Strategy.generate_targets(date, chain_df, portfolio) -> (symbol, target_vega)`.

* `FixedTargetStrategy({symbol: dollar_vega})` — a hard-coded book, used to
  validate the backtester.
* `ReferenceUnitStrategy` — exactly one long contract in every symbol with a
  chain today, via an optional `target_contracts` column that bypasses the
  vega rounding. This is what builds the reference returns.
* `OptimizationStrategy(alpha_provider, risk_model_constructor, optimizer, universe, gap_freq)`
  — alphas for today's eligible names (chain ∩ universe, plus anything held
  so it can be unwound), the current book as `previous`, `gap_freq` from the
  stock features, then `optimizer.optimize(...)`.

## What the tests pin down

* A single long straddle with zero IV change and zero spot change loses
  exactly the vendor theta of its two legs times 100 times contracts, less
  the entry costs — the synthetic day-2 mids are day-1 mids plus theta, so
  the assertion is exact (`test_backtester.py`).
* `book_delta` is zero to 1e-9 after every session over 40 sessions with
  random spot and IV paths, including through a roll.
* A run split in two and resumed from a saved `Portfolio` equals the
  continuous run.
