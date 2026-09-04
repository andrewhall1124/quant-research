# Cheap against expensive implied vol, in the cross-section of single names

Buy the S&P 500 names whose implied volatility is low relative to their own
recent history, sell the ones where it is high, vega-weight both sides,
delta-hedge daily, hold to expiry.

**On five years the strategy does not work.** It has a weak gross signal that
cannot pay its own bid-ask spread, and every specific parameter chosen on 2025
fails to survive the other four years.

| | 2025 only | + 2024 | 2021-2025 |
|---|---|---|---|
| formation dates | 66 | 152 | **380** |
| gross Sharpe | 3.15 | 1.55 | **0.74** (t=1.79) |
| net of half the quoted spread | 1.35 | 0.10 | **−0.68** (t=−1.65) |
| break-even spread | 0.886 | 0.535 | **0.260** |
| net P&L | +$100k | +$19k | **−$318k** |

Break-even decays monotonically as out-of-sample years are added — 0.886, then
0.535, then 0.260 against the 0.5 needed. That shape is what a result fitted to
its own sample looks like, and this study was specified entirely on 2025.

Sections 1-2 are the idea and the method, which are unchanged and worth
keeping. Sections 3-6 are what the five-year data does to each design choice.
§7 is the verdict.

---

## 1. The idea

Implied volatility moves around a lot, and much of that movement reverses. A
name whose options suddenly price 45% vol when they normally price 30% has
usually not permanently become a riskier company; more often the market has
repriced its options and will reprice them back.

That is the bet: **implied volatility mean-reverts**, so buy it where it is low
relative to the name's own history and sell it where it is high.

**The signal is a 60-session z-score of each name's ATM implied vol against
itself.** Measuring against its own history rather than against other names is
the point — a utility always has lower implied vol than a biotech, so sorting
on the raw level buys utilities and sells biotech. The position is
**vega-neutral**, equal volatility exposure long and short, so it profits when
the two sides converge rather than when volatility falls overall.

It is **not** a variance risk premium strategy, despite using the instrument
that would harvest one. The book is vega-neutral, which nets the premium's
level out by construction, and a greek attribution puts the money in vega —
the channel that pays when implied vol is re-priced — rather than in
gamma-and-theta, the channel that pays when realized volatility undershoots
implied. `IV − E[RV]`, the premium measured directly, is among the worst sorts
tried (§3).

## 2. How the backtest works

**The instrument is a delta-hedged ATM straddle** — one call and one put at the
strike nearest the money, the cleanest available exposure to volatility, with
the direction hedged out daily at the close. Without the hedge the P&L would be
dominated by whether the stock happened to rise or fall.

**Contract selection is point-in-time.** Each day the engine sees only that
day's chain: the listed expiration nearest the target tenor, then the strike
nearest the money. Where the chain cannot serve that, the name drops out of the
universe for the day.

**Sizing is a dollar vega budget** — $10,000 of vega per side, split equally
across the names in each decile. Equal *dollar* weighting would put wildly
different volatility exposure on a $15 stock and a $600 one.

**Positions are formed daily and held to expiry**, so ~14 cohorts are live at
once; the reported series divides by that count, representing one book rather
than fourteen stacked.

**Costs are charged against the quoted spread on the actual day**, entry only —
a position held to expiry settles at intrinsic and is never sold.

**Inference is Newey-West at the realized hold** (14 trading days). Overlapping
positions make today's P&L share most of its holdings with yesterday's.

### Two data problems that had to be solved first

**The hedge cannot use the stock feed.** This account holds Options STANDARD
but Stocks FREE, and the stock endpoint refuses anything before 2023-06-01. A
hedge built on it silently truncates a five-year backfill to the years the
*stock* tier allows. It is not needed: every greeks row carries
`underlying_price`, struck at the same instant as the option quote, for every
year the option store covers. Splits come from the corporate-actions table,
which runs 2016 to the present.

**Symbol trust has to be per-year.** `trusted_symbols()` reads a check built
against the 2025 universe, so applying it to a backfill drops the names that
did not survive — 84 of 511 symbols in 2021. `dal.usable_symbol_years()` uses
the per-year symbology check instead and excludes only `wrong_instrument`
(6 symbol-years, including `META` in 2021, when Facebook was still FB).
`thin_overlap` is kept: it means the check could not run, not that it failed,
and it falls disproportionately on delisted names, so excluding it would be a
survivorship filter dressed as a quality one.

## 3. The signal, over five years

Gross, identical machinery, at the strategy's own specification:

| signal | Sharpe | formation dates |
|---|---|---|
| **IV z-score, 60 sessions** | **0.74** | 380 |
| VRP, IV − trailing RV | 0.26 | 204 |
| VRP, IV − GARCH forecast | −0.03 | 173 |
| IV level (control) | **+0.17** | 390 |

The z-score still ranks first, and the premium-based sorts still rank below it.
But **the control no longer loses.** On 2025 the IV-level sort returned −1.35
and on two years −1.36, which was the evidence that the strategy was not a
disguised volatility-level tilt. Over five years it is mildly positive. That
argument is weaker than it looked.

The two VRP rows cover only 173-204 formation dates, because they need return
history from the stock feed and that feed starts 2023-06-01. They are not
comparable to the other two over the full window.

### The decile gradient is gone

Every decile held long, gross, 2021-2025:

| decile | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |
|---|---|---|---|---|---|---|---|---|---|---|
| mean daily P&L | −408 | −92 | −92 | −116 | +277 | +60 | +91 | −125 | −526 | −1218 |

On two years this graded cleanly — deciles 0-6 positive, 7-9 negative. On five
it does not. **The cheapest decile, which is the long side of the trade, is now
the second-worst bucket in the sort.** The long-short spread is still positive
only because decile 9 is very negative; the strategy has become a short of
expensive volatility with a long leg that contributes nothing.

