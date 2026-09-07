# Optimizer

## The problem

`MVO(objectives, constraints, solver="CLARABEL")` builds, over `w` in dollar
vega for the risk model's symbols:

```
maximize   sum of objective terms
             MaxUtility:       alpha'w − λ · w'Σw
             TurnoverPenalty:  − c · ‖w − w_prev‖₁
             JumpPenalty:      − J · Σ_i max(−w_i, 0) · gap_freq_i
subject to every constraint in the list
             NetVegaNeutral:    |Σ w_i| ≤ tolerance
             FactorNeutral:     |B' w| ≤ ε, per factor (or a named subset)
             PerNameVegaCap:    |w_i| ≤ cap
             GrossShortVegaCap: Σ max(−w_i, 0) ≤ cap
             GrossVegaCap:      Σ |w_i| ≤ cap
```

Objectives and constraints are composable classes in atium's shape: each has
`build(weights, **kwargs)`, an objective returns an expression to
*maximise* (penalties return a negative one), a constraint returns one CVXPY
constraint or a list. `MVO` sums the objective terms and flattens the
constraints.

The kwargs vocabulary (`optimizer/base.py`): `alphas`, `covariance`,
`loadings`, `factors`, `gap_freq`, `previous`, `symbols`. A new term reads
what it needs from there; nothing else is passed.

### Which names are in the problem

`risk_model.symbols`, filtered to those with a non-null alpha today plus
anything currently held (`symbols_with_alpha_only=True`), so a name that
lost its signal can be unwound by the optimizer rather than dumped by the
trade generator. Missing `previous` and `gap_freq` entries default to zero.

### Failure

A solver status other than `optimal` / `optimal_inaccurate` raises
`OptimizationError` with the date and the problem size. It does **not**
return zeros: an infeasible book is a configuration problem the researcher
has to see. `optimal_inaccurate` is logged as a warning.

### Solver

Clarabel by default (installed as a dependency); pass `solver="ECOS"` if
ECOS is installed. The covariance is wrapped in `cp.psd_wrap`, so a
numerically indefinite matrix does not fail the DCP check.

## Calibration

The units are in [units.md](units.md). The numbers that matter:

**λ (`MaxUtility.risk_aversion`)**, units 1/dollars. Without caps the
unconstrained solution is `w = Σ⁻¹α / (2λ)`. With per-vega daily vols σ ≈ 2
and alpha ≈ 0.04 × 2 × z ≈ 0.08 z per day, a name with z = 1 gets
`w ≈ 0.08 / (2 λ × 4) = 0.01 / λ`; λ = 1e-5 puts it at $1,000 of vega, and the
caps do the rest. The demos use 1e-5.

**c (`TurnoverPenalty.cost`)**, per-vega P&L per session. The optimizer is
myopic — it trades today's alpha against today's costs — so `c` cannot be
the cost of a trade. It has to be the round-trip cost amortised over the
expected holding period: the median straddle half-spread is 1.3–1.7 per
dollar of vega, a round trip ~3, and over a ~30-session hold that is ~0.1
per session, which is also the scale of a daily alpha. The demos use 0.10.
Setting it to the actual round-trip cost (1.0) makes the book hold nothing.

**J (`JumpPenalty.penalty`)**, per-vega P&L per session. `gap_freq` is
around 0.016 on this store, so J = 1 charges a short position about 0.016
per dollar of vega per session, a fifth of a typical daily alpha. It is a
nudge away from selling vol in names that gap, not a hard rule.

**Caps.** The demos use gross $20k, per-name $2k, gross short $10k, net-vega
tolerance $500, factor tolerance $1k. The gross cap is rarely reached because
the trade generator's liquidity screens drop names after the optimizer has
sized them; the book runs $10–12k gross.

## What the tests pin down (`tests/test_optimizer.py`)

* On three names with `Σ = s² I` and net-vega-neutral, the solution equals
  the closed form `w_i = (α_i − mean α) / (2 λ s²)` to 1e-4.
* Per-name and gross-short caps bind; a huge turnover penalty pins the
  solution to the previous book to 1e-4.
* A contradictory constraint set raises `OptimizationError`.

## Known behaviour

The myopic formulation with a weekly rebalance turns the book over 30–38
times gross vega a year in the demos, much faster than a 30-session signal
horizon warrants, and that turnover — plus a roll every few weeks — is what
makes the demo books cost-dominated. The levers, in order of leverage:
`TurnoverPenalty.cost`, the trade generator's no-trade `band`, the rebalance
cadence and signal smoothing, `StraddleConfig.roll_dte` / `restrike_moneyness`.
A multi-period formulation (alpha decay against amortised cost) would be the
principled fix and fits the `Objective` interface.
