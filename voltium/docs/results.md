# Results

`BacktestResults(records_df)` wraps the backtester's per-`(date, symbol)`
records. The book series is derived once in `__post_init__` (`book_df`):

| column | definition |
| --- | --- |
| `gross_pnl` | Σ option_pnl + hedge_pnl |
| `option_pnl`, `hedge_pnl` | the two parts |
| `cost` | Σ option_cost + hedge_cost |
| `net_pnl` | gross − cost |
| `cumulative_net` | running sum of net |
| `gross_vega` | Σ \|dollar_vega\| |
| `net_vega` | Σ dollar_vega |
| `dollar_theta` | Σ dollar_theta |
| `abs_delta` | Σ \|book_delta\| (zero by construction) |
| `turnover` | Σ over names of \|dollar_vega − previous session's\| |
| `positions` | count of non-zero contracts |

## `summary()`

| key | definition |
| --- | --- |
| `sessions` | rows in the book series |
| `mean_positions` | mean of `positions` |
| `mean_gross_vega` | mean of `gross_vega` over sessions where it is positive |
| `mean_net_vega` | mean of `net_vega` |
| `total_gross_pnl`, `total_cost`, `total_net_pnl` | sums |
| `sharpe` | mean(net) / std(net, ddof 1) × √252 |
| `annual_net_pnl_per_gross_vega` | mean(net) × 252 / mean_gross_vega — vol points of annual P&L per dollar of gross vega |
| `max_drawdown` | minimum of cumulative net minus its running maximum, dollars |
| `annual_turnover_over_gross_vega` | mean(turnover) × 252 / mean_gross_vega — how many times the gross book is traded a year |
| `cost_drag` | total cost / \|total gross P&L\| |

Sharpe here is on *net* daily P&L in dollars on a book whose gross vega is
held roughly constant by the caps, so it is comparable across runs of the
same configuration and not to a return-based Sharpe.

## `factor_regression(factor_returns_df, market_return_df=None, scale_by_gross_vega=True)`

OLS of daily net P&L (per dollar of gross vega, by default) on the risk
factors — `market` and each sector, as `(date, factor, ret)` from
`build_factor_returns` or from `StoredFactorRiskModelConstructor.factor_returns(date)`
— plus the SPX log return when given. Returns `(regressor, coefficient, t_stat, r_squared, observations)`.

With the dependent variable scaled by gross vega the coefficients read like
a single name's loadings: a `market` coefficient of −0.1 means the book, per
dollar of gross vega, is short a tenth of a unit of the SPX reference
straddle. The intercept is the daily net P&L per dollar of gross vega after
the factors, i.e. what the book earns that the factors do not explain —
negative in every demo because of costs.

## `decile_table(signal_df, reference_df, horizon_days=60, deciles=10)`

Static; it does not use the book at all. For every `(date, symbol)` in the
signal panel with a matching reference path:

* `gross_fwd` = Σ of `pnl_per_vega` over the next `horizon_days` sessions of
  the reference straddle — what a long unit bought at that close earned per
  dollar of vega, rolled and hedged as the book would, before costs.
* `net_fwd` = `gross_fwd` − Σ `cost_per_vega` over the same sessions (rolls,
  hedging) − today's `exit_cost_per_vega` (to enter) − the horizon date's
  `exit_cost_per_vega` (to exit).

Names are ranked by signal each session into `deciles` buckets (1 = lowest
signal = implied cheap, 10 = highest = implied rich), averaged within
bucket per session, then averaged over sessions. Output per decile:
`gross_fwd_pnl_per_vega`, `net_fwd_pnl_per_vega`, `gross_t_stat` (mean over
std × √sessions of the per-session decile means — the sessions overlap, so
treat it as indicative), `mean_names`, `dates`.

The table answers "does the signal order forward straddle P&L?" independent
of the optimizer, caps, screens and rebalance cadence. A signal that shorts
richness should show decile 10 below decile 1; over the seven-year sample
the VRP signal does (see [experiments.md](experiments.md)).

## Plots

* `plot_equity_curve(path)` — cumulative net and gross P&L (top), gross and
  net dollar vega (bottom).
* `plot_deciles(decile_df, path, horizon_days=60)` — gross and net forward
  P&L per dollar of vega by decile.

Both use matplotlib's Agg backend and write a PNG.

## Reading a demo equity curve

The gross line is the book before costs; on a vega-balanced, factor-neutral
book it is the sum of the names' idiosyncratic outcomes and should look like
noise around whatever the signal earns. The net line separates from it at a
roughly constant slope once the book is at full size: that slope is the
daily cost run rate. A cliff in the net line with no cliff in the gross line
is a day many units rolled together. The bottom panel shows gross vega
ramping as the optimizer fills the budget subject to the screens, and net
vega oscillating inside its tolerance.
