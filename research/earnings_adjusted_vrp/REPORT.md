# Earnings and the cross-sectional variance premium, 2018–2025

## Bottom line

The raw variance-premium signal in voltium is partly a calendar, but the
alpha is not. Names within a week of their print have an average signal
z-score of +0.37 and fill 17% of the top decile; names 61–90 days out
average −0.35 and fill 4%. Stripping the earnings jump from the surface
with a term-structure estimate, and the earnings sessions from the realized
side, flattens that to +0.11 and −0.08 with every bucket near its 10% share.
Yet the decile table is unchanged — decile 10 earns 0.48 per dollar of vega
either way against 1.6–2.2 elsewhere — and the rank-weighted book is
slightly better after the adjustment: the same P&L, a higher Sharpe (1.40
against 1.35) and a third less drawdown. The forward P&L of the reference
straddle does not depend on distance to the print (1.4–1.8 per vega in
every bucket inside 90 days), which is why removing the calendar from the
signal costs nothing.

| Rank book, $20k gross vega, 1,758 sessions, no costs | Raw | IV adjusted | Fully adjusted |
| --- | --- | --- | --- |
| gross P&L | +$914k | +$885k | +$888k |
| Sharpe | 1.35 | 1.33 | 1.40 |
| max drawdown | −$95k | −$79k | −$66k |
| decile 10 forward P&L per $ vega (t) | 0.48 (1.6) | 0.56 (1.9) | 0.48 (1.6) |
| decile 1 | 1.59 (5.5) | 1.51 (5.1) | 1.74 (5.7) |

## Construction

**Events.** The store's earnings table (Yahoo-sourced) maps each print to
the session whose close-to-close return carries the move: the same session
for a before-open report, the next for an after-close one. 507 of the 659
names in the seven-year universe have dates; the missing 152 are the names
that left the index, so the study runs on the 507 and the raw signal is
rebuilt on the same names. Coverage on the 507 is complete: 34 prints per
name over eight and a half years, 99% with a known session, and the
existing earnings-straddle study confirmed the dates by the 24% IV crush
through them.

**Jump variance.** On each (date, symbol), with `w = iv² × dte / 365` the
total variance of an ATM pillar: the nearest pillar expiring before the
event gives the diffusive rate `v = w_B / t_B`, the nearest after gives
`J = w_A − v t_A`. That estimate exists on 67% of name-days within 60 days
of a print; on 32% no pillar expires before the event and the name's
trailing 250-session median of its own estimates stands in; 0.5% get
nothing. The median implied move `sqrt(J)` is 4.4% under both methods,
rising from 3.8% in 2018 to 4.8% in 2024. (The earnings-straddle study's
6.2% is straddle-over-spot on a weekly, which includes the week's diffusive
variance and the straddle's 0.8 factor; the two are consistent.)

**Adjusted surface.** Every pillar spanning `k` prints loses `k × J × 365 /
dte` of `iv²`, floored at a quarter of the original variance, then the
usual total-variance interpolation to 30/60/90 days.

**Adjusted realized vol.** Close-to-close with the event sessions' squared
returns left out of every window, so the HAR target and features are
diffusive too. The ex-earnings HAR puts 0.87 on the 22-day term against
0.81 raw, and its residual variance falls from 0.53 to 0.48: the forecast
is cleaner once the lumps are out.

**Signals.** Three versions of `CrossSectionalVRPSignal`, identical apart
from the inputs: raw surface and forecast; adjusted surface with the raw
forecast; adjusted surface with the ex-earnings forecast. Same controls
(beta × SPX premium, sector median, idio vol), smoothing and winsorisation.
Scored on the 60-session decile table and on `RankWeightedStrategy` at $20k
gross vega, weekly, on the panel engine.

## Results

### The raw signal is a calendar

| Days to next print | Name-days | Raw z | Adjusted z | Raw share in decile 10 | Adjusted share | Forward P&L per $ vega |
| --- | --- | --- | --- | --- | --- | --- |
| 0–7 | 69k | +0.37 | +0.11 | 16.8% | 12.6% | 1.44 |
| 8–30 | 187k | +0.20 | +0.02 | 13.3% | 10.4% | 1.64 |
| 31–60 | 233k | +0.09 | +0.01 | 10.7% | 10.1% | 1.77 |
| 61–90 | 219k | −0.35 | −0.08 | 4.4% | 8.4% | 1.53 |
| 91+ | 25k | −0.31 | +0.06 | 6.2% | 10.9% | 0.11 |

The raw z-score moves by 0.7 across the quarter for no reason other than
where the print sits relative to the 60-day tenor. That is a fifth of a
standard deviation of dispersion, cross-sectionally, spent on the calendar.
The adjustment removes almost all of it; the residual +0.11 in the last
week is the term-structure estimate undershooting when the pre-event
pillar is itself elevated, which biases `v` up and `J` down.

The forward P&L column is the finding. A long reference straddle earned
about the same over the next 60 sessions whether the print was a week away
or two months away. The calendar loading in the raw signal was therefore
noise: it moved names between deciles without moving their returns.

### The decile-10 collapse survives

Raw and fully adjusted decile tables are the same table to within sampling
error (figure, left panel). Decile 10 earns 0.48 per vega in both, with
the same t of 1.6; deciles 2–6 earn 2.0–2.3 in both. The IV-only
adjustment, which changes the surface but not the forecast, is also the
same. Whatever the richest decile is picking up, it is not names about to
report.

### The book improves at the margin

Gross P&L is unchanged (−3%, inside the noise of a 51× turnover book).
Sharpe rises from 1.35 to 1.40 and the maximum drawdown falls from $95k to
$66k. Removing a factor that moved weights without moving returns removes
variance without removing mean, which is what one would expect.

## What this says about the signal

The earnings hypothesis for decile 10 is rejected: the names the signal
calls richest are not rich because they are about to report, and shorting
them was not a bet against the earnings move. Two of the three stories for
the premium remain open. Decile 10's near-zero long P&L is consistent
either with option sellers being correctly paid in those names or with the
market pricing information the HAR cannot see; the test that separates them
is whether decile-10 names realize more vol than their forecast.

Two side observations. First, the raw premium `ln(iv60) − ln(rv_fcst)` is
negative on average in every bucket (−0.05 to −0.24): the pooled HAR with
its lognormal correction forecasts more realized vol than the market
implies, which is the same "no single-name premium at the money" result the
variance-risk-premium study found from the other direction. Second, the
adjusted signal has a small positive z in the last week before the print.
A per-name history of `J` measured on the last clean day before each event
would remove the term-structure bias; it is a refinement, not a fix.

## Caveats

* Survivorship in the events table: the 152 names without earnings dates
  are the departed ones. Both raw and adjusted signals were rebuilt on the
  507 covered names, so the comparison is fair, but the seven-year
  voltium books in `voltium/docs/experiments.md` include the 152 and this
  study's raw book is not identical to them.
* The term-structure estimate assumes a flat diffusive term structure
  between the two pillars. A steep or inverted structure biases `J`; the
  90-day gap cap and the quarter-variance floor limit the damage.
* The adjustment is applied to `iv60` only through the surface. Nothing
  else in the pipeline — the risk model, the reference returns — is
  earnings-aware. The reference straddle still holds through prints, so
  the decile table's forward P&L includes the earnings move.
* `TermStructureEarningsAdjuster` collects the pillars per symbol; on
  the full universe that is fine (69 s for 507 names, eight years) but it
  is not a lazy transform.
