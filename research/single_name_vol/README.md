# research/single_name_vol

The `research/volatility/` horse race, re-run on ~500 single names instead of
one index. Findings are in **[REPORT.md](REPORT.md)**.

## Run it

```bash
uv run python -m research.single_name_vol.panel --refresh   # ~30 min, builds results/panel.parquet
uv run python -m research.single_name_vol.analysis          # seconds, rewrites figures/ and results/
```

The panel is cached because building it inverts an option chain for every name
and day in 2025. Build it in one go if the machine is free; `build_panel` also
takes a symbol list, so a slow environment can run it in a few slices and
concatenate the parts. `analysis.py` reads the cache and never rebuilds it; pass
`--refresh` when the underlying data changes. Needs `options_2025/`,
`underlying_2025.parquet`, `yields.parquet` and `indices.parquet`.

## Files

| File | What it is |
|---|---|
| `panel.py` | builds one row per (symbol, date, horizon): four forecasts, two targets |
| `analysis.py` | pooled and cross-sectional scoring, every figure |
| `iv_validation.py` | the inverted implied vol against ThetaData's own, where the greeks pull covers a name |
| `results/panel.parquet` | the cached panel (a research artefact, not a dataset — nothing here writes to `data_store/`) |
| `figures/*.png`, `results/*.csv` | regenerated on every analysis run |

Shared with `research/volatility/`: `research/vol_models.py` (the forecasters)
and `research/scoring.py` (MZ, the two losses, Diebold-Mariano).

## Design in brief

- **Sample** calendar 2025, S&P 500 names with an option chain. One year is all
  the options pull covers, which forces a 120-day burn-in rather than 250.
- **Forecasts** trailing RV, ARCH(5), GARCH(1,1) — expanding window,
  out-of-sample — and a 30-day ATM implied vol inverted from each name's own
  chain (parity forward, Black-76, interpolated in total variance).
- **Benchmark** MEAN, each name's own expanding-mean vol. A level guess.
- **Targets** realized vol over `t+1..t+h` and the name's own ATM IV at `t+h`,
  for h = 5 and 21.
- **Scoring** the same four layers as the index study — see its report for what
  each one tests: MZ joint calibration, RMSE and QLIKE, pairwise DM,
  encompassing.
- **Validation** the inverted IV is checked against ThetaData's own
  `implied_vol` on every name the `option_greeks` pull covers; the two agree to
  a median 0.07 vol points.

## The two things that differ from the index study

1. **Standard errors have to handle the cross-section.** 500 names on the same
   250 dates share a market factor, so residuals are correlated across the
   panel on every date. Newey-West prices only the time dimension and is
   badly overconfident; every pooled test here uses **Driscoll-Kraay**.
   `results/standard_errors.csv` reports what OLS and NW would have claimed.
2. **Prices are unadjusted, so returns need cleaning first**, and a size
   threshold cannot do it — 2025 has six splits and a spinoff alongside genuine
   one-day crashes past -40%. Returns come from
   `load_underlying(trusted_symbols(), with_actions=True, in_universe=True)`
   with `split_adjusted_return()`; each of those four choices removes a
   different artefact, and the study's returns are wrong without all of them.
