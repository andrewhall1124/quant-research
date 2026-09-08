# Volatility momentum in S&P 500 single names, 2018–2025

## Bottom line

There is momentum in delta-hedged straddle returns, as Heston, Jones,
Khorram, Li and Mo (2023) report, and it is the strongest cross-sectional
sort this repository has produced. Names whose reference straddle earned
the most over the past 6 months (skipping the most recent month) went on to
earn 2.4 points of vega over the next 60 sessions; names whose straddle
earned the least went on to earn 0.1. That 2.3-point spread is larger than
the VRP signal's 1.8 (decile 1 minus decile 10, same panel, same horizon),
is monotone across the deciles, and holds at 3, 6 and 12 months. The most
recent month reverses, sharply: a one-month momentum book loses at a Sharpe
of −3.3, which is the subject of `research/vol_reversal`.

What the sort promises the book does not fully deliver. The rank-weighted
12-month book earned $603k on $20k of gross vega, Sharpe 0.93, against the
VRP rank book's $1.07M and 1.56 on the same panel. Two reasons, both
measurable: the book carries a persistent long market-vol tilt (loading
+0.06, t 8.3) that the sort does not see, and a weekly book on a
60-session signal trades 31× its gross vega a year, which is where the
mean-variance version, at 27× and Sharpe 0.44, also pays. Net of the full
half-spread every book here loses, as every book on this panel does.

| Formation window (skip 1 month) | 3m | 6m | 12m | 12m, no skip | 1m |
| --- | --- | --- | --- | --- | --- |
| decile 1 (highest past P&L) forward P&L per $ vega | 2.23 | 2.44 | 2.51 | 2.30 | 1.54 |
| decile 10 (lowest past P&L) | 0.66 | 0.12 | 0.53 | 0.54 | 1.88 |
| 10 − 1 spread (t, overlap-adjusted) | −1.57 (−1.9) | −2.32 (−2.9) | −1.98 (−2.4) | −1.77 (−2.0) | +0.35 (0.3) |
| rank book: gross P&L / Sharpe / max drawdown | $297k / 0.45 / −$183k | $523k / 0.82 / −$117k | $603k / 0.93 / −$122k | −$395k / −0.53 / −$398k | −$2.9M / −3.33 / −$3.0M |
| rank book market-vol loading (t) | +0.04 (5.0) | +0.04 (5.2) | +0.06 (8.3) | +0.08 (10.9) | +0.06 (6.8) |
| MVO book: gross P&L / Sharpe / max drawdown | $309k / 0.14 / −$679k | $1.04M / 0.49 / −$241k | $861k / 0.44 / −$311k | −$921k / −0.45 / −$1.47M | −$6.0M / −2.11 / −$6.0M |
| rank book net of full half-spread, Sharpe | −6.1 | −5.9 | −5.7 | −5.8 | −7.2 |

## Construction

**Signal.** `voltium.providers.signals.PastReturnSignal`: for each name,
the sum of the stored reference straddle's `pnl_per_vega` over `window`
sessions ending `skip` sessions ago, z-scored across names each session,
clipped at ±3, and signed so that a high past return is *cheap* in the
book's richness convention. The reference return is the daily P&L of one
long 60-day ATM straddle, delta-hedged at the close and rolled at 20 days
or a 15% drift, per dollar of inception vega — the same series the risk
model is estimated on and the same unit the panel engine trades. The
paper's returns are monthly and per dollar of premium; ours are daily and
per dollar of vega, which is why the numbers are not comparable in level,
only in sign and ranking.

