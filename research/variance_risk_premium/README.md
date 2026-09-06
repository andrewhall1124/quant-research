# Variance risk premium on S&P 500 single names

Sell one ATM straddle per name per month, no signal, and measure what selling
volatility earned on S&P 500 constituents over 2017–2025. The position is
carried three ways (naked, delta-hedged once at entry, delta-hedged daily) and
the premium is also measured directly as implied variance paid minus realized
variance delivered. SPX monthlies are run through the same code for
comparison.

Run from the project root, after the Goyal-Saretto panel exists:

```sh
uv run python -m research.variance_risk_premium.panel      # ~30s: daily paths, realized variance, SPX rows
uv run python -m research.variance_risk_premium.analysis   # tables and figures
```

`panel.py` reuses the baseline's contract selection and adds the straddle's
daily midpoint, net delta and spot from entry to expiry, plus realized
variance over the hold. `analysis.py` computes the P&L of each carry and
writes everything to `results/` and `figures/`.

See [REPORT.md](REPORT.md) for the rules, results and caveats.

## Outputs

| File | Holds |
| --- | --- |
| `results/stock_months.csv` | every stock-month: IV, realized variance, premium, the three short-side returns |
| `results/monthly.csv` | equal-weighted monthly series across names |
| `results/spx_monthly.csv` | the same for SPX |
| `results/summary.csv` | mean, std, t, Sharpe, min, max per series and sample |
| `results/costs.csv` | the three carries at 0/25/50/100% of the quoted spread |
| `results/annual.csv` | by calendar year |
| `results/tails.csv` | worst eight and best five months |
| `results/conditional.csv` | split by earnings in hold, IV tercile, HV/IV tercile |
| `figures/premium.png` | implied vs realized, monthly daily-hedged P&L, cumulative carries |
| `figures/implied_vs_realized.png` | stock-month scatter |

`results/*.parquet` are rebuildable caches and are gitignored.
