# IV skew and term slope on S&P 500 names, 2017–2025

## Bottom line

Neither published effect is present at usable size on large caps in this
period.

- **Skew and stock returns.** The steepest-skew decile earns 0.3% a month
  against 0.6% for the flattest, a 10-1 of -0.2% (t = -0.6). Xing, Zhang and
  Zhao reported about -0.9% a month at t = -3 on all optionable stocks over
  1996–2005. Deciles 2 through 10 do fall in order, so the sign is there; the
  size is a quarter of the paper's and the bottom decile breaks the pattern.
- **Term slope and straddle returns.** The 10-1 straddle spread is 1.1% a
  month (t = 0.4), 1.4% without earnings months (t = 0.5). Vasquez reported
  monthly straddle spreads of several percent at high significance. Here the
  decile curve is a hump, highest in the middle, and the bottom decile is
  63% earnings months.
- **One thing does show up.** Sorting on skew separates the delta-hedged call
  from the delta-hedged put: the steep-skew decile's hedged calls earn 0.8% a
  month more than the flat-skew decile's (t = 3.2) while its hedged puts earn
  0.6% less (t = -2.7). That is the skew premium itself, the rich put side
  underperforming the cheap call side, not a forecast of anything.

| Sort | Stock 10-1 | t | Straddle 10-1 | t | DH call 10-1 | t | DH put 10-1 | t |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Skew | -0.22% | -0.64 | 0.3% | 0.09 | +0.79% | 3.15 | -0.59% | -2.74 |
| Skew, no earnings | -0.02% | -0.07 | -0.8% | -0.22 | +0.61% | 2.26 | -0.75% | -3.00 |
| Slope | -0.09% | -0.29 | 1.1% | 0.39 | +0.25% | 1.06 | +0.21% | 0.96 |
| Slope, no earnings | +0.46% | 1.21 | 1.4% | 0.49 | +0.23% | 0.77 | +0.32% | 1.19 |
| Slope, relative | +0.08% | 0.30 | 1.1% | 0.44 | +0.16% | 0.83 | +0.15% | 0.81 |

## Construction

**Skew.** On each formation day, on the expiry closest to 30 days within
10–60: IV of the put with moneyness closest to 0.95 within 0.80–0.95, minus
IV of the call with moneyness closest to 1.00 within 0.95–1.05. Median put
moneyness selected is 0.94 and median expiry 32 days. Median skew is 3.4 vol
points; decile 1 averages -0.9 points and decile 10 +11.0.

**Slope.** ATM IV, the mean of the closest-to-ATM call and put, on the
expiry closest to 90 days within 60–150 (median 88) minus the same on the
expiry closest to 30 days within 20–45 (median 32). Median slope is +0.4
points; decile 1 averages -6.8 points and decile 10 +5.1. A relative
version divides by the short IV.

Contracts must have a positive bid and an IV inversion error of at most one
point. Skew is available on 97% of stock-months, slope on 98%.

**Outcomes.** Stock return from entry day to expiry; the one-month ATM
straddle and static-hedged calls and puts held to expiry from the baseline;
the daily-hedged straddle from the variance-premium study. Deciles within
month, equal weights, t on monthly means over 98 months.

## Results

### Skew

The decile curve for stock returns (`figures/deciles.png`, left) declines
from 1.1% in decile 2 to 0.3% in decile 10, which is the paper's shape.
Decile 1 sits at 0.55%, which is why the 10-1 is small. Removing earnings
months changes nothing. The steep-skew decile also has the lowest IV
(28% against 39% in decile 1), so a skew sort on large caps is partly a
sort on low-vol names, whose stock returns are not distinguishable here.

The clean result is in the hedged legs. Steep skew means the put is priced
at a higher vol than the call. Buying the call and hedging it earned 0.8% a
month more in the steep decile than the flat one; buying the put and hedging
it earned 0.6% less. Both are significant at conventional levels and both
survive removing earnings. A long-hedged-call, short-hedged-put position in
the steep-skew decile is a relative-value trade on the skew premium of
roughly 1.4% a month on the hedged-return scale. On that scale a month of
hedged-call return has a standard deviation of about 3%, so this is a Sharpe
of about 0.4 monthly before the spread on two legs and stock trading. It is
a real premium; it is not a large one.

### Term slope

Straddle returns by slope decile are a hump: 1.5% in decile 1, up to 5.6% in
decile 7, back to 2.6% in decile 10. Daily-hedged the curve is flat between
0% and 2%. The bottom decile is 63% earnings months, and its mean IV is 42%
against 27–31% elsewhere: an inverted term structure on a formation day is a
report due before the one-month expiry. Removing those months leaves the
hump and a 10-1 of 1.4% at t = 0.5.

Vasquez's mechanism is that a steep slope means the short-dated option is
cheap relative to what the term structure implies its vol will become. On
large caps in 2017–2025 the short-dated IV is either pricing an event, in
which case it is not cheap, or the slope is a few points either way, in
which case it carries no return information at monthly horizons.

### Costs

Buying decile 10 and selling decile 1 at half the quoted spread costs 4–7%
a month on every sort, which is why the straddle spreads do not appear in
the summary at any cost level.

## Caveats

- Skew is measured at one fixed moneyness rather than a fixed delta, and the
  term slope at two fixed maturities. Vasquez uses a fitted slope and Xing et
  al. a delta-based put; the choices here follow the papers' stated
  moneyness and maturity bands, not their exact surface fits.
- The stock return is over the option's hold, entry to expiry, not the
  calendar month the papers use.
- 98 months of large caps. The papers' effects are strongest in small
  and illiquid names and both were published on samples ending before this
  one begins.
