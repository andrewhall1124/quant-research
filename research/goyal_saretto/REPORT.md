# Goyal and Saretto (2008) on S&P 500 options, 2017–2025

## Bottom line

The paper's sort works mechanically on this data but does not pay. Sorting
S&P 500 names each month on log(HV/IV) produces deciles that look like the
paper's Table 2: historical volatility rises from 29% in decile 1 to 42% in
decile 10 while implied volatility falls from 38% to 28%, deltas sit near 0.54
and -0.46, and options in decile 1 cost more relative to spot than those in
decile 10. But the post-formation return spread the paper reports, 22.5% a month
on straddles and about 2.7% on delta-hedged options, is absent.

| 10-1 portfolio | Paper, 1996–2005 | Here, 2017–2025 |
| --- | --- | --- |
| Straddle, mean monthly | 22.5% (SR 0.72) | -0.2% (t = -0.07, SR -0.01) |
| Delta-hedged call | 2.7% (SR 0.61) | 0.33% (t = 1.30, SR 0.13) |
| Delta-hedged put | 2.6% (SR 0.68) | 0.10% (t = 0.42, SR 0.04) |

Every robustness cut the paper reports gives the same answer, and paying any
part of the quoted spread makes the straddle spread significantly negative.

## What was replicated

Sample: 98 formation months, October 2017 to November 2025, 620 symbols, an
average of 288 stock-months per formation date, about 29 per decile. The first
formation month is the first with 200 sessions of return history.

Rules, following Section 2 and 3.2 of the paper:

1. **Calendar.** Monthly options expire on the third Friday, or the Thursday
   before when the Friday is a holiday. Formation is the first session after
   that expiry; the signal uses that day's close. The position is entered at
   the next session's closing bid/ask midpoint and held to the next monthly
   expiry, where each leg settles at intrinsic value against the stock price on
   the chain rows that day.
2. **Contract.** The strike closest to spot on the formation day, within
   0.975–1.025 of spot, expiring at the next monthly expiry, with both a call
   and a put that pass the quote screens.
3. **Quote screens**, applied on the formation and entry days to both legs:
   bid above zero, ask above bid, positive volume, and bid and ask not both
   unchanged from the prior session. IV must have inverted with an error of at
   most 1.0 vol point.
4. **Signal.** HV is the annualized standard deviation of split-adjusted daily
   log returns over the 252 sessions ending on the formation day. IV is the
   mean of the selected call's and put's implied vol on the formation day. The
   sort variable is log(HV) - log(IV); decile 10 is the largest.
5. **Returns.** Straddle: payoff of call plus put over the sum of their entry
   midpoints, minus one. Delta-hedged call: long the call, short delta shares
   at the entry-day chain delta, gain scaled by |delta x S - C|. Delta-hedged
   put: long the put and |delta| shares, gain scaled by P + |delta| x S. No
   rebalancing, as in the paper.
6. **Portfolios.** Equal-weighted within decile each month; the 10-1 spread is
   the decile 10 mean minus the decile 1 mean. Sharpe is mean over standard
   deviation of monthly returns, as the paper's numbers imply. Certainty
   equivalents use power utility with gamma of 3 and 7.
7. **Costs.** Entry at 50%, 75% and 100% of the half-spread inside the touch:
   decile 10 is bought above mid, decile 1 sold below. Expiry is settlement, so
   there is no exit spread.

## Deviations from the paper

- **Universe.** Point-in-time S&P 500 members rather than every listed name.
  The paper's average stock was already large ($6–7 billion), but its sample
  included the small and mid caps where option mispricing is most plausible.
  Symbol-years the symbology check flags as the wrong company are excluded.
- **Period.** 2017–2025 versus 1996–2005. The paper's effect has been studied
  by others and reported to weaken after publication; this sample is entirely
  post-publication.
- **Minimum tick.** The paper drops quotes with a spread below the $0.05 or
  $0.10 minimum tick of its era. Penny quoting makes that screen meaningless
  now; the requirement is ask above bid.
- **HV source.** Daily closes are read from the `underlying_price` stamped on
  every chain row, since the stock loader is floored at 2023-06. Splits are
  applied from the corporate actions table.
- **Stock trading costs at expiry** are not modelled. The paper adds them from
  TAQ effective spreads; they would only lower the numbers below.
- **Greeks.** The chain's delta is a Black-Scholes delta, not the binomial
  American delta of OptionMetrics. Early exercise is ignored in both.
- **No factor regressions.** The risk-adjustment sections (Tables 4–7) need
  Fama-French factors and a Coval-Shumway zero-beta index straddle series.
  With a raw spread this close to zero there is nothing for them to explain.

## Results

### Table 3: post-formation returns by decile

Mean monthly returns, standard deviation and t-statistic across 98 months.
Full table with min, max, Sharpe and certainty equivalents in
`results/table3_returns.csv`.

