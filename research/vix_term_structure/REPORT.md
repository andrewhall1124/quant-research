# Timing the index straddle with the term structure, 2017–2025

## Bottom line

The contango rule does not work on this data, and the reason is instructive.
Over 95 months the monthly XSP ATM straddle, sold and delta-hedged daily,
returned -1.8% of premium a month (t = -0.5). Selling only in contango made
it -3.1%. Selling in contango and buying in backwardation made it -4.5%.
The premium that exists on the index is earned in the months *after* the
curve inverts, not while it is in contango: the 18 backwardation months
delivered a positive variance premium of +1.4 variance points (t = 2.2) and
the 75 contango months -0.2. A rule built on "sell when the curve is deeply
inverted relative to its own year" earned +12.5% of premium a month on its
8 months (t = 3.7), too few to trade on but the right direction.

The chain-derived slope is a faithful VIX3M-minus-VIX proxy: correlation
0.93 and sign agreement 88% over the 490 sessions where both exist.

| Rule on the monthly XSP straddle, daily-hedged | Months in | Mean / month | t | Max drawdown |
| --- | --- | --- | --- | --- |
| Always short | 95 | -1.8% | -0.47 | -383% |
| Short in contango, flat in backwardation | 75 | -3.1% | -0.86 | -478% |
| Short in contango, long in backwardation | 75 + 20 | -4.5% | -1.19 | -585% |
| Short when slope above its 1-year mean | 51 | -0.6% | -0.29 | -198% |
| Short when 1-month IV above its 1-year mean | 36 | -4.9% | -1.58 | -576% |
| Short when 1-month IV below its 1-year mean | 51 | +1.9% | 0.97 | -143% |

Returns are per dollar of premium, cumulative sums of monthly returns.

## Construction

**Slope.** Every session, from the XSP chain: mean of the closest-to-ATM
call and put IV on the expiry nearest 30 days (20–45) minus the same on the
expiry nearest 90 days (60–150), for quoted contracts with a valid IV
inversion. Positive is contango. Available on 2,244 of about 2,260 sessions
after the index chain repair; the gaps are days where one maturity had no
quoted ATM pair. The trailing
252-session mean and standard deviation give a z-score.

**Position.** The Goyal-Saretto calendar: on the session after each third
Friday select the XSP straddle at the strike closest to spot expiring at the
next third Friday, enter at the next session's closing midpoint, hold to
expiry. XSP is PM-settled so the payoff is against that day's close. Both
legs must have a positive bid and an ask above it; the stock panel's volume
screen is not applied because index quotes are firm and XSP strikes often
print no volume. That gives 95 of 107 months; the rest are months with no
quoted ATM pair on a formation or entry day. XSP was the only index root
quoted through 2020–2021 before `data_pipelines.cli index-repair` filled
the others from the 15:59 bar; the repaired XSP file is what runs here.

**Carry.** Naked, and daily delta-hedged with the chain's net delta at every
close, as in the variance-premium study. Rules are set on the formation-day
slope, the session before entry, and a non-zero position pays the given
fraction of the entry half-spread. Median relative quoted spread on the XSP
straddle is 3.2% of its price, a tenth of the single-name figure.

## Results

### The unconditional index premium is not in the ATM straddle

| Year | Months | Naked short | Hedged short | Mean ATM IV | Mean realized vol |
| --- | --- | --- | --- | --- | --- |
| 2017 | 12 | -12.8% | +1.1% | 8.2% | 6.6% |
| 2018 | 11 | -6.0% | -26.7% | 12.2% | 15.5% |
| 2019 | 12 | -0.0% | +5.2% | 12.4% | 11.3% |
| 2020 | 8 | +24.6% | +5.8% | 20.9% | 18.3% |
| 2021 | 7 | +36.7% | +20.6% | 18.8% | 10.8% |
| 2022 | 10 | +11.9% | -12.5% | 25.7% | 25.0% |
| 2023 | 12 | -12.2% | +9.7% | 15.1% | 12.5% |
| 2024 | 12 | +22.2% | -3.6% | 12.7% | 12.8% |
| 2025 | 11 | -5.9% | -7.9% | 15.9% | 16.2% |

Hedged, the short earns 1% to 21% of premium a month in calm years and loses
13% to 27% a month in 2018 and 2022. The worst single months are March 2025
(-182%), January 2018 (-158%) and July 2017 (-83%): all three entered in
contango with one-month IV between 7% and 16%.

ATM IV on XSP runs about 3 points below the VIX. The VIX prices the whole
smile; an ATM straddle collects only the centre of it. Over this sample the
ATM IV and the subsequent realized vol are about a point apart on average
(15.2% against 14.1%), and the tail months take that point back, so there
is no premium to time. The
well-known positive index variance premium is a statement about VIX-squared
against realized variance, which is a premium on the put wing that an ATM
straddle does not sell.

### The slope carries the opposite information

| Slope state at formation | Months | Mean 1m IV | Hedged short mean | t | IV² − RV, var pts | t |
| --- | --- | --- | --- | --- | --- | --- |
| Backwardation (slope < 0) | 18 | 22.1% | +4.0% | 0.82 | +1.39 | 2.22 |
| Contango (slope > 0) | 75 | 13.1% | -3.9% | -0.86 | -0.19 | -0.48 |
| Slope z-score < -1 | 8 | 20.6% | +12.5% | 3.74 | +2.55 | 2.64 |
| Slope z-score > +1 | 6 | 12.5% | +4.5% | 0.64 | -0.31 | -0.58 |

Rank correlation of the formation-day slope with the subsequent variance
premium is -0.17: steeper contango, smaller premium. Rank correlation of the
IV level with it is +0.42: higher IV, larger premium. Both say the same
thing. The curve inverts when IV has just spiked, and the month after a
spike is when IV runs furthest above what is then realized. Contango is the
resting state of a low-IV market, and low IV is when a short straddle is
most exposed to the next shock.

That is a different mechanism from the VIX futures carry that the rule comes
from. A short VIX future in contango earns the roll-down of the futures
curve toward spot regardless of what realized vol does. A short straddle
has no roll-down; it earns implied minus realized, and implied minus
realized is largest after shocks.

### Costs

At half the quoted spread the always-short rule is -6.3% a month (t = -1.7)
and no rule is positive. The spread on XSP is small in relative terms; it
is the absence of an edge to pay it from that matters.

## What this says for the series

Across the five studies the picture is consistent. On S&P 500 single names
there is no unconditional variance premium and no cross-sectional sort on
realized vol, forecast vol, idiosyncratic vol, skew or term slope that finds
one; the single exception is the one-session earnings straddle, where the
market overprices the print by a factor of two. On the index the ATM
straddle premium is also zero on average, and the term structure does not
time it, because the premium is earned after inversions rather than during
contango. The only index rule with a positive sign, selling after a deep
inversion, has eight observations.

## Caveats

- 95 months. The three worst months are most of the cumulative loss.
- The slope is an ATM-IV slope, not a VIX futures slope. The two agree in
  sign 88% of the time in 2024–2025, but a VIX futures carry trade earns
  roll-down that no straddle rule can replicate.
- Rules were evaluated on one signal timing (formation-day close) and one
  holding period (monthly). A daily-rebalanced rule on a rolling 30-day
  straddle would respond faster to inversions; the eight-month result
  suggests it might do better and this study cannot say.
- The daily hedge uses the chain's delta at the close and pays no cost on
  the stock leg.
