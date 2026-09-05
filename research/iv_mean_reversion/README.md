# IV mean reversion

A fresh, unhedged SPXW straddle baseline built directly on the data access
layer. No previous study code or results are used.

Run from the project root:

```sh
uv run python -m research.iv_mean_reversion.analysis
uv run python -m unittest tools.backtest.test_straddle
```

Optional `--start-year` and `--end-year` restrict the sample. A new run replaces
this study's outputs. Defaults are 2017–2025, a 60-session trailing IV z-score,
±1 thresholds, one session implementation lag and five-session holds.

See [REPORT.md](REPORT.md) for exact rules, results and limitations.
`results/signals.csv` records the rolling signal and selected contract;
`trades_*.csv` and `daily_*.csv` contain reconciled ledgers for each scenario.
Only research outputs are written; source data is unchanged.