| Decile | Straddle mean | std | t | DH call mean | t | DH put mean | t |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 (IV >> HV) | 3.9% | 35.3% | 1.09 | -0.07% | -0.20 | 0.15% | 0.58 |
| 2 | 5.5% | 47.3% | 1.14 | 0.13% | 0.39 | 0.18% | 0.70 |
| 3 | 1.5% | 46.2% | 0.31 | -0.19% | -0.57 | -0.12% | -0.51 |
| 4 | 3.9% | 48.1% | 0.81 | 0.08% | 0.24 | 0.16% | 0.67 |
| 5 | 0.9% | 46.8% | 0.19 | -0.07% | -0.21 | 0.02% | 0.07 |
| 6 | 3.4% | 46.8% | 0.72 | -0.01% | -0.04 | 0.10% | 0.44 |
| 7 | 5.9% | 54.0% | 1.08 | 0.12% | 0.34 | 0.17% | 0.70 |
| 8 | 5.0% | 49.8% | 1.00 | 0.19% | 0.53 | 0.19% | 0.77 |
| 9 | 3.3% | 45.5% | 0.71 | 0.11% | 0.35 | 0.13% | 0.54 |
| 10 (HV >> IV) | 3.7% | 41.3% | 0.89 | 0.26% | 0.83 | 0.25% | 1.05 |
| 10-1 | -0.2% | 28.6% | -0.07 | 0.33% | 1.30 | 0.10% | 0.42 |

There is no monotone pattern in the straddle deciles. The delta-hedged deciles
tilt faintly the paper's way, a spread of a third of a percent a month for
calls, but at t = 1.3 it is not distinguishable from zero, and it vanishes in
2022–2025. Positive mean straddle returns in every decile are the March 2020
formation and a handful of similar months; the median straddle return is
negative in every decile.

Stocks sorted the same way show no return pattern (10-1 of 0.1% a month,
t = 0.30), as in the paper.

### Table 2: what the sort picks up

| Decile | HV | IV | HV - IV | call delta | put delta | C/S | P/S | earnings in hold |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 29.0% | 37.6% | -8.6% | 0.539 | -0.462 | 4.2% | 4.0% | 63% |
| 5 | 30.9% | 30.3% | 0.6% | 0.537 | -0.464 | 3.4% | 3.2% | 31% |
| 10 | 41.7% | 28.4% | 13.3% | 0.526 | -0.475 | 3.1% | 3.1% | 6% |

The levels are the shape the paper describes, at lower volatility because these
are large caps. The last column is the difference that matters. In the paper,
earnings announcements were evenly spread across deciles. Here, 63% of decile 1
stock-months have an earnings release inside the holding period against 6% in
decile 10. For an S&P 500 name, one-month ATM IV is dominated by whether the
next report falls before expiry, so a low-HV-minus-IV sort is largely a sort on
the earnings calendar. The high IV in decile 1 is therefore not an overreaction
to be faded; it is the priced earnings jump, and those straddles earn a mean
of 3.9% a month, no worse than the rest.

Removing every stock-month with an earnings release in the hold leaves the
straddle spread at -2.2% (t = -0.65), so the null result is not a by-product
of earnings either.

### Table 8: transaction costs and liquidity

10-1 straddle, buying decile 10 and selling decile 1 inside the quoted spread:

| Effective spread | mean | t |
| --- | --- | --- |
| 0 (midpoint) | -0.2% | -0.07 |
| 50% of quoted | -6.0% | -2.11 |
| 75% of quoted | -8.9% | -3.17 |
| 100% of quoted | -12.0% | -4.26 |

Relative quoted spreads on the traded straddles average about 10.5% of the
straddle price, so crossing them removes 6 to 12 points a month. The paper's
22.5% survived that at 7.5%; a spread near zero does not. Splitting each month
at the median relative spread, the more liquid half shows a 0.1% midpoint
spread and the less liquid half -1.2%; the paper found the opposite ordering,
with its profits concentrated in illiquid options.

### Robustness

10-1 straddle and delta-hedged call means, t-statistics in parentheses; the
full set for all three strategies is in `results/robustness.csv`.

| Variant | Straddle | DH call |
| --- | --- | --- |
| Baseline | -0.2% (-0.07) | 0.33% (1.30) |
| 2017–2021 | 0.2% (0.04) | 0.63% (1.67) |
| 2022–2025 | -0.6% (-0.14) | 0.00% (0.01) |
| Moneyness 0.95–1.05 | -0.9% (-0.31) | 0.32% (1.19) |
| No earnings in hold | -2.2% (-0.65) | 0.13% (0.40) |
| IV from call only | -1.6% (-0.52) | 0.60% (2.36) |
| IV from put only | 1.5% (0.54) | 0.16% (0.62) |
| Sort on HV level | -0.8% (-0.17) | -0.21% (-0.56) |
| Sort on -IV level | -0.2% (-0.04) | 0.23% (0.62) |
| Quintiles | -1.2% (-0.55) | 0.16% (0.88) |

The one t-statistic above two, delta-hedged calls when the signal uses the
call's own IV, is what a call-only signal selecting for cheap calls should
produce mechanically, and the put-only mirror gives the put-hedged spread a
t of 1.95 with the call spread at 0.62. Neither carries over to straddles.

By calendar year the 10-1 straddle swings from +9.1% a month in 2020 to
-12.8% in 2024 (`results/annual.csv`); the cumulative line in
`figures/long_short.png` is a random walk that ends near where it began.

## Caveats

- 98 months is a short sample for a strategy with a 29% monthly standard
  deviation. The 95% interval on the straddle spread is roughly plus or minus
  6% a month, so a modest positive premium cannot be ruled out. A 22.5%
  premium can.
- Deciles hold 29 names, matching the paper's 30, but the universe behind them
  is 500 large caps rather than the several thousand names the paper drew on.
  This is a test of the paper's rule on the S&P 500, not a test of the
  paper's own sample.
- Only names in the S&P 500 on the formation day are traded, and the
  symbology check excludes 18 symbol-years. Names that left the index later
  in the month are still held to expiry.
- Payoffs use the `underlying_price` stamped on the expiry-day chain rather
  than an official settlement close. The two differ by the last print's
  timing; on a one-month hold this is noise.
