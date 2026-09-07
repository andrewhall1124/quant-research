# voltium documentation

voltium is a research backtester for single-name volatility books: cross-sectionally
long cheap implied vol and short expensive implied vol through delta-hedged ATM
straddles on the S&P 500 universe, in the shape of the equity framework
[atium](https://github.com/Atium-Research/atium).

These pages are written for someone who will read the code. Each names the
module it describes, states the conventions the code relies on, and says
where the equity abstraction stops.

| page | what it covers |
| --- | --- |
| [architecture.md](architecture.md) | the pipeline end to end, the module map, what carries over from atium and what does not |
| [data.md](data.md) | the store, the loaders, the canonical schemas, coverage, the vega-convention check |
| [units.md](units.md) | dollar vega, P&L per dollar of vega, and the units of every quantity the optimizer sees |
| [instrument_and_backtester.md](instrument_and_backtester.md) | contract selection, marking, rolling, hedging, the daily loop, portfolio state, the records schema |
| [risk_model.md](risk_model.md) | reference straddle returns, factors, estimation, the `vol-risk-model` pipeline, the stored tables |
| [signals_and_alphas.md](signals_and_alphas.md) | surface, realized vol and HAR, stock features, the two signals, the alpha |
| [optimizer.md](optimizer.md) | the CVXPY problem, objectives, constraints, calibration, failure modes |
| [trade_generator_and_costs.md](trade_generator_and_costs.md) | vega to contracts, liquidity screens, the no-trade band, cost models |
| [results.md](results.md) | every summary statistic, the factor regression, the decile table, the plots |
| [running.md](running.md) | install, tests, building the store, running the demos, caches, runtime, memory, reproducibility |
| [extending.md](extending.md) | adding an earnings adjuster, a term-structure or skew instrument, a signal, a risk model, a cost model |
| [experiments.md](experiments.md) | what has been run so far and what it showed |

A reading order for a first pass: architecture → units → instrument_and_backtester
→ risk_model → signals_and_alphas → optimizer → results → experiments.
