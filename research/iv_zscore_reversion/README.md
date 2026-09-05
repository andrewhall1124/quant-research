# iv_zscore_reversion

Daily cross-sectional implied-vol mean reversion in S&P 500 single-name
straddles, 2017-2025. Each day: build the ~30-dte ATM straddle for every name,
z-score its implied vol against its own 60-session history, buy the cheapest
decile and sell the richest, size to equal dollar vega on both sides, delta
hedge, hold one session.

**Findings are in [REPORT.md](REPORT.md).** The short version: the strategy
looks superb (gross Sharpe 11.0) and is not real. One session of
implementation lag takes it to 1.09, and the gross edge is 4.6% of the quoted
spread it must cross twice a day.

## Running it

```bash
# Once. Full pass over the option-greeks store, ~25s at 8 workers.
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

## The one thing to know before reusing this code

`Config.signal_lag` is the whole study. At `signal_lag=0` the z-score and the
entry price come from the same closing quote, so the bid-ask bounce in that
quote is both the reason a name looks cheap and the reason it appears to make
money. Any straddle study that ranks on a mid and executes at the same mid has
this problem. `signal_lag=1` is the honest number.
