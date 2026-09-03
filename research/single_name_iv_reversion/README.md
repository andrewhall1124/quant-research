# research/single_name_iv_reversion

Cheap against expensive implied vol, in the cross-section of S&P 500 single
names. Buy the names whose implied volatility is low relative to their own
recent history, sell the ones where it is high, vega-weight both sides,
delta-hedge daily, hold to expiry.

The bet is that implied volatility mean-reverts. It is **not** a variance risk
premium strategy, despite using the instrument that would harvest one: two
thirds of the option P&L is vega — money made when implied vol moves — against
a third from gamma and theta, the channel that pays when realized volatility
undershoots implied. The premium is the smaller half, the book is vega-neutral
so the premium's level cancels by construction, and `IV - E[RV]` — the premium
measured directly — is the worst of the four signals tried.

Findings, methods and intuition are in **[REPORT.md](REPORT.md)**.

The short version, on 2024 + 2025: gross Sharpe 1.55, net of half the quoted
spread **0.10**, break-even 0.535 against the 0.5 needed. On 2025 alone those
were 3.15, 1.35 and 0.886 — so adding the out-of-sample year roughly halves the
gross result and takes the net result to zero. It looks substantially fitted to
2025. What survives the second year is the ordering of signals and the
mechanics of execution, not the size of the edge.

## Run it

```bash
uv run python -m data_pipelines.open_interest                     # ~3 hr, once
uv run python -m tools.backtest.panel --refresh --max-dte 140     # ~10 min, cached
uv run python -m research.single_name_iv_reversion.analysis \
    --forecast-start 2023-06-01                                   # ~15 min
uv run python -m research.single_name_iv_reversion.cost_efficiency       # ~2 min
```

`--forecast-start` reaches into `underlying_history.parquet` so the vol-model
burn-in completes before the option sample opens — it only matters for the two
forecast-based signals in the race. `--refresh` refits those forecasts;
otherwise they come from `tools/backtest/results/forecasts.parquet`.

The selection panel must cover the tenors being swept: `--max-dte 140` is
enough for the 30/60/90/120-day grid.

## The strategy

| | |
|---|---|
| Universe | S&P 500 names with a listed chain, calendar 2025 |
| Signal | implied vol vs the name's own 60-session history (z-score) |
| Bet | that the z-score mean-reverts |
| Instrument | ATM straddle, 60 days to expiry (±12), strike within 5% |
| Screens | open interest ≥ 250, no earnings before expiry, vega ≥ $5, price ≥ $10 |
| Portfolio | 10 deciles, long cheapest / short richest, $10k vega per side |
| Hedge | daily delta hedge at the close |
| Exit | held to expiration, settled at intrinsic |
| Costs | half the quoted spread, entry only |

## Files

| File | What it is |
|---|---|
| `analysis.py` | the strategy, the six experiments that justify it, every figure |
| `cost_efficiency.py` | quoted spread per dollar of vega, by tenor, price and open interest |
| `results/strategy.csv` | the headline, gross and net |
| `results/decile_monotonicity.csv` | all ten deciles held long — does the sort grade |
| `results/signal_race.csv` | z-score against the textbook VRP and against an IV-level control |
| `results/tenor_grid.csv` | tenor × liquidity floor — why 60 days and why oi≥250 |
| `results/earnings_profile.csv` | how much of the sort the earnings calendar explains |
| `results/earnings_partition.csv` | the universe split on earnings-before-expiry |
| `results/exit_rule.csv` | sold-to-close against held-to-expiry |
| `results/cost_curve.csv` | net P&L as execution gets worse |
| `results/attribution.csv` | option P&L split into vega, theta and gamma — what names the strategy |
| `figures/*.png` | regenerated on every run |

Each experiment is the strategy with exactly one thing changed, so every row in
those tables is a controlled comparison rather than a separate backtest.

## Three things to know before reading the numbers

1. **Two years is about two independent observations at a 60-day hold.** 152
   formation dates. The net result (t = 0.15) is not distinguishable from
   zero, and the out-of-sample year is three times weaker than the in-sample
   one — 1.29 against 3.32 gross.
2. **This configuration survived a long search** — roughly 145 were evaluated.
   Treat t-statistics as ranking devices, not p-values.
3. **The mechanics generalise even though the performance number may not.**
   Holding to expiry halves the spread bill by arithmetic; vega growing as √T
   while spreads stay tick-driven is a property of option pricing; a short-dated
   IV sort ranking the earnings calendar is visible in the data without any
   P&L. Those survive any sample.
