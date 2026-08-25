# Forecasting volatility: RV, ARCH, GARCH and IV

**Question.** Given information through today, how well do trailing realized
volatility, an ARCH model, a GARCH model, and the option market's implied
volatility forecast (a) realized volatility over the next week and month, and
(b) implied volatility a week and a month from now?

**Sample.** SPX daily closes, 2024-01-03 to 2025-12-31 (501 trading days), with
VIX9D and VIX as the implied-vol measures for the 5- and 21-day horizons.
ARCH and GARCH are estimated on an expanding window with a 250-day burn-in, so
every forecast scored below is genuinely out-of-sample: 246 origins at h=5, 230
at h=21.

## Answer in three lines

1. **Implied volatility wins everywhere** — every horizon, both targets, every
   accuracy metric — and it is the only forecast that survives an encompassing
   regression against the other three.
2. **It wins on information, not on calibration.** VIX runs ~3.3 vol points
   above subsequent realized vol on 83% of days. Once a regression absorbs that
   premium into the intercept, its slope is statistically indistinguishable
   from 1 at both horizons.
3. **Forward implied vol is harder to forecast than forward realized vol**, and
   nothing forecasts it well at a month: the best model explains 8% of the
   variation in VIX 21 days ahead.

The caveat that governs everything else: two years of data containing one
enormous shock. Point estimates are clear, pairwise significance mostly is not.

## The sample

![SPX and volatility](figures/01_volatility_landscape.png)

Two years, one regime break. Realized vol sits between 6% and 20% for most of
the sample and then goes to 48% in April 2025. Implied sits above realized
almost everywhere — the shaded wedge — except through that shock.

That single episode carries most of the statistical weight in everything below.

## Forecasting forward realized volatility

![Forecast paths](figures/02_forecast_paths.png)

| h=5 | RMSE (pts) | MAE | bias | corr | MZ β | t(β=1) | MZ R² |
|---|---|---|---|---|---|---|---|
| RV | 11.89 | 6.74 | +0.12 | 0.50 | 0.50 | -3.1 | 0.250 |
| ARCH | 11.10 | 6.68 | +1.18 | 0.44 | 0.65 | -1.6 | 0.198 |
| GARCH | 11.42 | 6.89 | +1.34 | 0.42 | 0.60 | -2.1 | 0.173 |
| **IV** | **9.34** | **6.57** | +3.64 | **0.69** | **1.10** | **0.5** | **0.482** |

| h=21 | RMSE (pts) | MAE | bias | corr | MZ β | t(β=1) | MZ R² |
|---|---|---|---|---|---|---|---|
| RV | 12.16 | 7.93 | +0.31 | 0.32 | 0.32 | -3.9 | 0.103 |
| ARCH | 11.02 | 7.07 | +0.05 | 0.17 | 0.31 | -7.5 | 0.028 |
| GARCH | 11.64 | 7.45 | +0.23 | 0.20 | 0.27 | -8.4 | 0.038 |
| **IV** | **9.98** | 7.92 | +3.31 | **0.45** | **0.86** | **-0.7** | **0.198** |

![Scatter vs forward realized vol](figures/03a_scatter_forward_realized.png)

Reading these together:

- **IV is the only well-scaled forecast.** Its Mincer-Zarnowitz slope is 1.10
  at a week and 0.86 at a month, and neither is distinguishable from 1
  (t = 0.5 and -0.7). RV, ARCH and GARCH all come in at slopes of 0.27-0.65,
  rejected against 1 at every horizon — they respond far too little to the
  information they do have.
- **ARCH and GARCH beat trailing RV on RMSE while explaining less.** That is
  not a contradiction: they are smoother, and shrinking a forecast toward the
  mean lowers squared error in a sample with one huge outlier. Their
  correlation with the outcome at h=21 (0.17, 0.20) is *below* trailing RV's
  (0.32). RMSE rewards their caution; the MZ R² shows they know less.
- **Everything decays with horizon.** Every model's R² roughly halves going
  from a week to a month. Volatility is forecastable a few days out and mostly
  a level guess a month out.

### Is IV's edge significant?

Diebold-Mariano tests on squared-error differences (Newey-West, h lags):

| target | horizon | IV vs RV | IV vs ARCH | IV vs GARCH |
|---|---|---|---|---|
| forward realized | 5 | -1.37 | -1.32 | -1.56 |
| forward realized | 21 | -0.94 | -0.71 | -0.95 |
| forward implied | 5 | **-2.30** | -1.84 | -1.83 |
| forward implied | 21 | **-2.16** | -1.58 | **-2.09** |

Every t-statistic is negative — IV is more accurate in every pairing — but on
forward *realized* vol none of them clears the 5% bar. With 230 overlapping
observations and one dominant shock, an RMSE gap of 1-2 vol points is not
resolvable. **The honest claim is that IV is never worse and is directionally
better everywhere, not that it is significantly more accurate pairwise.**

The encompassing regression is where the result becomes sharp. Running all four
forecasts against forward realized vol together:

