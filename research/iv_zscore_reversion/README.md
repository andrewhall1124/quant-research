# iv_zscore_reversion

Daily cross-sectional implied-vol mean reversion in S&P 500 single-name
straddles, 2017-2025. Each day: build the ~30-dte ATM straddle for every name,
z-score its implied vol against its own 60-session history, buy the cheapest
decile and sell the richest, size to equal dollar vega on both sides, delta
hedge, hold one session.

**Findings are in [REPORT.md](REPORT.md).** The short version: it does not work,
and it fails twice independently. Entered at a close the signal did not read,
the gross Sharpe is **0.31 (t = 0.92)** with a flat decile gradient and no
specification in a twelve-cell grid clearing t = 2. And the book crosses
**$199,000 of quoted spread a day** to chase $224 — it can pay 0.03% of the
spread where a real crossing costs ~50%.

Run as literally specified, with signal and entry from the same closing print,
it shows a gross Sharpe of **10.1**. Working out why is what the study is
actually about.

## Running it

```bash
# Once. Full pass over the option-greeks store, ~30s at 8 workers.
uv run python -m research.iv_zscore_reversion.panel

# Every figure and table in REPORT.md, ~2 min.
uv run python -m research.iv_zscore_reversion.analysis
```

## Layout

| file | what it does |
| --- | --- |
| `panel.py` | Picks the ~30-dte ATM straddle per symbol-day and attaches the *same two contracts'* quote at the next close. Caches to `results/straddle_panel.parquet` (1.05M symbol-days). |
| `portfolio.py` | Screens, z-score signal, decile ranking, equal-vega sizing, delta-hedged P&L, cost model, HAC statistics. One `Config` per specification. |
| `analysis.py` | Runs the specifications, writes `results/*.csv` and `figures/*.png`. |

`results/straddle_panel.parquet` is an expensive intermediate cached under the
study, per the repo convention — `data_store/` still belongs to the pipelines.
It is gitignored; `panel.py` rebuilds it.

## Two things to know before reusing this code

**1. `Config.signal_lag` is the whole study.** At `signal_lag=0` the z-score and
the entry price come from the same closing quote, so the bid-ask bounce in that
quote is both the reason a name looks cheap and the reason it appears to make
money. It is worth a gross Sharpe of 10.1 out of thin air. Any straddle study
that ranks on a mid and executes at the same mid has this problem. The
confirming diagnostic is `spread_quintile_stats` — measurement error scales with
the quoted width (9.7x here) and a real repricing does not.

**2. The panel reads the chain loosely and selects strictly, and the gap between
those two bands is load-bearing.** Looking for tomorrow's mark inside the same
strict filter used for selection is a look-ahead screen: it deletes a position
whenever its quote went one-sided or its IV stopped inverting overnight, which
is exactly what happens when the underlying gaps. In the first version of this
study that screen touched 0.18% of rows and more than doubled the headline
t-statistic. Positions that are genuinely unmarkable are carried flat, never
dropped.

`Config` also carries three deliberate defects — `require_next_two_sided`,
`full_sample_zscore`, `static_universe` — used only by the look-ahead audit in
`analysis.py`, which prices what each mistake would have been worth. All three
default to the honest setting.
