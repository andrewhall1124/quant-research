# Research

## Implied financing rates from option combos vs SOFR

**Question.** What rate does the options market actually discount at, and how does it
compare to SOFR?

**Answer.** Box spreads on 2025 SPX/SPXW chains imply a median 4.218%. That is
within 3bp of overnight SOFR (4.226%), but the match is largely coincidence: the
measurable boxes are 6-12 months out, while SOFR is overnight. Compared instead to
a Treasury yield interpolated to each box's own maturity, correlation rises from
0.46 to 0.77, and the box sits a median **+16.3bp above** the curve -- the sign
theory predicts, since a box is unsecured synthetic financing while Treasuries
carry a collateral premium.

Report: `box_rates_report.html` (also published as an artifact).

### Files

| File | Purpose |
|---|---|
| `pull_index_options.py` | Pulls SPX, SPXW, XSP EOD chains for 2025 |
| `implied_rates.py` | Box-spread and put-call-parity estimators |
| `run_analysis.py` | Level, term structure, dividend check, American contrast |
| `term_matched.py` | Box rates vs a maturity-matched Treasury yield |

Run in that order; `pull_index_options.py` takes ~23 min for all three symbols.

### Why box spreads

For strikes K1 < K2 sharing an expiration:

    (C1 - P1) - (C2 - P2) = (K2 - K1) * exp(-r*T)

Spot cancels, and with it the whole dividend stream, so a box needs no dividend
data and no underlying price -- only four option quotes. That matters here because
ThetaData gives us no dividend or corporate-action data at any tier.

### Findings

- **Term structure is real.** Box rates fall monotonically with maturity (4.34% at
  3-6m, 4.22% at 6-12m, 3.98% beyond a year) against a flat spot SOFR -- the market
  pricing the easing cycle. It survives in the unfiltered data too, so it is not an
  artifact of the quality screen.
- **Short maturities are unmeasurable from EOD data.** Quote noise enters the rate
  divided by `width * T`. A one-week box carries +/-34 percentage points of rate
  uncertainty. 33,524 boxes under 90 days exist and none are usable. Reading the
  front of the curve needs intraday quotes, which are a paid tier.
- **Never run this on single-stock options.** American exercise breaks the parity
  identity: AAPL/MSFT/AMZN boxes imply ~1.6%, roughly 260bp too low. Cash-settled
  index options are the only clean instrument.
- **Put-call parity is dividend-contaminated by construction.** On the same index it
  gives 3.530% against the box's 4.218%; that 65bp gap is the dividend stream
  showing up as a rate error, and it recovers only about half the S&P's actual
  ~1.2-1.3% yield -- the rest is timing mismatch between the 16:00 index print and
  the option snapshot.

### Limitations

- The Treasury benchmark interpolates only four curve points (13w, 5y, 10y, 30y),
  and its short end sits inside the region where most boxes live.
- EOD midpoints are not executable prices.
- 49 of 250 trading days yield no measurable box, so the daily series is not a
  continuous panel. Early March to mid-April is missing entirely.
