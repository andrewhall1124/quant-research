# Volatility reversal

The short-window companion to `research/vol_momentum`. Heston, Jones,
Khorram, Li and Mo (2023) find that the most recent month of delta-hedged
straddle returns *reverses*, while longer windows continue. This study
sorts on the trailing 1-, 2-, 4- and 6-week reference-straddle P&L with the
sign flipped (a high recent return is treated as *rich* and sold), and
runs the same rank-weighted and mean-variance books on the panel engine.

Run from the project root, after `research.vol_momentum.panel`:

```sh
uv run python -m research.vol_reversal.analysis   # ~6 min
```

See [REPORT.md](REPORT.md). Outputs mirror the momentum study's
(`results/summary.csv`, `results/deciles.csv`, `results/book_series.csv`,
`figures/vol_reversal.png`).
