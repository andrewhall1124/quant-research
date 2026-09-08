# Volatility momentum

Heston, Jones, Khorram, Li and Mo (2023, *Option Momentum*) find that past
delta-hedged straddle returns predict future ones: momentum over 2–12 month
formation windows, reversal at one month. voltium already stores the daily
per-vega P&L of a delta-hedged reference straddle for every S&P 500 name
since 2017, so the signal is a trailing sum of a column. This study sorts
on it at formation windows of 1, 3, 6 and 12 months (skipping the most
recent month), and runs a rank-weighted and a mean-variance book on each.
`research/vol_reversal` does the short windows.

Run from the project root, with the voltium store in place:

```sh
uv run python -m research.vol_momentum.panel      # ~1 min: reference returns, features, sectors for the window
uv run python -m research.vol_momentum.analysis   # ~8 min: deciles, rank and MVO books per formation window
```

See [REPORT.md](REPORT.md).

## Outputs

| File | Holds |
| --- | --- |
| `results/summary.csv` | per formation window: decile 1 and 10 forward P&L, the 10−1 spread and its t, rank- and MVO-book Sharpe, P&L, drawdown, turnover, market loading |
| `results/deciles.csv` | full decile tables per window |
| `results/book_series.csv` | daily gross P&L of every book |
| `figures/vol_momentum.png` | 10−1 spread by window, decile curves, cumulative book P&L |

`results/*.parquet` are rebuildable caches and are gitignored.
