# Experiments

What has been run, in order, and what each showed. Logs are in `demos/`,
figures in `demos/figures/`. Every run pays the full half-spread on option
fills and $0.005/share on the hedge, rebalances weekly, and uses the
constraints and penalties at the top of `demos/level_book.py`.

## Six months, 2025-01-02 to 2025-06-30, 533 names

### The book is cost-dominated (first run, 10% re-strike, in-process model)

Gross +$141k, costs $613k, net −$472k on ~$11k of gross vega; turnover 33×
gross vega a year. Rolls were 70% of cost. The gross decile spread had the
right sign (5.1 vs 3.3) but a single half-year of formation dates is not
enough to read it.

### Inception-vega normalisation and the re-strike rule

The first reference paths divided daily P&L by the previous close's dollar
vega. A straddle drifting out of the money before its 20-DTE roll has a
tiny current vega, which produced per-vega values in the hundreds and roll
costs of 400 per dollar of vega — artefacts of the denominator. Two changes:
per-vega P&L now divides by the unit's vega at inception, and a unit
re-strikes when its strike drifts more than 10% (later 15%) from spot. The
per-vega daily std fell from ~18 to ~4 and roll costs to a median of ~6.

### 15% re-strike (chosen by the author over 10%)

Gross +$74k, costs $538k, net −$464k; roll costs down ~12%, turnover
slightly up. Decile 1 vs 10: 4.2 vs 3.7.

### Stored risk model (pipeline) instead of in-process

Same inputs, same picture: gross +$54k, net −$480k, market loading +0.02.
The model is now a store table and the six-month book runs in 12 min
instead of 16.

### Determinism

Two-month full-universe runs differed by thousands between repeats. Cause:
`DeltaHedgedStraddle.select` broke a tie between two monthlies equidistant
from the 60-day target (46 vs 74 days) by moneyness and strike only, so
when both shared the ATM strike the winner depended on parquet row order
across threads; the pillar selection had the same latent tie. Both now sort
on explicit keys. Two repeats match to the cent (net −$125,915.46). The
stored model was rebuilt under the fix.

### Six-month books on the deterministic model

| | VRP | IV z-score |
| --- | --- | --- |
| positions / gross vega | 36 / $10.4k | 51 / $9.8k |
| gross / costs / net | +$92k / $530k / −$438k | +$38k / $388k / −$349k |
| turnover / gross vega, annual | 36× | 30× |
| decile 1 vs 10, gross fwd P&L per $ vega | 4.4 vs 4.0 | 4.9 vs 4.5 |
| market vol loading (t) | +0.01 (0.1) | −0.13 (−1.5) |

Long vol paid in every decile over the first half of 2025 (the April vol
spike sits inside every 60-session forward window), and the spreads are
small. The time-series signal spreads over more names and churns less, so
its costs are a third lower.

### Research configuration: MVO against the rank book

The demos' defaults changed on 2026-09-07 to the research configuration:
no costs, no liquidity screens, fractional contracts, no no-trade band, no
jump penalty, and only `NetVegaNeutral($500)` and `GrossVegaCap($20k)` on
the optimizer. `--strategy rank` replaces the optimizer with
`RankWeightedStrategy` (dollar vega proportional to centred rank, no risk
model). All four books, same 122 sessions, stored risk model:

| | VRP, MVO | VRP, rank | IV z, MVO | IV z, rank |
| --- | --- | --- | --- | --- |
| mean positions / gross vega | 493 / $19.6k | 493 / $19.8k | 497 / $19.7k | 495 / $19.8k |
| gross P&L | +$140k | +$80k | +$324k | +$171k |
| annualised gross P&L per $ gross vega | 14.8 | 8.4 | 33.9 | 17.9 |
| Sharpe (gross) | 1.02 | 2.35 | 2.29 | 3.74 |
| max drawdown | -$98k | -$28k | -$106k | -$30k |
| annual turnover / gross vega | 45x | 52x | 46x | 59x |
| P&L loading on market vol factor (t) | +0.15 (2.1) | +0.05 (2.8) | -0.07 (-0.8) | -0.06 (-2.7) |
| intercept, P&L per $ gross vega per day (t) | 0.07 (0.8) | 0.04 (1.8) | 0.07 (0.8) | 0.06 (2.2) |

