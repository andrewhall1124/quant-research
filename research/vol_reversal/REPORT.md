# Short-horizon reversal in single-name straddle returns, 2018–2025

## Bottom line

The one-month reversal that Heston, Jones, Khorram, Li and Mo (2023) report
is, on this panel, almost entirely the most recent mark reverting. A book
that sells the names whose reference straddle paid the most over the past
week and buys the ones that paid the least earns a gross Sharpe of 4.5
with no costs. Skip a single session — score the same week ending
yesterday instead of today — and the Sharpe falls to 1.05, the 60-session
decile spread from 1.3 points of vega to 0.1, and the intercept t from
12.2 to 2.9. The one-month version behaves the same way: Sharpe 3.3 with
the last session in, 0.8 with it out, and the decile spread changes sign.
What is left after the skip is small, has a short-market-vol tilt, and
disappears at a fraction of the half-spread.

The mechanism is bid-ask bounce in end-of-day mids. A straddle's mid that
prints high today, because one leg's quote moved, prints lower tomorrow;
the reference return records that as a gain followed by a loss, and a
costless book that sells after the gain harvests the loss. The size fits:
the panel's median half-spread is 1.3–1.7 points of vega per unit, and the
one-week decile spread the skip removes is 1.2 points. Nothing about that
is a property of volatility.

| Formation window | 1w | 1w, skip 1 session | 2w | 1m | 1m, skip 1 session | 2m |
| --- | --- | --- | --- | --- | --- | --- |
| decile 1 (lowest recent P&L, bought) forward P&L per $ vega | 2.40 | 1.71 | 2.15 | 1.88 | 1.52 | 1.65 |
| decile 10 (highest recent P&L, sold) | 1.13 | 1.85 | 1.36 | 1.54 | 1.93 | 1.55 |
| 10 − 1 spread | −1.29 | +0.11 | −0.80 | −0.35 | +0.41 | −0.13 |
| rank book: gross P&L / Sharpe | $4.7M / 4.52 | $778k / 1.05 | $3.7M / 3.78 | $2.9M / 3.33 | $577k / 0.77 | $2.1M / 2.62 |
| rank book intercept t | 12.2 | 2.9 | 10.4 | 9.2 | 2.2 | 7.4 |
| rank book market-vol loading t | −3.2 | −4.8 | −5.6 | −6.8 | −6.0 | −8.0 |
| MVO book: gross P&L / Sharpe | $13.4M / 3.78 | $1.2M / 0.38 | $8.8M / 2.92 | $6.0M / 2.11 | $1.1M / 0.42 | $3.4M / 1.27 |
| rank book net of full half-spread, Sharpe | −6.5 | −7.1 | −6.3 | −6.2 | −6.6 | −6.0 |

## Construction

Identical to `research/vol_momentum` with the sign flipped:
`PastReturnConfig(window, skip, sign=+1)` scores a high trailing
reference-straddle P&L as *rich*, so the book sells it. Windows of 5, 10,
21 and 42 sessions with no skip, and 5 and 21 sessions skipping the most
recent session. Same 659 names, 2018-07-02 to 2025-06-30; same decile
table, rank book and mean-variance book on the panel engine; same
full-half-spread row.

## Results

### With the last session in

Every window shows reversal and every book is spectacular. The one-week
rank book earns $4.7M on $20k of gross vega, its intercept has a t of 12,
and the MVO book on the same signal earns $13.4M. The effect fades with the
window — Sharpe 4.5 at one week, 3.8 at two, 3.3 at one month, 2.6 at two
— which is the fingerprint of something that lives in the most recent
observation and gets diluted as the window grows.

### With the last session out

Skipping one session removes most of it. The one-week spread goes from
−1.29 to +0.11 and the book from Sharpe 4.5 to 1.05. The one-month spread
goes from −0.35 to +0.41 — that is, once the last mark is excluded, the
60-session decile table shows *momentum* at one month, in line with the
3–12 month windows in the companion study — and the book from 3.3 to 0.8.
The MVO books fall harder, to 0.4, because the optimizer had been
concentrating in the names with the noisiest marks.

The residual after the skip is a Sharpe of about 0.8–1.0 with a
significant short-market-vol tilt (t −5 to −6): the book that sells recent
winners sells names whose vol just rose, which is short market vol. Some
of the residual may be two-day bounce (a stale quote can sit for more than
one session), some may be the real short-horizon reversal the paper
found. This study cannot separate them; a skip-2 variant and a version
that marks at the far touch would.

### Costs

Every book loses at the full half-spread, at Sharpe −6 to −7. The
reversal books turn over 44–89× their gross vega a year, so this is not
close, and the decile-table evidence is that the signal being paid for is
the spread itself.

## What this says about the machinery

This is the first study to run a short-horizon signal through the panel
engine, and it exposes a limit of that engine as a research tool rather
than a fact about volatility. The reference return is computed from
end-of-day mids. Any signal that conditions on the most recent mid and any
book that trades at the mid without cost will harvest mid reversion, and
the faster the signal the larger the harvest. Two rules follow for
research on the reference panel: a signal built from the reference return
should skip at least one session, and a Sharpe above about 2 gross on
this panel should be assumed to be bounce until a skip test says
otherwise. The costed row is the other guard; nothing that is bounce
survives it, but nothing else does either, so it cannot rank signals.

The paper's monthly returns are less exposed to this because a one-day
bounce is a small share of a one-month return; the daily panel here makes
it the dominant share of a one-week one. Whether their one-month reversal
is bounce, a real effect, or both is not settled by this study; on this
panel the real component, if present, is a fraction of the reported one.

## Caveats

* The skip-1 residual is reported, not explained; see above.
* Sixty-session decile tables are a blunt instrument for a one-week
  signal. The books, rebalanced weekly, are the better read here, and they
  agree with the tables on the sign of every skip effect.
* The reference return's strike phase and the unscreened universe carry
  over from the momentum study.
