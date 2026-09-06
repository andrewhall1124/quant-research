# Cross-section of the variance premium with a forecast

Replaces the twelve-month trailing realized vol in the Goyal-Saretto sort
with an out-of-sample forecast of next month's realized variance (a pooled
log-HAR refit every formation month), and adds Cao and Han's idiosyncratic
volatility sort. Each signal is scored on the hold-to-expiry straddle and
static-hedged returns from the baseline and on the daily delta-hedged
straddle from the variance-premium study.

Run from the project root, after the Goyal-Saretto and variance-premium
panels exist:

```sh
uv run python -m research.vrp_cross_section.panel      # ~15s: HAR components, idio vol, on formation days
uv run python -m research.vrp_cross_section.analysis   # forecasts, sorts, tables, figure
```

See [REPORT.md](REPORT.md).

## Outputs

| File | Holds |
| --- | --- |
| `results/signals.csv` | 10-1 mean, t and Sharpe per signal and strategy, plus each signal's rank correlation with the realized premium |
| `results/deciles.csv` | mean return by decile per signal |
| `results/forecast_quality.csv` | how well HAR, 21-day RV, 12-month RV and IV each predict realized variance over the hold |
| `results/har_coefficients.csv` | the pooled HAR fit at every formation day |
| `figures/deciles.png` | decile curves for the straddle and the daily-hedged straddle |

`results/*.parquet` are rebuildable caches and are gitignored.
