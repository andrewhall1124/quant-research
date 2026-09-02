"""Scoring a dollar P&L series.

Everything is computed on dollars, so there is no return series and no implied
capital base. That is deliberate: a vega-weighted option book has no natural
denominator, and inventing one (premium, notional) adds a noisy scaling that
has nothing to do with whether the signal works. Sharpe is scale-free, so it
survives the choice intact.

The one statistical point that matters here. `research/single_name_vol/`
needed Driscoll-Kraay because it scored a *pooled panel* — 500 names sharing a
market factor on every date. This study aggregates the cross-section into a
single daily portfolio P&L before testing anything, so the panel dimension is
already collapsed and the remaining problem is purely serial: overlapping
cohorts induce autocorrelation out to `holding_days`. Newey-West with that many
lags is the right correction, and using it is not a contradiction of the
project note but the other side of it.
"""

import numpy as np
import polars as pl
import statsmodels.api as sm

TRADING_DAYS = 252


def newey_west_tstat(series: np.ndarray, lags: int) -> tuple[float, float]:
    """Mean of the series and its Newey-West t-statistic against zero.

    `lags` should be the holding period: h overlapping cohorts make today's
    P&L share h-1 days of position with yesterday's, and without the
    correction the t-statistic runs roughly sqrt(h) times too large.
    """
    values = series[np.isfinite(series)]
    if len(values) < lags + 5:
        return float(np.mean(values)) if len(values) else float("nan"), float("nan")
    model = sm.OLS(values, np.ones(len(values))).fit(
        cov_type="HAC", cov_kwds={"maxlags": lags}
    )
    return float(model.params[0]), float(model.tvalues[0])


def summarize(daily_pnl: pl.DataFrame, holding_days: int, column: str = "total_pnl") -> dict:
    """Headline statistics for one run, all in dollars."""
    series = daily_pnl[column].to_numpy().astype(float)
    mean, tstat = newey_west_tstat(series, holding_days)
    std = float(np.nanstd(series, ddof=1))
    cumulative = np.nancumsum(series)
    peak = np.maximum.accumulate(cumulative)

    return {
        "days": int(len(series)),
        "mean_daily_pnl": mean,
        "t_stat_nw": tstat,
        "daily_vol": std,
        "sharpe_annual": float(mean / std * np.sqrt(TRADING_DAYS)) if std > 0 else float("nan"),
        "total_pnl": float(cumulative[-1]) if len(cumulative) else float("nan"),
        "max_drawdown": float(np.max(peak - cumulative)) if len(cumulative) else float("nan"),
        "hit_rate": float(np.mean(series > 0)),
    }


def decile_table(decile_pnl: pl.DataFrame, holding_days: int) -> pl.DataFrame:
    """Mean daily P&L by quantile — the monotonicity check.

    A real cross-sectional signal grades across the sort. Two profitable tails
    with noise in between is a much weaker claim, and is what a spurious
    result usually looks like.
    """
    if decile_pnl.height == 0:
        return pl.DataFrame()
    rows = []
    for (quantile,), group in decile_pnl.sort("quantile", "date").group_by(["quantile"], maintain_order=True):
        mean, tstat = newey_west_tstat(group["pnl"].to_numpy().astype(float), holding_days)
        rows.append({"quantile": int(quantile), "mean_daily_pnl": mean, "t_stat_nw": tstat})
    return pl.DataFrame(rows).sort("quantile")


def compare(results: list, holding_days_by_label: dict | None = None) -> pl.DataFrame:
    """One row per run, for a grid."""
    rows = []
    for result in results:
        holding = result.config.holding_days
        stats = summarize(result.daily_pnl, holding)
        rows.append(
            {
                "label": result.config.label,
                "filters": result.diagnostics.get("filters", ""),
                "holding_days": holding,
                "names_per_side": round(result.diagnostics.get("mean_names_per_side", float("nan")), 1),
                **stats,
            }
        )
    return pl.DataFrame(rows)