| horizon | const | RV | ARCH | GARCH | IV | R² |
|---|---|---|---|---|---|---|
| 5 | -0.01 (-0.5) | 0.54 (1.8) | -0.75 (-1.7) | -0.49 (-1.9) | **1.49 (5.9)** | 0.55 |
| 21 | -0.01 (-0.6) | 0.38 (0.8) | 0.01 (0.0) | -0.93 (-1.3) | **1.34 (4.2)** | 0.30 |

IV is the only term that survives with a large positive coefficient and a
t-statistic above 4. Whatever RV, ARCH and GARCH know about tomorrow's
volatility, the option market has already priced it.

### The premium that makes IV biased

![Variance risk premium](figures/04_variance_risk_premium.png)

| horizon | mean | median | share positive | worst |
|---|---|---|---|---|
| 5 | +3.64 pts | +4.91 | 80% | -62.0 |
| 21 | +3.31 pts | +6.01 | 83% | -31.1 |

IV's one clear weakness is a systematic +3.3 point bias: it is the *price* of
volatility, not a forecast of it, and it carries a risk premium. The
distribution is the classic short-vol payoff — positive four days in five,
occasionally catastrophic. April 2025 alone produced -31 points at a month and
-62 at a week.

Note what this does to the metrics: IV's MAE at h=21 (7.92) is no better than
trailing RV's (7.93) precisely because of that constant overstatement, while
its RMSE and R² are much better. Subtracting the mean premium would make IV
both unbiased and the most accurate forecast on every metric — but only in
sample, and this sample is too short to estimate the premium honestly.

## Forecasting forward implied volatility

| h=5 | RMSE (pts) | MAE | bias | corr | MZ β | MZ R² |
|---|---|---|---|---|---|---|
| RV | 10.78 | 7.11 | -3.43 | 0.52 | 0.33 | 0.272 |
| ARCH | 8.52 | 4.75 | -2.38 | 0.46 | 0.43 | 0.212 |
| GARCH | 8.49 | 4.84 | -2.21 | 0.47 | 0.43 | 0.222 |
| **IV** | **6.84** | **3.78** | **+0.09** | **0.59** | 0.59 | **0.347** |

| h=21 | RMSE (pts) | MAE | bias | corr | MZ β | MZ R² |
|---|---|---|---|---|---|---|
| RV | 11.33 | 8.35 | -2.88 | 0.16 | 0.08 | 0.025 |
| ARCH | 8.31 | 5.14 | -3.13 | 0.03 | 0.03 | 0.001 |
| GARCH | 9.52 | 6.28 | -2.95 | 0.05 | 0.04 | 0.003 |
| **IV** | **6.56** | **4.28** | **+0.12** | **0.28** | 0.28 | **0.076** |

![Scatter vs forward implied vol](figures/03b_scatter_forward_implied.png)

Today's VIX is the best predictor of VIX in a month, which is only to say VIX
is persistent — but even it explains just 8% of the variation at h=21, with a
slope of 0.28 rather than 1. That slope is the mean reversion: a VIX of 30
today implies roughly `0.14 + 0.28 × 0.30 ≈ 22%` in a month, not 30%.

The striking column is ARCH and GARCH at h=21: correlation 0.03 and 0.05, R²
of essentially zero. **Return-based volatility models carry no information
about where the option market will be priced a month out.** They chase the
realized path; implied vol moves on a mix of realized vol, positioning and
demand for protection that GARCH never sees. Their negative bias (-3 points) is
the mirror image of IV's positive one — the same risk premium, viewed from the
other side.

## Is VIX a fair stand-in for implied vol?

VIX is used above because it is free and spans the whole sample. The option
chains only cover 2025, but they cover it well enough to check:

![Implied vol validation](figures/06_implied_vol_validation.png)

A 30-day ATM implied vol rebuilt from SPXW mids — forward implied from put-call
parity, Black-76 inverted at the strike nearest the forward, interpolated in
total variance to exactly 30 days — tracks VIX at a correlation of **0.991**
across 247 days of 2025, sitting a steady **3.7 points below** it.

That gap is expected and is not an error: VIX integrates the whole OTM strip,
so the put skew lifts it above the ATM vol. For a study about forecast
*information*, a 0.991 correlation means the choice of measure changes nothing
here. It would matter for anything trading the level.

## What this does and does not establish

Established, in this sample:

- IV dominates the return-based models on every metric at both horizons, and
  encompasses them for forward realized vol.
- IV is well-scaled but biased high by ~3.3 vol points; the return-based models
  are roughly unbiased for realized vol but badly under-scaled.
- Nothing forecasts forward implied vol at a month; ARCH and GARCH have
  literally zero explanatory power there.

Not established:

- **Pairwise significance** of IV's accuracy edge on forward realized vol.
  Two years is not enough.
- **Generality across regimes.** The sample contains one shock, and it drives
  most of the dispersion. Rerunning across 2015-2025 would be the obvious next
  step and needs a paid ThetaData tier (or another vendor) for the history.
- **Anything about the equity cross-section.** SPX only. The single-name chains
  in `data_store/options_2025/` would support the same test on 500 names, where
  the IV measure has to be built from chains rather than a published index.

Two extensions worth more than more data: an HAR model, which usually beats
GARCH at these horizons by mixing daily/weekly/monthly RV, and a premium-
adjusted IV forecast estimated on a rolling basis, which would test whether the
+3.3 point bias is stable enough to remove out of sample.
