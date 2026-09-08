# Earnings-adjusted variance premium

Asks how much of the cross-sectional variance-premium signal in voltium is
earnings timing. `iv60` contains the next print's jump variance when the
print is inside 60 days and not otherwise, while the 60-session realized-vol
forecast almost always spans a print, so the raw premium is mechanically
higher for names whose announcement is near. The study measures that
mechanical effect, strips the jump from the surface with a term-structure
estimate (`voltium.providers.earnings`), takes the earnings sessions out of
the realized side, and rescores the signal on deciles and on a rank-weighted
book.

Run from the project root, with the voltium store and its caches in place
(`data_pipelines.cli vol-risk-model`; the first run also builds the raw
panels the demos use):

```sh
uv run python -m research.earnings_adjusted_vrp.panel      # ~12 min cold: adjusted surface, ex-earnings RV, forecasts
uv run python -m research.earnings_adjusted_vrp.analysis   # ~2 min: tables, books, figure
```

See [REPORT.md](REPORT.md).

## Outputs

| File | Holds |
| --- | --- |
| `results/by_days_to_event.csv` | raw premium, signal, decile shares and forward P&L by distance to the next print |
| `results/jump_estimates.csv` | implied earnings move by estimation method and by year |
| `results/deciles.csv` | forward 60-session P&L per $ vega by decile, raw / IV-adjusted / fully adjusted signal |
| `results/books.csv` | rank-weighted book summary for each signal |
| `results/book_series.csv` | daily book P&L for each signal |
| `figures/earnings_adjusted_vrp.png` | decile curves and forward P&L by days to event |

`results/*.parquet` are rebuildable caches and are gitignored.
