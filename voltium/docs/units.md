# Units

Getting the units straight is most of what makes an options optimizer well
posed. This page states every one the code relies on.

## Dollar vega

A position, a target, and an optimizer weight are all in **dollar vega**:
dollars of P&L per 1.00 vol point (a 0.01 change in implied vol).

```
dollar vega of a position = vega_per_share_per_point × 100 × contracts
```

`vega_per_share_per_point` is the canonical `vega` column (the vendor's
per-1.00 vega divided by 100; see [data.md](data.md)). `100` is the
contract multiplier (`loaders.CONTRACT_MULTIPLIER`). `contracts` is signed:
long straddles positive.

For a straddle unit, `UnitMark.vega` is the sum of the call's and the put's
per-share vega, so one contract's dollar vega is `mark.vega × 100`. A 60-DTE
ATM straddle on a $200 stock at 30% vol has a per-share vega of about 0.5 per
point, so one contract is about $50 of vega; a book of $10k gross vega is
about 200 straddle contracts spread over 40 names.

## P&L per dollar of vega

The unit of every "return" in the system. For a reference unit:

```
pnl_per_vega[t] = (option_pnl[t] + hedge_pnl[t]) / entry_vega
```

where `entry_vega` is the dollar vega of the unit held into session `t`, at
the close it was opened or rolled into. A value of `1.0` means the position
made what a one-point rise in IV would have made on the vega bought. It is
*not* a change in IV: a hedged straddle's daily P&L is dominated by gamma
against theta, and single names show daily standard deviations of 1–4 in
these units.

Why inception vega rather than the previous close's: a straddle that has
drifted from the money has a small current vega, and dividing a gamma-driven
P&L (or a roll cost) by it gave per-vega values in the hundreds that were an
artefact of the denominator. Inception vega is what the book sized on. See
`providers/straddle_returns.py`.

`cost_per_vega` and `exit_cost_per_vega` use the same denominator.

## The quantities the optimizer sees

`optimizer/base.py` fixes the kwargs vocabulary. With `w` the (n,) variable in
dollar vega:

| kwarg | shape | unit |
| --- | --- | --- |
| `alphas` | (n,) | dollars of P&L per day per dollar of vega — i.e. per-vega P&L per day |
| `covariance` | (n, n) | daily covariance of per-vega P&L |
| `loadings` | (n, k) | per-vega P&L per unit of factor per-vega P&L (dimensionless) |
| `gap_freq` | (n,) | fraction of trailing sessions with a > 3-sigma stock move |
| `previous` | (n,) | dollar vega currently held |

So:

* `alphas @ w` is dollars per day.
* `w @ covariance @ w` is dollars² per day; `sqrt` of it is the book's daily
  P&L standard deviation in dollars.
* `loadings.T @ w` is dollar vega of exposure to each factor.
* `MaxUtility.risk_aversion` (λ) multiplies dollars² and is subtracted from
  dollars, so it has units of **1 / dollars**. With a book of ~$10k gross
  vega and per-vega daily vols of ~1–3, `w @ Sigma @ w` is of order 1e8 and
  `alpha @ w` of order 1e3; λ around 1e-5 makes the two terms comparable.
* `TurnoverPenalty.cost` (c) multiplies `|w - w_prev|` in dollar vega and is
  subtracted from dollars per day, so it is **P&L per dollar of vega traded,
  per session**. See the calibration note in [optimizer.md](optimizer.md).
* `JumpPenalty.penalty` (J) multiplies `gap_freq × max(-w, 0)`, dollar vega,
  so it is also P&L per dollar of vega per session.
* Every cap (`PerNameVegaCap`, `GrossShortVegaCap`, `GrossVegaCap`,
  `NetVegaNeutral.tolerance`, `FactorNeutral.epsilon`) is in dollar vega.

## Alpha

```
alpha_i = -IC × sigma_idio_i × z_i
```

`z_i` is a cross-sectional z-score (dimensionless). `sigma_idio_i` is the
daily idiosyncratic standard deviation of the name's per-vega P&L from the
risk model, so the product is per-vega P&L per day, the same unit as the
covariance's square root. `IC` (0.04 by default) is the assumed correlation
between score and realised per-vega P&L. The sign flip makes a rich name
(positive z) a short-vega target.

## Costs

* `HalfSpreadCost.compute(contracts, bid, ask, price)` returns
  `|contracts| × ((ask - bid) / 2 × fraction × 100 + fee_per_contract)`
  dollars. `bid` and `ask` are the *straddle's* (sum of the two legs'), so
  the half-spread is the cost of crossing from mid to the touch on both legs.
* `PerShareCost.compute(shares, ...)` returns `|shares| × per_share` dollars.
* `exit_cost` in the records is what closing the position at today's
  half-spread would cost, in dollars; nothing is charged for it.

A median straddle half-spread on this store is about 1.3–1.7 per dollar of
vega — that is, exiting a straddle costs the equivalent of a 1.3–1.7 point
IV move against you. A round trip is ~3. That number is why the demos are
cost-dominated.

## Realized and implied vol

Both are annualised decimals (0.25 = 25%). Realized-vol panels carry vols,
not variances: `rv_22` is the square root of the annualised 22-session
variance. The HAR regresses log *variance* on log variances and applies the
lognormal correction, then returns a vol (`rv_fcst`).

## Time

`dte` is calendar days to expiration (as the vendor and the roll rule count
it). Windows for rolling statistics (250, 60, 22, 5) are trading sessions.
`tau` in Black-Scholes is `dte / 365`. Annualisation of session-frequency
statistics uses 252.
