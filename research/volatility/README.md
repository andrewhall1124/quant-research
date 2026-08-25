# research/volatility

Do realized, ARCH, GARCH and implied volatility forecast forward realized vol
and forward implied vol? Findings are in **[REPORT.md](REPORT.md)**.

## Run it

```bash
uv run python -m research.volatility.analysis    # ~1 min, rewrites figures/ and results/
```

Needs `indices.parquet` (SPX + VIX complex) and, for the validation figure,
`index_options_2025/SPXW.parquet` and `yields.parquet`. Rebuild any of them with
`uv run python -m data_pipelines.reference` / `... .options --symbols SPXW`.

## Files

| File | What it is |
|---|---|
| `analysis.py` | the whole study: panel, horse race, scoring, every figure |
| `volatility_models.py` | the four forecasters, all returning annualized decimal vol for `t+1..t+h` |
| `implied_vol.py` | Black-76 inversion of the SPXW chain, used only to check VIX |
| `figures/*.png` | regenerated on every run |
| `results/*.csv` | the numbers behind the report's tables |

## Design in brief

- **Sample** SPX daily closes, 2024-01-03 to 2025-12-31 (501 trading days).
- **Horizons** 5 and 21 trading days, each paired with the implied index that
  spans it: VIX9D and VIX.
- **Forecasts** trailing RV, ARCH(5), GARCH(1,1), and IV — all using
  information through `t` only. ARCH/GARCH use an expanding window with a
  250-day burn-in and refit every 5 origins, so every number is out-of-sample.
- **Targets** realized vol over `t+1..t+h`, and the implied index at `t+h`.
- **Scoring** RMSE, MAE, bias, correlation, Mincer-Zarnowitz regression,
  Diebold-Mariano tests against IV, and a joint encompassing regression.
- **Inference** horizons overlap, so every regression uses Newey-West errors
  with `h` lags. Without that the t-statistics would be roughly `sqrt(h)` times
  too large.

Volatility is defined `sqrt(252 · mean(r²))` everywhere — target, predictor and
GARCH forecast alike — so the comparison is like for like.
