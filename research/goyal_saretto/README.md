# Goyal and Saretto (2008), replicated on the S&P 500 option store

Decile portfolios of one-month ATM straddles and delta-hedged calls and puts,
sorted each month on the log difference between twelve-month historical
volatility and at-the-money implied volatility, following *Cross-Section of
Option Returns and Volatility* (Goyal and Saretto, March 2008 draft). The paper
trades every OptionMetrics name over 1996–2005; this runs the same rules on the
repo's point-in-time S&P 500 chains over 2017–2025.

Run from the project root:

```sh
uv run python -m research.goyal_saretto.panel      # ~30s, writes results/panel.parquet
uv run python -m research.goyal_saretto.analysis   # tables, figures, stdout summary
```

`panel.py` builds one row per stock-month from the option chains: the signal on
the formation day, the entry-day quotes, and the payoff at expiry. It is the
only step that touches `data_store/`. `analysis.py` cuts deciles and writes
everything under `results/` and `figures/`. `--band` on the analysis step
changes the moneyness screen (default 0.025, the paper's 0.975–1.025).

See [REPORT.md](REPORT.md) for the exact rules, every deviation from the
paper, the results and what they do and do not show.

## Outputs

| File | Holds |
| --- | --- |
| `results/stock_months.csv` | every traded stock-month: signal, contract, quotes, payoffs, returns, decile |
| `results/portfolio_monthly.csv` | equal-weighted monthly return per decile, decile 0 is the 10-1 spread |
| `results/table3_returns.csv` | the paper's Table 3: mean, std, t, Sharpe, certainty equivalents by decile |
| `results/table2_formation.csv` | the paper's Table 2: HV, IV, deltas, price/spot, earnings share by decile |
| `results/table8_costs.csv` | the 10-1 straddle at effective spreads of 0/50/75/100% of quoted |
| `results/table8_liquidity.csv` | the same split at the monthly median relative spread |
| `results/robustness.csv` | sub-periods, wide band, call-only and put-only IV, no-earnings, level sorts, quintiles |
| `results/annual.csv` | 10-1 mean by calendar year |
| `results/coverage.csv` | names per formation month |
| `figures/decile_returns.png` | mean return by decile, three strategies |
| `figures/long_short.png` | monthly and cumulative 10-1 returns |
| `figures/hv_iv.png` | cross-sectional mean HV and IV through time |

`results/panel.parquet` is a rebuildable cache and is gitignored.
