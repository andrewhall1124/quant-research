# research/vrp_cross_section

A daily cross-sectional volatility strategy on S&P 500 single names: rank every
name on how rich its 30-day ATM straddle is, buy the cheapest decile against
the richest, equal-weight each side by vega, delta-hedge daily. Findings are in
**[REPORT.md](REPORT.md)**.

The short version: the gross signal is real and well identified, and it does
not survive its own bid-ask spread.

## Run it

```bash
uv run python -m data_pipelines.open_interest                    # ~3 hr, once
uv run python -m tools.backtest.panel --refresh               # ~5 min, cached
uv run python -m research.vrp_cross_section.analysis \
    --forecast-start 2023-06-01                                  # ~10 min
```

`--forecast-start` reaches into `underlying_history.parquet` so the GARCH
burn-in for the VRP variants completes before 2025 rather than eating the first
half of the sample. `--refresh` refits the vol forecasts; without it they come
from `tools/backtest/results/forecasts.parquet`.

All the machinery lives in `tools/backtest/` — this module only chooses
configurations and scores them.

## Files

| File | What it is |
|---|---|
| `analysis.py` | every configuration, figure and table |
| `results/open_interest_grid.csv` | the liquidity-floor sweep |
| `results/holding_grid.csv` | the holding-period sweep |
| `results/signal_race.csv` | five sorts through the same machinery |
| `results/cost_grid.csv` | the same strategy charged 0 to 1x the quoted spread |
| `results/decile_monotonicity.csv` | all ten deciles held long |
| `figures/*.png` | regenerated on every run |

## Design in brief

- **Sample** calendar 2025, 519 S&P 500 names with an option chain. 250 trading
  days, of which the z-score burn-in costs the first ~40.
- **Signal** each name's ATM implied vol against its own trailing 60-day IV
  history. The forecast-based `IV - E[RV]` definition is a variant, not the
  strategy — see the report for why.
- **Instrument** the 30-day ATM straddle, expiration and strike chosen
  point-in-time from that day's chain, delta-hedged daily at the close.
- **Portfolio** ten deciles, extremes traded, $10k of vega per side split
  equally across names. Vega-neutral by construction; all P&L in dollars.
- **Costs** a configurable fraction of the quoted spread, charged against the
  actual quote on the entry and exit days.
- **Inference** Newey-West with `holding_days` lags on the aggregated daily
  series. Not Driscoll-Kraay — the cross-section is summed away before anything
  is tested. See `tools/backtest/README.md`.

## The three things that decide the result

1. **Open interest is thin.** The median selected straddle has 19 contracts on
   its thinner leg. A floor of 500 leaves 15 names on the median day, which is
   one or two per decile, and `min_names_per_side` then drops most of the
   calendar. Any filter sweep here changes the *sample*, not just the screen,
   which is why `formation_days` is reported in every table.
2. **Spreads are enormous.** A median 19.8% of mid, 36.8% at the 75th
   percentile. This, not the signal, is what the study ends up being about.
3. **One year is not enough.** ~200 overlapping days is ~10 independent
   observations, over a window with one dominant shock. The grid searched more
   configurations than the sample can support.
