# Earnings straddles

ATM straddles around every S&P 500 earnings announcement from 2017 to 2025:
held through the print (last close before the release to first close after)
and, separately, through the five-session run-up into it. Measures the
implied move against the realized move, the IV crush, and what a long or
short straddle earned at the midpoint and inside the spread, with sorts on
the implied move, the name's own history of realized versus implied moves,
the pre-print IV rise and the IV level.

Run from the project root:

```sh
uv run python -m research.earnings_straddles.panel      # ~15s: one row per event
uv run python -m research.earnings_straddles.analysis   # tables and figure
```

`panel.py` reads announcement dates with their session (before open or after
close) from the earnings table, maps each to entry and exit sessions, and
selects the straddle on each window's own entry day. `analysis.py` computes
returns and writes `results/` and `figures/`.

See [REPORT.md](REPORT.md) for the rules, results and caveats.

## Outputs

| File | Holds |
| --- | --- |
| `results/events.csv` | every event: contract, implied and realized move, IV crush, both windows' returns |
| `results/summary.csv` | long and short, each window, at 0/25/50/100% of the quoted spread |
| `results/moves.csv` | distribution of implied move, realized move, their ratio, IV crush, IV rise |
| `results/sorts.csv` | long through-print returns by tercile of four sort variables |
| `results/sort_costs.csv` | the short side of each sort's top tercile inside the spread |
| `results/splits.csv` | by session and by year |
| `results/monthly.csv` | monthly means of the event series |
| `figures/earnings.png` | realized/implied histogram and yearly means |

`results/panel.parquet` is a rebuildable cache and is gitignored.
