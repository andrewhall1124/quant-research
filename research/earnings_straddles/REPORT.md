# Earnings straddles on S&P 500 names, 2017–2025

## Bottom line

Earnings options on S&P 500 names are systematically overpriced at the
midpoint, and the overpricing is large enough to survive a realistic share
of the spread when the trade is limited to the names with the biggest
implied moves. Across 12,780 events the straddle priced a 6.2% move and the
stock moved 3.3%; only 16% of prints beat their implied move. Selling the
ATM straddle at the last close before the release and covering at the first
close after earned 8.0% of premium per event at the midpoint, positive in
106 of 108 calendar months, with a t-statistic of 15 on monthly means. That
edge is consumed at about half the quoted spread on the whole universe. On
the top tercile by implied move it is 15% per event at the midpoint and
still 7.7% at half the quoted spread.

The run-up into the print is a separate and much smaller thing: a long
straddle over the five sessions before the release earned 1.0% at the
midpoint (t = 2.6) and nothing after any spread.

| Short ATM straddle through the print, per event | Midpoint | 25% of spread | 50% | 100% |
| --- | --- | --- | --- | --- |
| All events, mean | +8.0% | +3.8% | -0.8% | -12.8% |
| All events, t on monthly means | 15.3 | 8.7 | -1.8 | -8.7 |
| Top implied-move tercile, mean | +15.4% | +11.8% | +7.7% | -2.4% |
| Top implied-move tercile, t | 21.0 | 16.1 | 11.1 | 1.2 |

## Construction

**Events.** Announcements from the earnings table for names in the S&P 500
on the day, with a known session: 7,814 before-open and 4,966 after-close
prints with a tradable straddle, from 501 symbols. A before-open report on
day E is entered at the close of E-1 and exited at the close of E; an
after-close report is entered at the close of E and exited at the close of
E+1. The session flag matters: getting it wrong puts the entry after the
move.

**Contract.** On the window's entry day, the strike closest to spot within
2.5%, on the nearest expiry with at least five calendar days left after the
print exit. Median days to expiry is 11, so these are mostly weeklies.
Both legs need a positive bid, ask above bid, volume and a valid IV
inversion. Marks are midpoints; a zero-bid leg is marked at half the ask.

**Run-up.** Entered five sessions before the print entry and closed at the
print entry, on a straddle selected ATM on the run-up day. Selecting it on
the print-entry day instead biases the result badly, because by then spot
has drifted toward that strike: a first pass built that way showed the
run-up losing 13% per event, and it was entirely the selection.

**Returns.** Straddle: exit over entry minus one, signed for the side. Delta
hedged: the same with the entry net delta times the spot move removed.
Costs: the long buys `f` of the half-spread above mid and sells `f` below,
the short the reverse; the exit spread is the entry's relative spread
applied to the exit mark. Statistics are on calendar-month means of the
per-event returns, so the t-statistics reflect 108 earnings-season
observations rather than 12,780 correlated events.

## Results

### The implied move is too big

| Per event | Mean | Median | 25th pct | 75th pct |
| --- | --- | --- | --- | --- |
| Implied move (straddle / spot) | 6.2% | 5.8% | 4.6% | 7.3% |
| Realized move (close to close) | 3.3% | 2.8% | 1.3% | 4.9% |
| Realized / implied | 0.57 | 0.48 | 0.23 | 0.83 |
| IV change through the print | -24% | -23% | -35% | -13% |
| IV change over the run-up | +15% | +13% | +5% | +23% |

The histogram in `figures/earnings.png` is the whole result in one picture:
the ratio of realized to implied move peaks near 0.1 and the mass past 1.0
is a thin tail. IV falls by about a quarter through the print in 94% of
events.

### Long straddle through the print

| Per event, midpoint | Mean | Median | Share positive | t (monthly) |
| --- | --- | --- | --- | --- |
| Straddle | -8.0% | -13.7% | 30% | -15.3 |
| Delta-hedged | -8.0% | -13.1% | 30% | -15.9 |

The straddle and its delta-hedged version are the same trade here: an ATM
straddle held one session has almost no delta. Negative in every year
(-4.9% in 2023 to -12.6% in 2025) and for both sessions, more so for
after-close reports (-10.7%) than before-open ones (-5.6%).

### What separates the events

Long through-print straddle return by within-month tercile of each sort:

| Sort | Tercile 1 | Tercile 2 | Tercile 3 | 3-1 t |
| --- | --- | --- | --- | --- |
| Implied move | -1.2% | -6.0% | -15.4% | -14.0 |
| IV level at entry | -0.1% | -6.2% | -16.3% | -16.7 |
| IV rise over the run-up | -3.0% | -7.3% | -13.5% | -10.2 |
| Own realized/implied ratio, past 4 events | -6.4% | -6.9% | -9.0% | -3.1 |

The bigger the move the market prices, the more it overpays: the top
implied-move tercile prices an 8.2% move and gets a ratio of 0.49, the
bottom prices 4.3% and gets 0.63. A name's own recent history of beating or
missing its implied move carries almost no information, which argues against
the "this stock always moves more than implied" heuristic.

### Costs

Relative quoted spreads on these straddles are wide: the median is 13% of
the straddle price, the interquartile range 8% to 22%. That sets the
breakeven for the whole-universe short at roughly half the quoted spread.
For the top implied-move tercile, the edge is larger and the breakeven moves
out to about 90% of the quoted spread. Those names are also the ones whose
quotes are most likely to be crossable inside the touch, since a large
implied move means a large dollar premium.

### The run-up

| Long straddle, five sessions into the print | Midpoint | 25% of spread | 50% |
| --- | --- | --- | --- |
| Mean per event | +1.0% | -2.0% | -4.9% |
| t (monthly) | 2.6 | -3.4 | -8.4 |

IV rises 15% into the print, but the straddle is a week from expiry and its
theta over five sessions is about the same size. What is left is a percent,
and two spread crossings take three.

## What this says

Selling earnings straddles is the single-name volatility trade this data
supports. The unconditional single-name variance premium (see
`research/variance_risk_premium`) is zero, but the premium concentrated in
the one session of the print is not: the market pays for an expected move
roughly twice the median realized one. The catch is the spread. This is a
trade in the names where the implied move is large in dollars and the quote
can be worked, not a trade to run across the index at the touch.

## Caveats

- Exit at the first close after the print. A short that covers at the open
  captures a different, usually larger, part of the crush; a short that
  holds to expiry takes the subsequent-day drift. Neither is measured here.
- The exit spread is proxied by the entry's relative spread. After the print
  the straddle is smaller and the relative spread wider, so costs on the
  exit are understated at every fraction.
- Earnings dates come from Yahoo. A misdated event puts the entry on the
  wrong side of the release; those events show as a realized move near
  zero on the wrong day and a large one the next, which flatters the short.
  The 6% of events where IV rose through the "print" are the likely
  suspects.
- Margin on short straddles is not modelled. Returns are per dollar of
  premium; per dollar of margin they are a fraction of that.
- Assignment risk on the short leg through an event is real for American
  single-name options and ignored here.
