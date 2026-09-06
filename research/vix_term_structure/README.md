# Volatility term structure as a timing signal

Sell the monthly index ATM straddle when the volatility curve is in contango
and stand aside (or buy) when it inverts: the classic VIX carry rule, tested
2017–2025. The VIX complex in the store starts in 2024, so the slope is
rebuilt daily from the XSP option chain as 90-day minus 30-day ATM implied
vol, and checked against VIX3M minus VIX where both exist. XSP is used
because it was the one index root quoted through 2020–2021 before the
index chain repair, and its third-Friday expiries settle on the close.

Run from the project root, after the Goyal-Saretto panel exists:

```sh
uv run python -m research.vix_term_structure.panel      # ~10s: daily slope, monthly straddles, daily paths
uv run python -m research.vix_term_structure.analysis   # rules, states, validation, figure
```

See [REPORT.md](REPORT.md).

## Outputs

| File | Holds |
| --- | --- |
| `results/slope_daily.csv` | daily 30d and 90d ATM IV, slope, its trailing-year z-score, VIX and VIX3M where present |
| `results/months.csv` | one row per straddle month: slope at formation, IV, realized variance, naked and daily-hedged short returns |
| `results/rules.csv` | each timing rule at 0/25/50/100% of the quoted spread: mean, t, Sharpe, max drawdown |
| `results/conditional.csv` | what the short straddle and the variance premium did in each slope state |
| `results/validation.csv` | chain slope against VIX3M minus VIX, 2024–2025 |
| `figures/term_structure.png` | IV at two maturities, the slope, cumulative return by rule |

`results/*.parquet` are rebuildable caches and are gitignored.
