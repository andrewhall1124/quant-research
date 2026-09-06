# IV skew and term slope

Two shape-of-the-surface signals tested on S&P 500 names, 2017–2025, with
the Goyal-Saretto calendar and contracts:

- **Skew** (Xing, Zhang and Zhao 2010): OTM put IV minus ATM call IV on the
  one-month expiry. Claim: steep skew predicts low stock returns.
- **Term slope** (Vasquez 2017): 90-day ATM IV minus 30-day ATM IV. Claim:
  steep upward slopes predict high straddle returns.

Each is sorted into deciles within month and scored on the next-month stock
return, the hold-to-expiry straddle and static-hedged legs, and the daily
delta-hedged straddle, on the full panel and with earnings months removed.

Run from the project root, after the Goyal-Saretto and variance-premium
panels exist:

```sh
uv run python -m research.iv_skew_slope.panel      # ~20s: skew and slope on formation days
uv run python -m research.iv_skew_slope.analysis   # sorts, table, figure
```

See [REPORT.md](REPORT.md).

## Outputs

| File | Holds |
| --- | --- |
| `results/sorts.csv` | 10-1 mean and t per sort and outcome; straddle spread at half the quoted spread |
| `results/deciles.csv` | per decile: signal level, IV, earnings share, mean of each outcome |
| `figures/deciles.png` | skew deciles against stock returns; slope deciles against straddle returns |

`results/features.parquet` is a rebuildable cache and is gitignored.
