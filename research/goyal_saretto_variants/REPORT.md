# Modifying the Goyal-Saretto sort on S&P 500 options

## Bottom line

None of sixteen modifications turns the log(HV/IV) sort into a strategy on
2017–2025 S&P 500 options. The best midpoint 10-1 straddle spread is 2.1% a
month with a t-statistic of 0.7, and every variant is negative once a quarter
of the quoted spread is paid. One variant briefly looked strong, an exit ten
sessions after entry, and that turned out to be a marking bug rather than a
result. The modifications were chosen to attack the two problems the baseline
diagnosed, an earnings-calendar sort and a noisy hold-to-expiry payoff, and
neither fix produces a premium.

## Variants

All share the baseline panel: ATM straddle selected on the session after the
monthly expiry, entered the next session at mid, S&P 500 members only, deciles
on the signal, equal weights, 10-1 spread. Names in the table below are the
ones in `results/variants.csv`.

**Signal.**

- `hv 63d`, `hv 21d`: HV over 63 or 21 sessions instead of 252. Tests
  whether the paper's twelve-month window is too slow for the modern IV term.
- `ex-earnings hv, no earnings hold`: twelve-month HV with the earnings day
  and the session after it removed, and stock-months with a report inside the
  hold dropped. This removes the earnings calendar from both sides of the ratio.
- `iv vs own 12m mean`: log of the trailing 252-session mean of daily ATM IV
  over today's IV. The paper mentions this in unreported results as the same
  phenomenon; here it is a signal in its own right.
- `iv z-score vs own 12m`: the same gap scaled by the trailing IV standard
  deviation.
- `rank(hv/iv) + rank(iv history)`: mean of the two cross-sectional ranks.
- `combined, ex-earnings`: the rank average with the ex-earnings HV, on the
  no-earnings sample.

**Position.**

- `baseline, zero-delta straddle`: the paper's footnote 10. The net delta of
  the pair is hedged with stock at entry, gain scaled by the straddle price.

**Exit.**

- `baseline, exit after 3/5/10/15 sessions`: close at the exit-day midpoint
  instead of settling at expiry, so the return is an IV move plus theta rather
  than a realized-move lottery. Three signal variants are combined with the
  10-session exit.

## Results

10-1 straddle spread per month, and delta-hedged call spread, both at the
midpoint; then the straddle spread with decile 10 bought and decile 1 sold at
a quarter and a half of the quoted spread. Full table with Sharpe ratios,
put-hedged spreads and the short leg alone in `results/variants.csv`.

| Variant | Names/mo | Straddle 10-1 | t | DH call 10-1 | t | @25% spread | @50% spread |
| --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | 288 | -0.2% | -0.07 | 0.33% | 1.30 | -3.1% | -6.0% |
| hv 63d | 288 | -1.3% | -0.49 | 0.09% | 0.37 | -4.2% | -7.0% |
| hv 21d | 288 | 1.7% | 0.60 | 0.35% | 1.64 | -1.1% | -3.9% |
| ex-earnings hv, no earnings hold | 202 | -3.1% | -0.88 | 0.15% | 0.45 | -6.2% | -9.4% |
| iv vs own 12m mean | 280 | 2.0% | 0.60 | 0.26% | 1.04 | -0.8% | -3.7% |
| iv z-score vs own 12m | 280 | 2.1% | 0.69 | 0.36% | 1.40 | -0.7% | -3.6% |
| rank(hv/iv) + rank(iv history) | 280 | -2.7% | -0.93 | 0.13% | 0.53 | -5.5% | -8.3% |
| combined, ex-earnings | 197 | -2.9% | -0.90 | 0.00% | 0.00 | -6.0% | -9.0% |
| baseline, zero-delta straddle | 288 | 1.3% | 0.46 | 0.33% | 1.30 | -1.5% | -4.4% |
| baseline, exit after 3 sessions | 288 | -0.2% | -0.33 | 0.07% | 0.89 | -5.8% | -11.5% |
| baseline, exit after 5 sessions | 288 | -1.1% | -1.39 | -0.00% | -0.03 | -6.6% | -12.2% |
| baseline, exit after 10 sessions | 288 | -0.7% | -0.40 | 0.11% | 1.05 | -6.2% | -11.9% |
| baseline, exit after 15 sessions | 288 | -1.1% | -0.46 | 0.17% | 0.91 | -6.8% | -12.6% |
| ex-earnings, exit after 10 | 202 | -0.9% | -0.52 | 0.12% | 0.74 | -7.1% | -13.4% |
| iv vs own 12m mean, exit after 10 | 280 | 0.0% | 0.01 | 0.01% | 0.07 | -5.5% | -11.1% |
| combined, ex-earnings, exit after 10 | 197 | -2.5% | -1.29 | -0.15% | -0.90 | -8.5% | -14.6% |