That is a materially different claim from "cheapness predicts returns across
the cross-section", which is what the two-year gradient supported.

## 4. Tenor: 60 days was fitted

Gross Sharpe by tenor and liquidity floor, 2021-2025:

| tenor | oi≥0 | oi≥25 | oi≥100 | oi≥250 | oi≥1000 |
|---|---|---|---|---|---|
| **30d** | **2.19** | **1.02** | **0.90** | **0.87** | 0.93 |
| 60d | 1.28 | 0.66 | 0.70 | 0.74 | **1.02** |
| 90d | 0.31 | 0.14 | −0.09 | −0.47 | −0.52 |

**30 days now beats 60 at every liquidity floor but the highest.** On 2025
alone the ordering was the reverse and emphatic — 60 days returned 3.15 at
oi≥250 against 30 days' 1.02 — and that comparison is what selected the tenor
the whole strategy is built on.

The mechanical argument for longer tenors survives: ATM vega grows as √T while
quoted spreads stay closer to tick-driven, so a longer contract does buy the
same exposure for less spread (`cost_efficiency.py`). What does not survive is
the claim that the *signal* is stronger there.

## 5. Earnings, and what still holds

The earnings mechanism is the part of this study that has replicated in every
window tested, because it is measured directly rather than through P&L. The
fraction of selected contracts whose life contains an announcement runs from
about 0.07 in the cheapest decile to 0.75 in the richest at a 30-day tenor, and
is flat near 0.99 at 120 days.

![earnings](figures/04_earnings.png)

Implied vol prices the variance a contract expects to cover, and an
announcement is a scheduled jump, so a contract spanning one is genuinely worth
more — not mispriced, just covering more risk. A short-dated sort therefore
ranks partly on a calendar fact. That is true regardless of whether the
strategy makes money, and it is the most durable finding here.

## 6. Execution: holding to expiry still helps, and still is not enough

Break-even spread, 2021-2025:

| | sold after 21 days | held to expiry |
|---|---|---|
| 30-day contract | 0.052 | 0.187 |
| **60-day contract** | 0.101 | **0.260** |

Holding to expiry roughly doubles what the strategy can afford, in every window
it has been measured in. That is arithmetic — a settled position never crosses
the spread a second time — and it will hold in any sample.

It is not enough. The full cost curve, 2021-2025:

| spread paid per crossing | 0 | 0.25 | 0.5 | 0.75 | 1.0 |
|---|---|---|---|---|---|
| net Sharpe | 0.74 | 0.03 | **−0.68** | −1.37 | −2.04 |

The strategy is flat paying a quarter of the quoted spread and loses from there.
At the ordinary half-spread assumption it loses $318k across five years.

## 7. Verdict

**Year by year, gross and net of half the quoted spread, no liquidity screen so
the years share a universe:**

| year | gross Sharpe | net Sharpe |
|---|---|---|
| 2021 | 3.24 | −3.45 |
| 2022 | 1.12 | −4.24 |
| 2023 | −0.01 | −2.34 |
| 2024 | 1.43 | −0.92 |
| 2025 | 2.14 | −3.15 |
| **pooled** | **1.28** (t=3.20) | **−2.45** (t=−6.55) |

**There is a real but weak gross signal.** Positive in four of five years,
pooled t = 3.20 over 1,185 days. Implied vol does mean-revert in the
cross-section, and sorting on it does carry information.

**It cannot pay to trade.** Net of an ordinary execution assumption the
strategy loses money in **all five years**, pooled t = −6.55. The gross edge is
roughly a quarter of the spread it must cross to capture it.

**And the specification was fitted.** The tenor, the decile gradient, and the
IV-level control all pointed one way on 2025 and a different way on five years.
The break-even decayed monotonically as each out-of-sample year was added.
Roughly 145 configurations were searched to arrive at the 2025 version, on a
sample of about one independent observation; this is what that produces.

The honest summary: **a genuine but small cross-sectional effect, too small to
survive single-name option spreads, discovered inside a search wide enough to
have manufactured a much larger one.**

## 8. What would be worth doing instead

1. **Trade this where spreads are 2%, not 14%.** The gross signal is real and
   the obstacle is entirely execution. `cost_efficiency.py` measures index
   options at 0.242 in quoted spread per dollar of vega against 4.69 for a
   30-day single-name straddle — roughly **19× cheaper**. A dispersion
   structure (short index vol against long single-name vol) puts the expensive
   leg where the spread is thin, and `research/single_name_vol/` already
   measured the ingredient: the single-name premium is proportionally smaller
   than the index's at a month, 1.14× against 1.21×.
2. **Fills rather than quotes.** Everything here charges the quoted spread. A
   break-even of 0.260 needs execution at a quarter of quoted, which is a
   strong claim, but this data cannot say whether it is achievable.
3. **A cheaper hedge.** The delta hedge is a large drag, and it hedges each
   name's gross delta when the long and short deciles partly offset. Hedging
   net portfolio delta should cost a fraction of it.
4. **Do not extend this specification further.** Adding years has monotonically
   reduced it. The next useful experiment is a different structure, not another
   parameter.

### A note on scope

The five-year panel is built on a slightly tighter band than the earlier runs —
dte 18-115 and 7% moneyness, against 18-140 and 8% — to keep 35.6M rows in
memory. That drops the 120-day cell from the tenor grid. It does not affect the
headline strategy, whose contracts sit at 48-72 dte and 5% moneyness, but the
2025 figures quoted in the comparison table at the top were computed on the
wider band and are not exactly reproducible from this panel.
