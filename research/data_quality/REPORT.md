# Is the ThetaData EOD feed good enough to build on?

Yes — the feed itself is close to spotless. Every quality problem this audit
found is a **corporate-action problem**, and none of them is fixed by paying
ThetaData more.

Sample: five S&P 500 roots (AAPL, NVDA, JNJ, MOH, NWSA) across two one-month
windows, 332,632 contract-days, plus a sweep of all 114,366,912 contract-days
already in `data_store/options_2025/`.

## 1. What the account can actually call

The published tier table and the per-endpoint badges contradict each other, so
the audit asks the server directly. `option_history_*` on a FREE account:

| Endpoint | Allowed | Server says |
|---|---|---|
| `stock_history_eod` | yes | |
| `option_history_eod` | yes | |
| `option_history_open_interest` | no | needs **VALUE** |
| `option_history_greeks_eod` | no | needs **STANDARD** |
| `option_history_greeks_implied_volatility` | no | needs **STANDARD** |
| `option_history_greeks_first_order` | no | needs **STANDARD** |

This settles the tier question from the other direction: the pricing page
advertises 1st-order greeks and IV at VALUE, and the server refuses them at
anything below STANDARD. Open interest, which the subscription table lists as
FREE, needs VALUE.

## 2. The feed is clean

Across 332,632 sampled contract-days, in both the 2025 and the 2023 window:

| Check | 2025-06 | 2023-07 |
|---|---|---|
| crossed quotes (bid > ask) | 0 | 0 |
| locked quotes (bid == ask > 0) | 0 | 0 |
| rows with no quote at all | 0 | 0 |
| duplicate contract-days | 0 | 0 |
| null fields | 0 | 0 |
| rows for already-expired contracts | 0 | 0 |
| zero bid | 10.8% | 10.4% |
| no trade that day | 53.5% | 53.3% |

The same checks over the full 114 M stored contract-days give the honest rates
rather than zeros: **8,723 crossed quotes (0.0076%)**, **4,275 rows with no
quote at all (0.0037%)**, and zero negative prices, zero rows that traded but
carry a zero close. That is a very low defect rate for OPRA EOD data.

Two documented caveats did not reproduce. The docs warn that EOD quote fields
"may lack availability before December 1, 2023" — the July 2023 window is fully
populated, with the same 0% missing-quote rate as 2025. And the `created`
timestamp really is the 17:15 ET report, clustered between 17:15 and 17:26.

## 3. Quotes are simultaneous, which is the thing that actually matters

Put-call parity on ATM pairs (0.97–1.03 moneyness, 20–60 dte), strike
discounted at SOFR, comparing the implied spot to the cash close:

| Root | Pairs | Median error | Median abs error | p95 abs error |
|---|---|---|---|---|
| NVDA | 538 | +0.4 bp | 3.0 bp | 52 bp |
| AAPL | 230 | +2.5 bp | 5.1 bp | 37 bp |
| JNJ | 173 | +2.4 bp | 8.3 bp | 52 bp |
| NWSA | 9 | −2.1 bp | 2.8 bp | 20 bp |
| MOH | 49 | −9.3 bp | 19.5 bp | 66 bp |

A feed that stamped a stale option quote onto a fresh close would show errors of
tens to hundreds of basis points and a median far from zero. Three basis points
on NVDA means the call quote, the put quote and the stock close were struck at
the same instant. The residual dispersion tracks liquidity exactly as it should,
and the small negative median on MOH is the dividend the carry model omits.

Spread quality decays hard down the ladder, which is a property of the market
rather than the vendor — but it bounds what the bottom of the index can support:

| Root | ATM relative spread (median) | Median ATM volume | Share with no trade |
|---|---|---|---|
| NVDA | 1.9% | 303 | 0.8% |
| AAPL | 3.2% | 479 | 0% |
| JNJ | 18.1% | 10 | 19% |
| MOH | 25.1% | 0 | 58% |
| NWSA | 40.0% | 0 | 67% |

At the NWSA end, two-thirds of at-the-money contracts do not trade on a given
day and the quoted spread is 40% of the mid. Greeks and IV from a STANDARD
subscription will be *computed* for those contracts, but they will be computed
off a 40%-wide mid. Any S&P 500-wide study needs a liquidity filter regardless
of tier.

## 4. Everything that is actually broken is a corporate action

ThetaData serves **raw, unadjusted prices**. In `underlying_2025.parquet`:

**Six unadjusted splits.** The price ratio lands on a whole number:

| Symbol | Date | Naive return | Ratio |
|---|---|---|---|
| ORLY | 2025-06-10 | −93.2% | 15:1 |
| NFLX | 2025-11-17 | −90.1% | 10:1 |
| NOW | 2025-12-18 | −80.4% | 5:1 |
| IBKR | 2025-06-18 | −74.7% | 4:1 |
| TPL | 2025-12-23 | −67.3% | 3:1 |
| FAST | 2025-05-22 | −50.0% | 2:1 |

Anything computing returns off `close` currently books a −93% day on O'Reilly.
The split session itself can also be corrupt: NVDA's 10:1 on 2024-06-10 carries
a high of 195.95 against an actual post-split range near 117–123, so the print
mixes pre- and post-split ticks.

**Three delisting stubs.** HES (2025-07-18), JNPR (2025-07-02) and K
(2025-12-11) each get one final row with open = high = low = close = 0 — HES
with 104,703 shares of volume attached. Naive returns book a −100% day, and
because the price is 0 rather than null nothing downstream flags it.

**One reused ticker.** SOLS is a legitimate index member from 2025-10-30, but
the feed also returns four rows in Jan–Apr 2025 priced at $0.000001–$0.0001 with
real volume — a different instrument under the same symbol. This is the same
failure already recorded for BNY in `CLAUDE.md`: ThetaData answers a symbol
request with *something* rather than an error.

**No splits endpoint in the client.** The tier table lists a Splits endpoint at
every level, but `thetadata` 1.0.10's `ThetaClient` exposes no method for it. An
adjustment factor has to come from somewhere else.

## 5. One trap in the option schema

There is no `date` column and no underlying price on the EOD chain — the
session date has to come from `created.dt.date()`. And for the 53.5% of
contract-days with no trade, `open/high/low/close` are **0.0, not null**. Any
`close`-based computation silently treats half the chain as a zero-priced
option. Filter on `volume > 0`, or use the mid.

## What to do

1. Buy Options STANDARD for greeks/IV/OI — confirmed by the server, not the
   marketing page.
2. Build a corporate-actions table before trusting any return computed off
   `close`. Six splits, three delistings and one ticker collision in a single
   year of 500 names is not a tail case.
3. Drop zero-price rows at load time in `data_access_layer/loaders.py`, the same
   way the SPX holiday rows are already dropped.
4. Carry a liquidity filter into any cross-sectional option study. Quotes are
   honest everywhere; they are only *informative* in the top half of the index.