**Windows.** 1 month with no skip (the paper's reversal horizon); 3, 6 and
12 months skipping the most recent month; 12 months without the skip as a
control. 659 names, 2018-07-02 to 2025-06-30, 1,758 sessions.

**Scoring.** Three views per window:

* the 60-session decile table of the long reference straddle, and the daily
  decile-10-minus-decile-1 series; the reported t divides the naive t by
  √60 for the overlapping horizon, which is conservative;
* a rank-weighted book (`RankWeightedStrategy`, centred ranks, net vega
  neutral, $20k gross vega, weekly) on the panel engine;
* the voltium mean-variance book (stored factor risk model, IC-scaled
  alphas at IC 0.04, λ 1e-5, turnover penalty 0.10 per vega per session,
  net-vega ±$500 and $20k gross constraints), same engine and cadence.

Books are gross unless stated. The last row of the table is the rank book
with the panel's full half-spread charged on every fill plus the reference
path's own roll and hedge costs; nothing on this panel survives that, and
the row is there to fix the scale of the cost problem, not to rank signals.

## Results

### The sort

Momentum is monotone at every skip-one-month window (figure, middle panel).
At 6 months decile 1 earns 2.44 and decile 10 earns 0.12; the middle
deciles fall in order. At 12 months the top is 2.51 and the bottom 0.53.
The effect is not a single bad decile: deciles 8 and 9 are also below
average. Compare the VRP sort on the same panel, where deciles 1–9 are
flat at 1.7–2.3 and only decile 10 collapses. Momentum spreads the whole
cross-section; the VRP identifies one tail.

The one-month window inverts: decile 10 (the worst recent month) earns
1.88, decile 1 earns 1.54. Small at a 60-session horizon, because the
reversal is fast and the horizon averages over it; the book, which
rebalances weekly, sees it in full.

### The books

The rank book earns $300k–$600k gross over seven years at Sharpe
0.45–0.93, improving with the formation window. That is a fraction of the
sort's promise and well under the VRP rank book (Sharpe 1.56). Three
things account for the gap.

*Market-vol tilt.* Every momentum book loads positively on the market
straddle factor at t of 5–11. Names whose straddles paid over the past
year are names whose vol rose, and buying them buys market vol exposure
that a net-vega-neutral constraint does not remove. The VRP signal is
residualised against the market before it reaches the book; this one is
not. A residualised momentum signal, or the factor-neutral constraint the
demos dropped, is the obvious next test.

*Speed mismatch.* The signal moves slowly and the book rebalances weekly,
so most of its 31× annual turnover is noise trading around a stable rank.
The 12-month book turns over less than the 3-month book (31× against 40×)
and earns more.

*The no-skip control.* Including the most recent month costs the 12-month
book $1M of gross P&L and turns its Sharpe negative, even though the
60-session decile spread barely moves (−1.98 to −1.77). The reversal
component is small at a 60-session horizon and dominant at a one-week
horizon. Any momentum book has to skip the last month, and the paper's
result that the two horizons have opposite signs is confirmed on this
panel with daily data.

*The optimizer.* The MVO book has a higher gross P&L at 6 months ($1.04M
against $523k) but a lower Sharpe (0.49 against 0.82) and twice the
drawdown, with most of its P&L in 2024–2025 (figure, right). That is the
same pattern as in the VRP and IV z-score books: without the per-name cap
it buys return with concentration. Its turnover is lower (27–33×), so the
turnover penalty is doing something; the risk model is not.

## Caveats

* The reference return has a strike phase (see
  `voltium/docs/experiments.md`): which strike a name's straddle sits on
  depends on when it last re-struck. A name's past P&L is partly the
  history of its own re-strike timing. The signal inherits that noise and
  the panel-engine book trades exactly the unit that generated it.
* The panel engine reproduces a fractional, unscreened book with no
  liquidity filter, so the long-decile names may include ones with wide or
  stale quotes whose marks drift and mean-revert.
* Seven years is one sample, and a third of the 12-month book's P&L came
  in the last eighteen months.
* Costs are the binding constraint on this panel and are not addressed
  here: the full-half-spread row is uniformly catastrophic, and the
  question of what fraction of the spread a patient book pays is the same
  open question as for every other signal.
