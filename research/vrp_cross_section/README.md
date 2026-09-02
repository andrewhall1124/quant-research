# research/vrp_cross_section

A cross-sectional volatility strategy on S&P 500 single names. Buy the names
whose options are cheap relative to their own recent history, sell the ones
that are expensive, vega-weight both sides, delta-hedge daily, hold to expiry.

Findings, methods and intuition are in **[REPORT.md](REPORT.md)**.

The short version: gross Sharpe 3.15, and it can pay 89% of the quoted bid-ask
on every crossing and still make money. On 66 formation dates, which is about
one independent observation.

## Run it

```bash
uv run python -m data_pipelines.open_interest                     # ~3 hr, once
uv run python -m tools.backtest.panel --refresh --max-dte 140     # ~10 min, cached
uv run python -m research.vrp_cross_section.analysis \
    --forecast-start 2023-06-01                                   # ~15 min
uv run python -m research.vrp_cross_section.cost_efficiency       # ~2 min
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
| `figures/*.png` | regenerated on every run |

Each experiment is the strategy with exactly one thing changed, so every row in
those tables is a controlled comparison rather than a separate backtest.

## Three things to know before reading the numbers

1. **The sample is one year and the strategy holds for 60 days.** 66 formation
   dates is roughly one independent observation. The net result is not
   statistically distinguishable from zero, and `REPORT.md` §7 says so before
   it says anything else.
2. **This configuration survived a long search** — roughly 145 were evaluated.
   Treat t-statistics as ranking devices, not p-values.
3. **The mechanics generalise even though the performance number may not.**
   Holding to expiry halves the spread bill by arithmetic; vega growing as √T
   while spreads stay tick-driven is a property of option pricing; a short-dated
   IV sort ranking the earnings calendar is visible in the data without any
   P&L. Those survive any sample.