Selling decile 1 on its own, the leg that should carry the return if
expensive options are overpriced, loses money in every variant at a quarter of
the spread: between -2.1% and -9.1% a month.

## What each idea did

**Shorter HV windows** move the spread around inside noise. The 21-day window
is the best of the family at 1.7% and t = 0.6.

**Taking earnings out** makes things worse, not better. With the calendar
removed the sort is left with genuinely low-IV versus high-IV names and the
spread is -3.1%. The earnings months the baseline sort concentrated in decile 1
were not what was hiding a premium; they were the closest thing to one, since
selling pre-earnings straddles and holding through the print roughly breaks
even here while the remaining decile 1 names lose.

**IV against its own history** is the most sensible signal of the group and
gives the best raw numbers, 2.0–2.1% with t of 0.6–0.7, a monotone decile
shape (rank correlation 0.44 for the mean version), and the least damage from
costs. It is still not distinguishable from zero and still negative at a
quarter of the spread. Averaging it with HV/IV removes the small improvement.

**Hedging the net delta** at entry raises the straddle spread from -0.2% to
1.3% by removing some directional noise, and does nothing for its t-statistic.

**Early exits** are where the process mattered. The first pass showed a 4.0%
spread at ten sessions with t = 2.7 and delta-hedged t-statistics above four,
monotone across deciles. The cause was the exit mark: a leg with a zero bid on
the exit day was treated as unquoted and the row dropped. That keeps only
straddles whose both legs still had bids, which are the ones pinned near the
strike, so the long side was marked at the bottom of its distribution and the
sort into cheap and expensive did the rest. Marking a zero-bid leg at half the
ask instead leaves every horizon within 1.1% of zero and negative once the
exit crosses a spread. The lesson is general for any mid-hold mark on a
one-month straddle: a leg going to zero bid is the normal outcome for the
losing side, not missing data.

## Why the paper's rule does not transfer

The paper's sample is 1996–2005 across every optionable stock. This is
2017–2025 across the 500 largest. The same rule finds the same kind of
cross-section, high-HV low-IV names on one side and the reverse on the other,
but on large caps the reverse side is priced by the earnings calendar, and
outside earnings the level of IV against HV or its own past carries no return
information at monthly horizons that survives a fraction of the spread.
Quoted spreads of 7–12% of the straddle price against a raw edge of at most
2% a month leave no room.

## Caveats

- 98 months and a 29% monthly standard deviation on the straddle spread: the
  interval on any variant is about plus or minus 6% a month. These tests rule
  out the paper's 22.5%; they cannot rule out a small premium.
- Sixteen variants on one sample is a search, and the best t-statistic of 0.7
  is what a search over sixteen null variants produces.
- Early-exit costs use the entry day's relative spread for the exit leg, since
  the exit-day touch was not extracted; on a decayed straddle the actual
  relative spread is wider, so those rows understate the cost.
- Realized vol from the chain's stamped underlying price, S&P 500 membership
  on the formation day, and the other baseline caveats all carry over.
