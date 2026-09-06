# Goyal-Saretto variants

Sixteen modifications of the log(HV/IV) straddle sort replicated in
[`research/goyal_saretto`](../goyal_saretto), each scored the same way on the
same 2017–2025 S&P 500 stock-month panel. The baseline found no return spread;
this asks whether a cleaner signal, a different reference for "normal" IV, a
hedged straddle, or an earlier exit produces one.

Run from the project root, after the baseline panel exists:

```sh
uv run python -m research.goyal_saretto_variants.extras     # ~80s, adds IV history, ex-earnings HV, exit marks
uv run python -m research.goyal_saretto_variants.analysis   # scores every variant
```

`extras.py` reads the chains once more for three things the baseline never
needed: a daily ATM IV series per symbol, realized vol with earnings days
removed, and midpoint marks on the selected contract 3, 5, 10 and 15 sessions
after entry. `analysis.py` imports the baseline's decile and return machinery
and only adds the variant signals and exits.

See [REPORT.md](REPORT.md) for what each variant is and what it did.

## Outputs

| File | Holds |
| --- | --- |
| `results/variants.csv` | one row per variant: 10-1 mean, t, Sharpe for three strategies; straddle spread at 25/50/100% of quoted spread; short-decile-1 leg at the same costs |
| `results/variants_monthly.csv` | monthly 10-1 returns per variant |
| `results/variants_deciles.csv` | mean return by decile per variant |
| `figures/variants.png` | 10-1 straddle mean at midpoint and at half the spread; cumulative 10-1 for seven variants |

`results/extras.parquet` is a rebuildable cache and is gitignored.
