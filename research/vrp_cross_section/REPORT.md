# Cross-section of the single-name variance premium, 2017–2025

## Bottom line

A proper volatility forecast does not rescue the cross-sectional trade.
Sorting S&P 500 names on log(forecast vol / IV), with the forecast an
out-of-sample HAR fit refit every month, gives a 10-1 straddle spread of
1.3% a month (t = 0.3) and a daily-hedged spread of 1.1% (t = 0.6). The
Cao-Han idiosyncratic vol sort is the best of the group at 2.4% a month
daily-hedged (t = 1.8), in the direction their paper reports, and still not
significant on 98 months.

The reason is visible in the forecasting table: **implied vol is a better
forecast of next month's realized variance than any of the realized-vol
measures**, including the HAR. Rank correlation with realized variance over
the hold is 0.72 for IV against 0.51 for HAR, 0.50 for 21-day RV and 0.53
for 12-month RV. The market's number already contains what the realized-vol
history contains and more. A sort on "realized versus implied" is therefore
mostly a sort on forecast error in the realized-vol measure, which is noise.

| Signal, 10-1 monthly | Straddle | t | Daily-hedged | t |
| --- | --- | --- | --- | --- |
| HAR forecast vol / IV | 1.3% | 0.30 | 1.1% | 0.64 |
| 21-day realized vol / IV | 1.7% | 0.60 | 0.8% | 0.75 |
| 12-month realized vol / IV (the paper) | -0.2% | -0.07 | 1.2% | 0.92 |
| Negative idiosyncratic vol (Cao-Han) | 2.4% | 0.66 | 2.4% | 1.77 |
| Negative HAR forecast alone (control) | -1.6% | -0.40 | 0.5% | 0.24 |

## Construction

**Panel.** The Goyal-Saretto stock-months: one ATM straddle per name per
month, selected the session after the monthly expiry and entered the next
session at the midpoint, 28,187 stock-months, 288 names a month.

**HAR forecast.** Daily realized variance components per name from
split-adjusted close-to-close log returns: the last day's squared return,
and the means over the last 5 and 22 sessions, annualized. The target is
mean squared return over the following 21 sessions. A single pooled
regression of log target on log components and a constant is fit for each
formation day on every row whose 21-session target window closed before
that day, so no forecast sees its own future. The fit uses 74,000 rows for
the first month and 1.07 million for the last; the coefficients are stable
(0.02 on the 1-day, 0.07–0.14 on the 5-day, 0.45 on the 22-day component)
and the forecast carries the usual lognormal correction.

**Idiosyncratic vol.** Standard deviation of residuals from a one-factor
regression of the name's daily return on the equal-weighted universe return
over the trailing 21 sessions, in closed form from rolling moments, as in
Cao and Han (2013). Sorted negative so that decile 10 is low idio vol,
where their delta-hedged returns were highest.

**Returns.** Hold-to-expiry straddle and static delta-hedged calls and puts
from the baseline; daily delta-hedged straddle from the variance-premium
study, as a long, scaled by premium paid. Deciles within month, equal
weights, 10-1 spread, t on monthly means.

## Results

### Forecast quality out of sample

| Predictor of realized variance over the hold | Spearman | Mean log error | RMS log error |
| --- | --- | --- | --- |
| Implied variance (ATM IV squared) | 0.72 | +0.15 | 0.68 |
| HAR forecast | 0.51 | +0.25 | 0.85 |
| 12-month realized variance | 0.53 | +0.25 | 0.89 |
| 21-day realized variance | 0.50 | +0.00 | 0.93 |

IV ranks realized variance far better than any history-based measure, and
its bias is smaller than the HAR's despite being a risk-neutral quantity.
The HAR does not even beat the crude twelve-month window on rank
correlation, which says the single-name variance process at this horizon is
close to a random walk around a slow mean: the 22-day component does the
work and the fast components add nothing.

### The sorts

The daily-hedged decile curves (`figures/deciles.png`, right panel) sit
between 0% and +2.5% a month for every signal, with decile 1 of the three
IV-relative sorts at or below zero and no monotone rise after decile 3. In
hold-to-expiry straddles the curves are noise around +3.5%. The Cao-Han
sort is the only one that rises from decile 1 to decile 10 with any
regularity, and its 10-1 is 2.4% with t = 1.8.

Each signal's rank correlation with the premium it is trying to predict
(realized minus implied variance over the hold) is 0.17 for HAR, 0.05 for
21-day RV, 0.03 for 12-month RV and 0.12 for idio vol. The HAR signal does
carry information about the premium; what it does not do is turn that into
a decile spread, because the dispersion of the premium within a month is
dominated by the realized tail, which no forecast captures.

## What this says

The single-name variance premium on S&P 500 names is not forecastable
cross-sectionally from realized-vol history, because the option market
already prices that history better than a HAR does. What remains after IV
is a residual whose sign no realized-vol statistic predicts. Cao and Han's
idiosyncratic vol effect points the right way at t = 1.8, which is
consistent with a small effect that a 98-month large-cap sample cannot
resolve.

## Caveats

- One pooled HAR across names. A per-name HAR, or one with a jump
  component or intraday variance, could forecast better; but IV would need
  to be beaten by a wide margin, not matched, for the sort to work.
- The forecast uses close-to-close returns and so does the realized
  target, so both miss intraday variance equally.
- Cao and Han hold delta-hedged positions with daily rebalancing and scale
  gains by the stock price; here gains are scaled by the premium, which
  changes the level but not the sign of decile differences.
- The five signals were chosen in advance and there is no selection across
  a wider set; the t of 1.8 is the best of five.
