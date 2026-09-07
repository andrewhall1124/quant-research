# Running

## Install

voltium is a member of the repo's uv workspace; from the repo root:

```sh
uv sync
uv run pytest voltium/tests            # 22 tests, ~3 s; the loader tests skip without data_store/
```

Outside uv, `pip install -e voltium` installs it with its dependencies
(polars, numpy, cvxpy, clarabel, scipy, matplotlib). Python ≥ 3.13.

## Build the store once

Everything voltium reads is in `data_store/`, written by `data_pipelines`.
The pieces voltium needs beyond the option and stock pulls:

```sh
uv run python -m data_pipelines.cli sectors          # GICS sectors from Wikipedia, seconds
uv run python -m data_pipelines.cli vol-risk-model   # reference returns + risk model, ~25 min
```

`data_pipelines.cli build` includes both in order. See
[risk_model.md](risk_model.md) for what the second one writes and when it
needs rerunning (any change to the instrument).

## The demos

```sh
uv run python voltium/demos/level_book.py       # VRP signal, 2025-01-02 .. 2025-06-30
uv run python voltium/demos/iv_zscore_book.py   # time-series IV z-score, same dates
```

Common flags (`build_parser` in `level_book.py`):

| flag | default | meaning |
| --- | --- | --- |
| `--start`, `--end` | 2025-01-02, 2025-06-30 | backtest window |
| `--rebalance` | `weekly` | or `daily` |
| `--rv-source` | `ohlc` | `ohlc`: Yang-Zhang from the stock file (2023-06 on); `close`: close-to-close off the chains (2017 on), size control dropped |
| `--in-process` | off | estimate the risk model on the fly instead of reading the stored tables |
| `--refresh` | off | rebuild the cached panels |
| `--limit N` | | first N symbols (smoke test; the VRP signal needs ≥ 50 names to regress) |
| `--window` | 250 | `iv_zscore_book.py` only: the z-score window |

Each demo prints `summary()`, the factor regression and the decile table,
writes `demos/records_<tag>.parquet` and `demos/portfolio_<tag>.json`, and
`demos/figures/equity_curve_<tag>.png` / `deciles_<tag>.png`. The tag is the
signal name, suffixed with the start and end year on the `close` path
(`vrp_2018_2025`). The committed `demos/*.log` files are the last runs.

`level_book.py` is structured as `build_panels(args) -> Panels`,
`build_vrp_signal(panels)`, `run_book(args, panels, signal, tag)`; a new demo
is those three lines with a different signal (`iv_zscore_book.py` is 30
lines).

## What a run does, and how long it takes

| stage | six months, `ohlc` | seven years, `close` |
| --- | --- | --- |
| universe, sectors, calendar | < 1 s | < 1 s |
| reference returns (stored) | 1 s | 1 s |
| surface panel (per symbol, cached) | ~30 s cold, 1 s cached | ~10 min cold |
| realized vol + HAR | 5 s | ~1 min |
| stock features | 1 s | ~1 min |
| signal | 5 s | ~30 s |
| backtest | ~12 min (122 sessions) | ~3.5 h (1,758 sessions) |

The backtest is the per-session chain scan (`StoreChainProvider`, 1–3 s per
session on 500+ files) plus the optimizer on rebalance days. The
`--in-process` path adds ~4 min for reference paths on the six-month window.

## Caches

`voltium/.cache/` (gitignored):

| directory | contents | keyed on |
| --- | --- | --- |
| `surface/` | one surface panel per symbol | symbol, history start, forward end |
| `rv_close/`, `spot/` | close-to-close RV and spot panels per symbol (`--rv-source close`) | same |
| `reference/` | reference paths per symbol (`--in-process` only) | symbol, dates, **instrument config hash** |

Delete a directory or pass `--refresh` to rebuild. A changed
`StraddleConfig` invalidates `reference/` automatically; it does not
invalidate the stored tables, which need `vol-risk-model --force`.

## Memory

An 18 GB machine ran out of memory on a single streaming query over 659
names by eight years of chains. Panels are therefore built one symbol at a
time (`providers/base.py: materialize_by_symbol`) and concatenated, which
bounds memory at one chain (~100k rows a year) and keeps the demo under
1 GB. The rule generalises: never `collect()` a plan over more than one
symbol's full history; loop and concat.

## Reproducibility

Runs are deterministic bit for bit. Two things had to be true for that:

* Every selection tie is broken by an explicit key — two monthlies
  equidistant from the target DTE, two strikes equidistant in delta from
  0.50 — never by row order, which varies across parquet reader threads.
  The two-month full-universe check matches to the cent across runs.
* Everything is point-in-time: the HAR fits only on closed targets, the
  rolling estimates use sessions ≤ t, the stored tables are built forward.
  `tests/test_realized_vol.py` asserts the forecast is unchanged when the
  future is scrambled.

The backtest is still *sensitive*: contracts are integers, so a
one-contract rounding flip propagates through the no-trade band and the
turnover penalty for the rest of the run. Two configurations that differ
slightly can diverge a lot in net P&L for that reason; compare gross
per-vega numbers and decile tables, which are stable, before comparing net
dollars.

## Background runs

The long-history books take hours; start them with `nohup` so a dead
terminal does not kill them, and watch the log:

```sh
nohup uv run python voltium/demos/level_book.py --rv-source close --start 2018-07-02 --end 2025-06-30 \
    > voltium/demos/level_book_long.log 2>&1 &
grep -v "vega convention" voltium/demos/level_book_long.log | tail
```

Every session logs `positions`, day P&L and cumulative P&L every ten
sessions. A rerun after an interruption skips every cached panel and starts
the backtest within a minute.
