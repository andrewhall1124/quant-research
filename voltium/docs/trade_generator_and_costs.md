# Trade generator and costs

## From dollar vega to contracts (`trade_generator.py`)

```python
TradeGenerator(instrument, TradeGeneratorConfig(max_spread=0.08, min_oi=100, band=0.10, max_contracts=None, fractional=False),
               constraints=None)   # default: [MaxSpread(max_spread), MinOpenInterest(min_oi)]
TradeGeneratorConfig.research()    # max_spread=inf, min_oi=0, band=0, fractional=True — the demos' default
trades = generator.generate(targets_df, chain_by_symbol, marks, portfolio, date_)
```

`targets_df` is `(symbol, target_vega[, target_contracts])`; a held symbol
absent from it is a target of zero. `marks` holds today's `UnitMark` for
every held position; `chain_by_symbol` today's canonical rows per symbol.
The output is a list of `Trade(symbol, unit, contracts, mark)` with
`contracts` the signed *change*.

Per symbol, in order:

1. **Resolve the unit.** A held name keeps its series (`position.unit`) and
   uses today's mark; a held name that is unmarkable today is left alone. A
   new name gets `instrument.select(...)` and a mark; if either fails it is
   skipped.
2. **Round.** `target_contracts = round(target_vega / (mark.vega × 100))`,
   clipped to `±max_contracts` if set. An explicit `target_contracts` column
   bypasses the rounding (the reference-path builder uses it to hold exactly
   one contract).
3. **Screen, on opening or adding only.** Every `TradingConstraint.allow(quotes_df, unit, mark, is_opening)`
   must pass. `MaxSpread`: `(ask − bid) / mid ≤ max_spread` on the straddle.
   `MinOpenInterest`: both legs have non-null OI ≥ `min_oi`. Reducing or
   closing is never screened — a name that became illiquid can still be
   exited.
4. **No-trade band.** If the name is held and
   `|change| × unit_vega < band × |current dollar vega|`, skip. A 10% band
   suppresses resizing noise without blocking a flip.

`TradingConstraint` mirrors atium's ABC of the same name; add a screen by
subclassing it and passing the list.

The screens run *after* the optimizer, so a name the optimizer sized that
fails the spread or OI test simply is not traded, and the book ends up below
the gross cap. If that matters, screen the alpha universe before optimising.

## Costs (`costs.py`)

```python
class CostModel(ABC):
    def compute(self, quantity, bid, ask, price) -> float   # dollars for one fill
```

| model | formula | used for |
| --- | --- | --- |
| `NoCost()` | 0 | default |
| `HalfSpreadCost(fraction=1.0, fee_per_contract=0.0)` | `|contracts| × ((ask − bid)/2 × fraction × 100 + fee)` | every option fill: open, resize, close, both sides of a roll |
| `PerShareCost(per_share=0.005)` | `|shares| × per_share` | every hedge adjustment, and the unwind on a close |

`bid` and `ask` passed to the option model are the straddle's (sum of the
legs'), so `fraction=1.0` pays the full half-spread on both legs, mid to
touch. `fraction=0.5` is the usual "half-way fill" assumption. The demos
charge nothing unless `--costs` is passed, which pays the full half-spread.

**Fractional contracts.** With `fractional=True` the target is
`target_vega / unit_vega` unrounded, so every name hits its dollar-vega
target exactly and `contracts` in the records and the `Portfolio` is a
float. The default rounds to an integer as before. The rank-weighted book
needs this: $20k of gross vega over 400 names is ~$50 a name, under one
contract's vega for most of them.

The backtester charges the option model on every contract traded and the
hedge model on every share traded; `records_df.option_cost` and
`hedge_cost` carry the dollars per session, and `event` says why (`open`,
`trade`, `close`, `roll`). In the demo books rolls are about 70% of total
cost, opens and resizes most of the rest, hedging about 1%.

## Reading the cost numbers

A median straddle half-spread on this store is 1.3–1.7 per dollar of vega.
Per contract on a $200 name that is about $50 × 1.5 = $75 a side. A book of
40 positions averaging 12 contracts that rolls each unit every 20–30
sessions pays roughly $2k per roll and sees about one roll a session, which
is the ~$4k-a-day cost slope visible on every demo equity curve. Nothing in
that is a bug; it is the price of trading single-name straddles at the EOD
touch with a fast roll.