**What it says.** With every name tradeable and the gross cap the only
sizing constraint, the book holds the whole universe and sits at the cap.
Both signals are positive gross over the window; the IV z-score earns
about twice the VRP under either sizer, consistent with the six-month
decile tables (the seven-year tables below reverse that ordering). The
MVO books earn roughly twice the dollars of the rank books with three
times the drawdown: without the per-name cap, the optimizer concentrates
where alpha is large and idio vol is small, and that concentration is what
the drawdown measures. The rank books' Sharpe is higher in both cases, so
on this window the risk model is buying return, not risk-adjusted return.
The MVO VRP book also carries a significant long market-vol loading
(+0.15, t 2.1) that the dropped `FactorNeutral` constraint used to remove.

**What it does not say.** Six months and 122 sessions is too short for
the intercepts to be significant (t 0.8–2.2), and the MVO-versus-rank gap
is one sample. Turnover is 45–60x gross vega a year in every book, higher
than under the screened configuration (30–38x) because fractional
contracts and no band mean every rebalance trades every name. Any cost
model will dominate at that cadence; the comparison to make next is the
same four books over seven years, then the same four with `--costs`.

## Seven years, 2018-07-02 to 2025-06-30, 659 names, close-to-close realized vol

The stock file starts mid-2023, so realized vol, the HAR and the stock
features here come from the chains' underlying close (`--rv-source close`),
without the size control. The stored risk model covers the whole span.
Each book took ~3.5 h.

| | VRP | IV z-score |
| --- | --- | --- |
| positions / gross vega | 41 / $12.0k | 58 / $12.3k |
| gross / costs / net | +$952k / $7.09M / −$6.13M | −$112k / $6.63M / −$6.74M |
| turnover / gross vega, annual | 30× | 25× |
| market vol loading (t) | −0.11 (−3.4) | −0.25 (−8.6) |
| intercept, net per $ gross vega per day (t) | −0.30 (−9.9) | −0.30 (−10.8) |

Forward 60-session gross P&L per dollar of vega, long reference straddle,
by signal decile (1,756–1,758 formation dates, ~47 names per decile-day):

| decile | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| VRP | 2.04 | 2.29 | 2.34 | 2.26 | 2.23 | 2.25 | 2.01 | 1.76 | 1.69 | **0.21** |
| VRP t | 6.5 | 7.3 | 7.3 | 7.1 | 6.9 | 7.0 | 6.4 | 5.7 | 5.5 | 0.7 |
| IV z | **1.42** | 1.96 | 2.00 | 1.99 | 1.95 | 2.06 | 2.19 | 2.26 | 2.15 | **1.42** |
| IV z t | 4.5 | 6.0 | 6.4 | 6.3 | 6.2 | 6.6 | 7.2 | 7.4 | 6.9 | 4.2 |

**What it says.** Over the sample, long hedged straddles paid about 2 points
of vega per 60 sessions before costs in almost every bucket. The VRP
signal's top decile — the names it calls richest after the market, sector
and idio-vol controls — earned essentially nothing (0.21, t 0.7), with a
monotone fade from decile 6 upward. That is the effect the signal is built
to find, and it needs years of formation dates to be visible. The
time-series z-score is U-shaped: its richest decile earns the same as its
cheapest, the middle is best. High IV relative to a name's own past is as
often the start of a vol episode as the end of one, and the book that
shorts it carries a significant short-vol tilt (market loading −0.25,
t −8.6) that the $1k factor tolerance lets through.

**What it does not say.** Neither book is profitable net of costs; the
VRP book's gross of +$952k over seven years on $12k of vega (+0.3 points a
session on gross exposure) is the number to build on, and the cost side is
a cadence and fill-assumption question. Net of the full half-spread every
decile is negative in both tables.

## Open questions

* How much of the VRP decile-10 effect is earnings timing? Names a week
  before reporting have rich front IV and the surface has no earnings
  adjustment. The `EarningsAdjuster` hook is where to test it.
* Roll cadence: a 90-day entry rolled at 30 halves the roll count. The
  reference returns and stored model would need rebuilding with the new
  `StraddleConfig`.
* A multi-period turnover term, so the optimizer stops paying a round trip
  to chase a one-week alpha change.
* The 18 wrong-company symbol-years from the symbology check are not
  screened.
