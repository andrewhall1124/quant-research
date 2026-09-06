"""Backtest results: book series, summary statistics, attribution, deciles.

`BacktestResults` wraps the per-(date, symbol) records the backtester
returns. The book-level series is derived once in `__init__`:

    gross_pnl   option + hedge P&L
    cost        option + hedge costs
    net_pnl     gross - cost
    gross_vega  sum |dollar vega|
    net_vega    sum dollar vega
    turnover    sum |change in dollar vega| (day over day, by name)

`summary()` reports Sharpe on net P&L, annualised net P&L per dollar of
gross vega, max drawdown of cumulative net P&L, annualised turnover as a
fraction of gross vega, and cost drag as costs over gross P&L.

`factor_regression()` regresses daily net P&L on the market vol factor, the
SPX return and the sector factors. `decile_table()` sorts names by signal
each day and reports the forward per-unit-vega P&L of the *reference*
straddle over `horizon_days` sessions, gross and net of costs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import polars as pl

SESSIONS_PER_YEAR = 252


@dataclass
class BacktestResults:
    records_df: pl.DataFrame

    def __post_init__(self) -> None:
        by_name = (
            self.records_df.sort("symbol", "date")
            .with_columns(
                (pl.col("dollar_vega") - pl.col("dollar_vega").shift(1).fill_null(0.0)).abs().over("symbol").alias("vega_traded")
            )
        )
        self.book_df = (
            by_name.group_by("date")
            .agg(
                (pl.col("option_pnl") + pl.col("hedge_pnl")).sum().alias("gross_pnl"),
                (pl.col("option_cost") + pl.col("hedge_cost")).sum().alias("cost"),
                pl.col("option_pnl").sum().alias("option_pnl"),
                pl.col("hedge_pnl").sum().alias("hedge_pnl"),
                pl.col("dollar_vega").abs().sum().alias("gross_vega"),
                pl.col("dollar_vega").sum().alias("net_vega"),
                pl.col("dollar_theta").sum().alias("dollar_theta"),
                pl.col("book_delta").abs().sum().alias("abs_delta"),
                pl.col("vega_traded").sum().alias("turnover"),
                (pl.col("contracts") != 0).sum().alias("positions"),
            )
            .sort("date")
            .with_columns((pl.col("gross_pnl") - pl.col("cost")).alias("net_pnl"))
            .with_columns(pl.col("net_pnl").cum_sum().alias("cumulative_net"))
        )

    # ------------------------------------------------------------------

    def summary(self) -> dict[str, float]:
        book = self.book_df
        if book.is_empty():
            return {"sessions": 0}
        net = book["net_pnl"].to_numpy()
        gross_vega = book["gross_vega"].to_numpy()
        mean_gross_vega = float(np.mean(gross_vega[gross_vega > 0])) if np.any(gross_vega > 0) else float("nan")
        sharpe = float(np.mean(net) / np.std(net, ddof=1) * np.sqrt(SESSIONS_PER_YEAR)) if len(net) > 1 and np.std(net) > 0 else float("nan")
        cumulative = np.cumsum(net)
        drawdown = cumulative - np.maximum.accumulate(cumulative)
        gross_total = float(book["gross_pnl"].sum())
        return {
            "sessions": int(book.height),
            "mean_positions": float(book["positions"].mean()),
            "mean_gross_vega": mean_gross_vega,
            "mean_net_vega": float(book["net_vega"].mean()),
            "total_gross_pnl": gross_total,
            "total_cost": float(book["cost"].sum()),
            "total_net_pnl": float(book["net_pnl"].sum()),
            "sharpe": sharpe,
            "annual_net_pnl_per_gross_vega": float(np.mean(net) * SESSIONS_PER_YEAR / mean_gross_vega),
            "max_drawdown": float(drawdown.min()),
            "annual_turnover_over_gross_vega": float(book["turnover"].mean() * SESSIONS_PER_YEAR / mean_gross_vega),
            "cost_drag": float(book["cost"].sum() / abs(gross_total)) if gross_total != 0 else float("nan"),
        }

    def factor_regression(
        self,
        factor_returns_df: pl.DataFrame,
        market_return_df: pl.DataFrame | None = None,
        scale_by_gross_vega: bool = True,
    ) -> pl.DataFrame:
        """OLS of daily book P&L on the risk factors (+ SPX return).

        `factor_returns_df` is `(date, factor, ret)` as built by
        `risk_model.factor.build_factor_returns`; `market_return_df` is
        `(date, market_return)`. With `scale_by_gross_vega` the dependent
        variable is net P&L per dollar of gross vega, so the coefficients are
        comparable to the loadings of a single name.
        """
        wide = factor_returns_df.pivot(index="date", on="factor", values="ret")
        frame = self.book_df.select("date", "net_pnl", "gross_vega").join(wide, on="date", how="inner")
        if market_return_df is not None:
            frame = frame.join(market_return_df.select("date", pl.col("market_return").alias("spx_return")), on="date", how="left")
        regressors = [c for c in frame.columns if c not in ("date", "net_pnl", "gross_vega")]
        frame = frame.drop_nulls(regressors).filter(pl.col("gross_vega") > 0)
        y = frame["net_pnl"].to_numpy()
        if scale_by_gross_vega:
            y = y / frame["gross_vega"].to_numpy()
        x = np.column_stack([np.ones(frame.height), frame.select(regressors).to_numpy()])
        beta, *_ = np.linalg.lstsq(x, y, rcond=None)
        residual = y - x @ beta
        dof = max(frame.height - x.shape[1], 1)
        sigma2 = float(residual @ residual) / dof
        cov = sigma2 * np.linalg.pinv(x.T @ x)
        se = np.sqrt(np.diag(cov))
        r2 = 1.0 - float(residual @ residual) / float(np.sum((y - y.mean()) ** 2)) if frame.height > 1 else float("nan")
        return pl.DataFrame(
            {
                "regressor": ["const", *regressors],
                "coefficient": beta,
                "t_stat": beta / np.where(se > 0, se, np.nan),
                "r_squared": [r2] * (len(regressors) + 1),
                "observations": [frame.height] * (len(regressors) + 1),
            }
        )

    @staticmethod
    def decile_table(
        signal_df: pl.DataFrame,
        reference_df: pl.DataFrame,
        horizon_days: int = 60,
        deciles: int = 10,
    ) -> pl.DataFrame:
        """Forward per-unit-vega P&L of a long reference straddle by signal decile.

        For each (date, symbol): gross = sum of `pnl_per_vega` over the next
        `horizon_days` sessions; net = gross minus the reference path's costs
        over the same sessions (rolls, hedging) minus the half-spread to
        enter today and to exit at the horizon. Decile 1 is the lowest signal
        (implied cheap), decile 10 the highest (implied rich).
        """
        forward = (
            reference_df.sort("symbol", "date")
            .with_columns(
                pl.col("pnl_per_vega").fill_null(0.0).alias("pnl"),
                pl.col("cost_per_vega").fill_null(0.0).alias("cost"),
            )
            .with_columns(
                pl.col("pnl").reverse().rolling_sum(horizon_days).reverse().shift(-1).over("symbol").alias("gross_fwd"),
                pl.col("cost").reverse().rolling_sum(horizon_days).reverse().shift(-1).over("symbol").alias("cost_fwd"),
                pl.col("exit_cost_per_vega").shift(-horizon_days).over("symbol").alias("exit_at_horizon"),
            )
            .with_columns(
                (pl.col("gross_fwd") - pl.col("cost_fwd") - pl.col("exit_cost_per_vega") - pl.col("exit_at_horizon")).alias("net_fwd")
            )
            .select("date", "symbol", "gross_fwd", "net_fwd")
            .drop_nulls()
        )
        ranked = signal_df.join(forward, on=["date", "symbol"], how="inner").with_columns(
            (pl.col("signal").rank(method="ordinal").over("date") * deciles / pl.len().over("date"))
            .ceil()
            .clip(1, deciles)
            .cast(pl.Int64)
            .alias("decile")
        )
        daily = ranked.group_by("date", "decile").agg(
            pl.col("gross_fwd").mean().alias("gross"),
            pl.col("net_fwd").mean().alias("net"),
            pl.len().alias("names"),
        )
        return (
            daily.group_by("decile")
            .agg(
                pl.col("gross").mean().alias("gross_fwd_pnl_per_vega"),
                pl.col("net").mean().alias("net_fwd_pnl_per_vega"),
                (pl.col("gross").mean() / pl.col("gross").std() * (pl.len() ** 0.5)).alias("gross_t_stat"),
                pl.col("names").mean().alias("mean_names"),
                pl.len().alias("dates"),
            )
            .sort("decile")
        )

    # ------------------------------------------------------------------

    def plot_equity_curve(self, path: Path | str) -> None:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        book = self.book_df
        fig, axes = plt.subplots(2, 1, figsize=(10, 7), sharex=True, gridspec_kw={"height_ratios": [2, 1]})
        dates = book["date"].to_list()
        axes[0].plot(dates, book["cumulative_net"].to_list(), label="net", color="tab:blue")
        axes[0].plot(dates, book["gross_pnl"].cum_sum().to_list(), label="gross", color="tab:gray", linestyle="--")
        axes[0].axhline(0, color="black", linewidth=0.6)
        axes[0].set_ylabel("cumulative P&L ($)")
        axes[0].legend()
        axes[1].plot(dates, book["gross_vega"].to_list(), label="gross vega", color="tab:green")
        axes[1].plot(dates, book["net_vega"].to_list(), label="net vega", color="tab:red")
        axes[1].axhline(0, color="black", linewidth=0.6)
        axes[1].set_ylabel("$ vega")
        axes[1].legend()
        fig.autofmt_xdate()
        fig.tight_layout()
        fig.savefig(path, dpi=150)
        plt.close(fig)

    @staticmethod
    def plot_deciles(decile_df: pl.DataFrame, path: Path | str, horizon_days: int = 60) -> None:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(9, 4.5))
        x = decile_df["decile"].to_numpy()
        width = 0.4
        ax.bar(x - width / 2, decile_df["gross_fwd_pnl_per_vega"].to_numpy(), width, label="gross", color="tab:gray")
        ax.bar(x + width / 2, decile_df["net_fwd_pnl_per_vega"].to_numpy(), width, label="net", color="tab:blue")
        ax.axhline(0, color="black", linewidth=0.6)
        ax.set_xlabel("signal decile (10 = implied rich)")
        ax.set_ylabel(f"{horizon_days}-session P&L per $ vega, long straddle")
        ax.legend()
        fig.tight_layout()
        fig.savefig(path, dpi=150)
        plt.close(fig)
