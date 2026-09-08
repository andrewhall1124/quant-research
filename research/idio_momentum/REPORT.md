# Idiosyncratic volatility momentum, 2018–2025

## Bottom line

Removing the factor part of the reference return before measuring momentum
does not help. The residual momentum sort is about 20% weaker than the raw
one at every window, the rank book earns a little less at the same risk,
and the long market-vol tilt that motivated the study is reduced but not
removed. Scaling the residual by its own volatility, the Blitz–Huij–Martens
construction, is weaker again. The factor component of a name's past
straddle P&L carries part of the predictive content, and the book's tilt
is not mainly a consequence of the signal selecting high-loading names.

| 6 months, skip 1 month | raw (parent study) | residual | scaled residual |
| --- | --- | --- | --- |
| decile 1 − decile 10, forward 60-session P&L per $ vega | 2.32 | 1.81 | 1.69 |
| rank book: gross P&L / Sharpe / max drawdown | $523k / 0.82 / −$117k | $494k / 0.79 / −$129k | $409k / 0.72 / −$119k |
| rank book market-vol loading (t) | +0.04 (5.2) | +0.03 (4.1) | +0.03 (4.2) |
| MVO book: gross P&L / Sharpe | $1.04M / 0.49 | $1.20M / 0.56 | $267k / 0.17 |

| 12 months, skip 1 month | raw | residual | scaled residual |
| --- | --- | --- | --- |
| decile spread | 1.98 | 1.58 | 1.60 |
| rank book Sharpe | 0.93 | 0.82 | 0.78 |
| rank book market-vol loading t | 8.3 | 6.4 | 6.2 |
| MVO book Sharpe | 0.44 | 0.24 | 0.25 |

## Construction

**Residual.** For every name and session,
`residual = pnl_per_vega − Σ_f loading_{t−1,f} × factor_return_{t,f}`
with the stored risk model's loadings (market and own sector) as of the
previous session and the stored factor returns, so the residual is point in
time. The factors explain 17.8% of the variance of the reference return
across the panel; the other 82% is what this study sorts on.

**Scaled.** The residual divided by its own trailing 252-session standard
deviation, so a name's past P&L is measured in its own units before
summing.

**Everything else** is the parent study's: `PastReturnSignal` on the
residual panel at 3, 6 and 12 months skipping the last month, with the
1-month and 12-month-no-skip controls; decile tables, rank book and MVO
book on the panel engine, weekly, $20k gross vega, no costs. The books
always trade the actual reference unit; only the signal changes.

## Results

**The sort weakens.** At 6 months the residual spread is 1.81 against the
raw 2.32; at 12 months 1.58 against 1.98. The deciles stay monotone. Some
of the momentum in raw returns is momentum in a name's factor exposure
times persistent factor returns, and removing it removes signal.

**The tilt shrinks but stays.** The point of the study was the raw book's
long market-vol loading (t 8.3 at 12 months). Residualising the returns
before sorting brings it to t 6.4, not to zero. A book that is long names
with high past *residual* P&L and short names with low still ends up long
the market factor. The likely reasons: the loadings are 250-session
estimates on a series whose factor share is small, so residuals keep
factor content; and a centred-rank book is not beta-neutral by
construction. The remedy is on the book side — a `FactorNeutral`
constraint, or a beta-adjusted rank weight — rather than on the signal
side.

**The optimizer.** The residual MVO book at 6 months is the one place the
optimizer beats its raw counterpart (Sharpe 0.56 against 0.49), with a
smaller tilt (t 0.1). Elsewhere it is as concentrated and as poor as in
every other study on this panel; see the risk-aversion sweep in
`voltium/docs/experiments.md` for why.

**Scaling by volatility hurts.** Dividing by the residual's own trailing
standard deviation is meant to stop volatile names dominating the sort. On
this panel it lowers the spread and every book statistic. A name's raw
past P&L per dollar of vega, volatile or not, is the better ranking
variable, which is consistent with the parent study's finding that the
signal is partly a vol-level effect.

## What to do with it

Use raw momentum, skip the last month, and neutralise the tilt on the book.
Residualising the signal costs about a fifth of the effect and does not
buy the neutrality it was meant to. The next test is the raw 12-month
signal in the MVO book with `FactorNeutral` restored, against a
beta-adjusted rank book, on the panel engine.

## Caveats

* The residual depends on the stored risk model; a different factor set
  would give a different residual. The market and own-sector model is a
  weak one for this series (18% of variance).
* The loadings are lagged one session, which is the minimum. They are
  estimated on trailing 250-session windows and change slowly, so the
  lag matters little, but a full-sample fit would leak.
* All other caveats of the parent study apply: strike phase, no
  liquidity screen, one seven-year sample, costs untreated.
