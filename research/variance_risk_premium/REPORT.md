# Variance risk premium on S&P 500 single names, 2017–2025

## Bottom line

There is no variance risk premium to harvest on S&P 500 single names in this
sample. Selling one ATM straddle per name per month, hedged daily to its net
delta, returned -1.1% of premium a month on average (t = -0.6) across 98
months and 285 names a month. Implied variance at entry exceeded realized
variance over the hold in 63% of stock-months, which is the textbook picture,
but the 37% that went the other way were large enough that the mean premium
in variance points is slightly negative. Every carry loses more once a
fraction of the spread is paid.

This matches Driessen, Maenhout and Vilkov (2009): the well-known index
premium is compensation for correlation risk, and individual-stock variance
carries little or no premium of its own. The SPX comparison, on 102 of 107
months after the index chain was repaired, is also flat for an ATM
straddle: the index premium the literature measures against VIX-squared
lives in the put wing, not at the money.

| Short straddle, monthly, % of premium | Naked | Static hedge | Daily hedge |
| --- | --- | --- | --- |
| S&P 500 names, mean | -3.5% | -1.9% | -1.1% |
| t-statistic | -0.81 | -0.53 | -0.57 |
| Standard deviation | 43% | 34% | 20% |
| Worst month (Feb 2020) | -369% | -263% | -136% |
| SPX, mean (102 months) | +2.6% | +3.7% | -1.2% |

## Construction

The contract is the Goyal-Saretto baseline's: the strike closest to spot
within 2.5%, expiring at the next monthly expiry, selected on the session
after this month's expiry and sold at the next session's closing midpoint.
Both legs need a positive bid, ask above bid, volume and a moving quote on
both days. 27,931 stock-months survive after dropping the 60 symbol-years the
symbology check flags for an unadjusted spinoff.

Three ways of carrying the short position, each scaled by the premium
received:

- **Naked.** Sell and settle at intrinsic value at expiry.
- **Static hedge.** Buy the straddle's net delta in stock at entry, hold to
  expiry. This is the paper's delta-hedged construction.
- **Daily hedge.** Rebalance the stock position to the straddle's net delta
  at every close, using the chain's delta and midpoint. The expiry-day quote
  is replaced by intrinsic value, since closing quotes on an expiring
  contract are unreliable and the position settles at the payoff regardless.
  Sessions where a leg has no ask are skipped with the hedge carried through.

And the premium measured directly: implied variance at entry (the square of
the mean ATM IV) minus realized variance over the hold (annualized sum of
squared split-adjusted daily log returns from entry to expiry).

## Results

### The premium is not there

| Series, S&P 500 names, monthly | Mean | Std | t | Min | Max |
| --- | --- | --- | --- | --- | --- |
| IV² − RV, variance points | -0.012 | 0.143 | -0.82 | -1.32 | 0.15 |
| IV − RV, vol points | +0.007 | 0.102 | 0.71 | -0.84 | 0.14 |
| Short straddle, daily hedge, % of premium | -1.1% | 19.7% | -0.57 | -136% | +25% |

Implied vol runs about 0.7 points above realized vol on average, but that is
the Jensen gap between vol and variance: in variance terms, which is what a
short straddle actually pays out on, the mean is negative. The scatter in
`figures/implied_vs_realized.png` shows the shape: most points sit just
below the 45-degree line, and a thick tail sits far above it.

### The tail is the whole story

Daily-hedged, the seven worst months cost between 18% and 136% of premium
each. Feb 2020 alone (formation on Feb 24, held into the March crash,
realized vol of 114% against 31% implied) is -136%. March 2025 is -71%.
Excluding Feb 2020 the daily-hedged mean is +0.3% a month, still zero. The
five best months (April and July 2020, March 2023, November 2020, June 2022)
are each +15% to +25%: the premium is real in the months after a shock, when
IV is high and realized vol is falling, and absent otherwise.

By calendar year (daily hedge, % of premium): 2019 +3.4%, 2021 +6.1%, 2023
+1.9%; 2018 -5.6%, 2020 -4.7%, 2022 -2.8%, 2024 -3.5%, 2025 -2.9%. Three
winning years out of eight, and the winning years are the ones after a
volatility spike.

### Nothing observable at entry sorts it

Conditional on what was known at entry (`results/conditional.csv`,
daily-hedged mean per month): earnings in the hold -0.9%, no earnings -1.2%;
IV terciles -1.8%, -0.4%, -1.2%; HV/IV terciles -0.6%, -1.9%, -0.9%. No
group is positive and no ordering is monotone.

### Costs

| Effective spread | Naked | Static hedge | Daily hedge |
| --- | --- | --- | --- |
| 0 | -3.5% | -1.9% | -1.1% |
| 25% of quoted | -5.0% | -3.2% | -2.5% |
| 50% | -6.5% | -4.7% | -4.0% |
| 100% | -9.9% | -8.1% | -7.3% |

The daily hedge also trades stock every session, which is not charged here.

### The SPX comparison

SPX monthlies through the same code give a daily-hedged mean of -1.2% of
premium (t = -0.4) over 102 months, naked +2.6% and static-hedged +3.7%,
none distinguishable from zero. By year the daily-hedged short earns
1% to 14% of premium a month in 2017, 2019, 2020, 2021 and 2023 and loses
3% to 23% a month in 2018, 2022, 2024 and 2025. The worst months are
January 2018 (-134%), March 2025 (-105%) and July 2024 (-82%), each entered
with one-month IV under 16% and realizing 23% to 51%.

Two things about this measurement. SPX monthlies settle on the third
Friday's opening prints; the payoff here uses that day's close. And ATM IV
on SPX runs about 3 points below the VIX, because the VIX prices the skew
and an ATM straddle does not. Over the sample the SPX ATM IV averages 15.2%
against 14.1% realized: a one-point vol premium that a straddle's variance
exposure and tail months turn into nothing. The index premium in the
literature is measured against VIX-squared, which is a larger number than
what an ATM straddle collects; that premium sits in the put wing.

The index chain in the store had no quotes for most of 2020-2021 when this
study was first run; `data_pipelines.cli index-repair` filled them from the
15:59 intraday bar and the numbers above are on the repaired data.

## What this means for the other studies

Any single-name option-selling strategy on this universe starts from a zero
unconditional premium, not a positive one. A signal has to find a
cross-section of the premium that the unconditional average hides, and the
Goyal-Saretto sort, its variants, and the entry-time conditioning above did
not. The premium that exists is a post-shock, time-series phenomenon: it is
earned in the months after IV spikes. That points at index-level timing
(study 5) rather than single-name selection.

## Caveats

- 98 months, dominated by one observation. The t-statistics are honest about
  that; the point estimates are not stable to including or excluding
  Feb 2020.
- The daily hedge uses the vendor's Black-Scholes delta on the closing
  midpoint. Real hedging would use a model delta, trade at the touch and pay
  stock commissions. None of that would raise the return.
- Realized variance uses close-to-close returns from the chain's stamped
  underlying price. Intraday variance is higher than close-to-close variance,
  so if anything this understates what the straddle seller was exposed to.
- The 2.5% moneyness band, the quote screens and the S&P 500 membership rule
  are inherited from the baseline and select toward liquid, quoted months.
