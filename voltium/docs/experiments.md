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

### The panel engine against the chain engine

`panel_backtester.py` holds fractional units of the stored reference
straddle per name and reads P&L off the reference panel; six months of the
VRP book takes 1 s (rank) or 5 s (MVO) of backtest against 12 minutes.
On two synthetic chains the two engines agree to 1e-6 through several
rolls. On the real six-month books they do not:

| VRP book, six months | chain engine | panel engine |
| --- | --- | --- |
| rank: gross P&L / Sharpe | +$80k / 2.35 | +$64k / 1.59 |
| MVO: gross P&L / Sharpe | +$140k / 1.02 | +$122k / 0.80 |
| daily book P&L correlation | 0.88 (rank), 0.90 (MVO) | |
| mean gross vega, positions | identical | |

The gap is the **identity of the unit**, not the accounting. Per name-day,
93% of rows hold the identical straddle (same expiration and strike) and
there the two engines' P&L correlates at 0.999 on ordinary days. The other
7% hold the same expiration at a different strike, and those rows carry
93% of the absolute P&L difference. They arise when the 15% re-strike rule
fires on a different date in the two books: the reference path's strike is
the one it rolled into on its own schedule, the chain engine's is the one
struck when the book entered the name, and a name that moves 15% pushes
one across the band before the other (HUM in January 2025 is the worked
example: reference re-struck on the 13th, chain book on the 28th, then
two straddles 7% apart on the same expiration). Because these are, by
construction, the names with the largest moves, their gamma-dominated P&L
is strike-specific and nearly uncorrelated between the two strikes (0.18).

**What it says.** "The 60/20 rolling ATM straddle" is not a well-defined
return series; its daily P&L depends on the strike's phase, and that
dependence is concentrated exactly where the P&L is largest. The panel
engine's series is the one the risk model and the decile table are
estimated on, so a panel-engine book is internally consistent; the chain
engine's book is what a trader who entered on the book's dates would have
held. Neither is wrong. The open design question is whether the reference
unit should be defined to remove the phase — a daily re-struck (constant
moneyness) synthetic, or an average over several staggered roll schedules
— which would also make the risk model's returns less noisy.

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

### Seven-year research-configuration books on the panel engine

Same four books as the six-month table above, 2018-07-02 to 2025-06-30,
1,758 sessions, 659 names, close-to-close realized vol, panel engine
(`--engine panel --rv-source close`; logs `demos/*_panel_long.log`):

| | VRP, MVO | VRP, rank | IV z, MVO | IV z, rank |
| --- | --- | --- | --- | --- |
| mean positions / gross vega | 476 / $19.5k | 470 / $19.4k | 489 / $19.1k | 485 / $19.5k |
| gross P&L | +$2.97M | +$1.07M | +$1.58M | +$1.31M |
| annualised gross P&L per $ gross vega | 21.9 | 7.9 | 11.8 | 9.7 |
| Sharpe (gross) | 1.28 | 1.56 | 0.67 | 1.50 |
| max drawdown | -$280k | -$83k | -$597k | -$223k |
| annual turnover / gross vega | 40x | 50x | 35x | 51x |
| P&L loading on market vol factor (t) | +0.01 (0.3) | +0.01 (0.9) | -0.08 (-3.3) | -0.08 (-8.3) |
| intercept, P&L per $ gross vega per day (t) | 0.081 (3.2) | 0.030 (4.0) | 0.048 (2.0) | 0.040 (4.6) |
| backtest time | 63 s | 11 s | 64 s | 10 s |

**What it says.** Over seven years every book is positive gross and every
intercept is significant, the rank books at t 4.0–4.6. The ordering of the
signals reverses from the six-month window: the VRP book earns more than
the IV z-score under both sizers, as the seven-year decile tables said it
should (decile 10 of the VRP earns 0.21 per vega, the z-score's is
U-shaped). The MVO-versus-rank pattern from the six-month runs holds: the
optimizer buys dollars with concentration — the VRP MVO book earns 2.8×
the rank book's P&L at 3.4× its drawdown and a lower Sharpe; on the IV
z-score the optimizer is strictly worse, Sharpe 0.67 against 1.50, and its
$597k drawdown is a third of its total P&L. The z-score books carry a
significant short market-vol loading (−0.08, t −3.3 and −8.3) with no
factor-neutral constraint to remove it; the VRP books do not, because the
signal residualises it out before the optimizer sees it.

**What it does not say.** Costs are off and turnover is 35–51× gross
vega a year, so none of this is net of anything; the `--costs` rerun is
the next number to look at, and it is now a two-minute job. These are
panel-engine books, so they hold the reference unit's strike phase (see
the agreement note above), and the same books on the chain engine would
differ by the amount that note describes.

### Momentum and reversal in the reference returns

`research/vol_momentum` and `research/vol_reversal` (Heston, Jones,
Khorram, Li and Mo, 2023), on the seven-year panel with rank and MVO books
on the panel engine. Momentum in the reference straddle's past P&L is the
strongest sort on this panel — a 2.3-point 60-session decile spread at
6 months skipping the last month, monotone, against the VRP's 1.8 — but
the rank book earns Sharpe 0.9 at best because it carries a long
market-vol tilt (t 8) and turns over 31× a year. Including the last month
turns the book negative. Short-horizon reversal is bid-ask bounce: a
one-week reversal book has gross Sharpe 4.5 with the last session in and
1.05 with it out. Two rules for the panel engine follow: signals built
from the reference return skip at least one session, and a gross Sharpe
above about 2 on this panel is bounce until a skip test says otherwise.

## Open questions

* ~~How much of the VRP decile-10 effect is earnings timing?~~ Answered in
  `research/earnings_adjusted_vrp`: none of it. The raw signal's z-score
  moves 0.7 across the quarter with distance to the print, but the forward
  P&L does not, and stripping the jump leaves the decile table unchanged
  and the rank book slightly better (Sharpe 1.35 → 1.40).
* Do decile-10 names realize more vol than their forecast (the market has
  information) or realize the forecast (sellers are being paid)?
* Residualise the momentum signal against the market straddle factor, or
  restore `FactorNeutral` for it; the tilt is most of the gap between its
  sort and its book.
* Mark the reference unit at the far touch, or skip two sessions, to bound
  how much short-horizon reversal is real.
* Roll cadence: a 90-day entry rolled at 30 halves the roll count. The
  reference returns and stored model would need rebuilding with the new
  `StraddleConfig`.
* A multi-period turnover term, so the optimizer stops paying a round trip
  to chase a one-week alpha change.
* The 18 wrong-company symbol-years from the symbology check are not
  screened.
