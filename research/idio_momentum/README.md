# Idiosyncratic volatility momentum

`research/vol_momentum` found momentum in past reference-straddle P&L and
a book that carried a long market-vol tilt the sort did not see. This
study removes the factor part first: the daily reference return is
regressed out against the stored risk model's market and own-sector factor
returns (loadings as of the previous session), and the momentum signal is
built on the residual. Two versions: the plain residual sum, and the
residual scaled by its own trailing volatility (Blitz, Huij and Martens,
2011, residual momentum). Same windows, deciles, rank and MVO books as the
parent study.

Run from the project root, after `research.vol_momentum.panel`:

```sh
uv run python -m research.idio_momentum.panel      # seconds: residual reference returns
uv run python -m research.idio_momentum.analysis   # ~15 min
```

See [REPORT.md](REPORT.md). Outputs mirror the momentum study's, with a
`version` column (`residual`, `scaled`) in `results/summary.csv`.
