"""Run with: uv run python -m research.iv_mean_reversion.analysis."""

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import polars as pl

import data_access_layer as dal
from tools.backtest.straddle import run_backtest


FIELDS = ['bid', 'ask', 'mid', 'implied_vol', 'iv_error', 'volume']
KEYS = ['date', 'expiration', 'strike']
OUTPUT = Path(__file__).resolve().parent


def load_pairs(year):
    # No liquidity, IV, moneyness or volume filters on the marking universe.
    chain_df = dal.load_option_greeks(
        'SPXW', index=True, years=year, min_dte=0, max_dte=40,
        columns=KEYS + ['right', 'dte', 'underlying_price'] + FIELDS,
    )
    if chain_df.select(pl.struct(KEYS + ['right']).is_duplicated().sum()).item():
        raise ValueError(f'Duplicate contract/day keys in {year}')
    calls_df = chain_df.filter(pl.col('right') == 'CALL').drop('right').rename(
        {field: field + '_CALL' for field in FIELDS})
    puts_df = chain_df.filter(pl.col('right') == 'PUT').select(KEYS + FIELDS).rename(
        {field: field + '_PUT' for field in FIELDS})
    return calls_df.join(puts_df, on=KEYS, how='inner')


def select_signals(pairs_df):
    conditions = [(pl.col('dte') >= 25), (pl.col('dte') <= 35),
                  pl.col('underlying_price') > 0]
    for right in ('CALL', 'PUT'):
        conditions += [pl.col(f'bid_{right}').is_finite(),
                       pl.col(f'ask_{right}').is_finite(),
                       pl.col(f'bid_{right}') > 0,
                       pl.col(f'ask_{right}') >= pl.col(f'bid_{right}'),
                       pl.col(f'volume_{right}') > 0,
                       pl.col(f'implied_vol_{right}').is_between(0.01, 3),
                       pl.col(f'iv_error_{right}').abs() <= 0.01,
                       (pl.col(f'ask_{right}') - pl.col(f'bid_{right}'))
                       / pl.col(f'mid_{right}') <= 0.10]
    candidates_df = pairs_df.filter(pl.all_horizontal(conditions)).with_columns(
        (pl.col('dte') - 30).abs().alias('tenor_distance'),
        (pl.col('strike') / pl.col('underlying_price') - 1).abs().alias('atm_distance'),
        ((pl.col('implied_vol_CALL') + pl.col('implied_vol_PUT')) / 2).alias('iv'),
    ).filter(pl.col('atm_distance') <= 0.01).sort(
        ['date', 'tenor_distance', 'expiration', 'atm_distance', 'strike']
    ).unique('date', keep='first', maintain_order=True)
    return pairs_df.select('date').unique().join(
        candidates_df.select(KEYS + ['dte', 'iv', 'underlying_price']),
        on='date', how='left').sort('date')


def summarize_run(mode, trades_df, daily_df, skips):
    equity = np.r_[0, daily_df['net'].to_numpy().cumsum()]
    return dict(mode=mode, trades=trades_df.height, skipped_entries=skips,
                gross_pnl=trades_df['gross'].sum(), net_pnl=trades_df['net'].sum(),
                costs=daily_df['costs'].sum(), win_rate=(trades_df['net'] > 0).mean(),
                mean_trade=trades_df['net'].mean(), worst_trade=trades_df['net'].min(),
                max_drawdown=float((equity - np.maximum.accumulate(equity)).min()),
                missing_marks=daily_df['missing_marks'].sum())


def build_report(summary_df, annual_df, trades_df, audit, start, end):
    summary = summary_df.filter(pl.col('mode') == 'reversion').row(0, named=True)
    lines = [
        '# SPXW IV mean reversion: a fresh baseline', '',
        f'Sample: {start}–{end}. Baseline rules fixed before the first run; no parameter search. A two-sided-exit sensitivity was added after inspecting an anomalous quote.', '',
        f"The bid/ask scenario produced **${summary['net_pnl']:,.0f}** across "
        f"{summary['trades']} trades, versus ${summary['gross_pnl']:,.0f} before costs. "
        f"Maximum daily marked dollar drawdown was ${-summary['max_drawdown']:,.0f}.", '',
        '**Preliminary: quote availability materially limits this result.** In 2021, 244 sessions have no eligible signal straddle and no trades occur. One 2022 exit has a zero-bid put with an exceptionally wide ask. This is not a clean nine-year test of an investable strategy.', '',
        '## Rules', '',
        '- Instrument: one SPXW call plus one put at the same strike and expiration; $100 multiplier.',
        '- Select the expiry closest to 30 calendar days within 25–35, then the strike closest to spot within 1%. Deterministic ties prefer earlier expiry/lower strike.',
        '- Signal IV is the average of the two vendor IVs. Z-score uses the preceding 60 sessions (sample standard deviation), excluding the current observation; 60 valid values required.',
        '- Buy when z ≤ −1; sell when z ≥ +1. Freeze the selected contracts and enter at the following session’s EOD quote.',
        '- Hold five trading-session intervals; one position at a time; no same-session reopening. Skip entries too close to sample end.',
        '- At selection require both legs to trade that day, positive bids, uncrossed finite quotes, relative spread ≤10%, IV 1–300%, and absolute vendor IV residual ≤0.01. At entry recheck quote/volume/spread eligibility, without reselection or a new IV signal.',
        '- No delta hedge, stop, early profit-taking, compounding, or position-size optimization.', '',
        '## Execution and accounting', '',
        'Gross P&L uses mids. Net buys at ask and sells at bid on each leg, plus an assumed $1 per contract per side ($4 per round trip). This is an EOD quote simulation: the data cannot prove executable fills or synchronized live quotes. Entry costs are booked on entry day. Fees are illustrative, not a broker/exchange quote.', '',
        'Held contracts are marked from the loose quote universe, regardless of entry filters. A missing or invalid mark is carried forward and counted; an unavailable exit is delayed. Unresolved positions fail the run. The mark-to-market curve includes entry/exit costs and flat days.', '',
        'Dollar P&L is for a fixed one-straddle position. No account return, CAGR, or return Sharpe is claimed: collateral, broker margin, financing, and interest are not modeled. Short straddles have uncapped upside loss. Changing delta, gamma, and theta affect these unhedged trades, so profit is not a clean measurement of IV mean reversion.', '',
        '## Cost and direction comparisons', '',
        'Long-only and short-only controls use exactly the same extreme-IV entry dates/contracts as the strategy, changing only direction. They are timing-matched controls, not continuously invested benchmarks.', '',
        '| Scenario | Trades | Gross $ | Net $ | Win rate | Max drawdown $ |',
        '|---|---:|---:|---:|---:|---:|',
    ]
    for row in summary_df.iter_rows(named=True):
        lines.append(f"| {row['mode']} | {row['trades']} | {row['gross_pnl']:,.0f} | {row['net_pnl']:,.0f} | {row['win_rate']:.1%} | {-row['max_drawdown']:,.0f} |")
    lines += ['', 'Midpoint-fees still charges commission. Half-crossing pays half the quoted distance from mid to bid/ask.', '',
              'The two-sided-exit sensitivity keeps holding when either bid is zero and exits on the next usable session. It retains all interim marks and losses; it does not delete the affected trade. This is a different exit rule, added as a data-handling diagnostic, not a tuned strategy.', '',
              '## Annual daily-marked results', '',
              '| Year | Gross $ | Net $ |', '|---|---:|---:|']
    for row in annual_df.iter_rows(named=True):
        lines.append(f"| {row['year']} | {row['gross']:,.0f} | {row['net']:,.0f} |")
    lines += ['', '## Direction breakdown', '', '| Side | Trades | Net $ | Win rate | Mean IV change (points) |', '|---|---:|---:|---:|---:|']
    for direction, label in [(1, 'Long'), (-1, 'Short')]:
        subset_df = trades_df.filter(pl.col('direction') == direction)
        lines.append(f"| {label} | {subset_df.height} | {subset_df['net'].sum():,.0f} | {(subset_df['net'] > 0).mean():.1%} | {subset_df['iv_change'].mean() * 100:.2f} |")
    lines += ['', '## Data audit and limitations', '',
              f"- {audit['sessions']} observed sessions; {audit['missing_signal_days']} sessions without an eligible signal straddle; {audit['valid_zscores']} valid rolling z-scores.",
              f"- {summary['skipped_entries']} unavailable/illiquid next-session entries; {summary['missing_marks']} carried held-position marks.",
              '- Zero-bid exits are flagged in each trade ledger. On 2022-12-09 the held 4075-strike put expiring 2022-12-30 quotes 0 / 643.9, versus 137.2 / 141.5 the day before and 126.2 / 129.2 the next session. This one exit accounts for $32,267 of modeled exit costs. Its midpoint is also suspect; gross P&L is not immune to this issue.',
              '- In the 2021 25–35 DTE, ±1% moneyness SPXW slice, only 0.2824% of 26,204 contract-days have positive bids. Most have zero bid/ask and IV residual 100. No valid 60-session signal window survives in 2021. See coverage.csv for all years.',
              '- Nine calendar years are historical evidence, not independent trials. This is not an untouched holdout and no statistical significance is asserted.',
              '- Approximate 30-day IV changes expiry and strike over time; it is not interpolated constant-maturity IV. Contract IV changes during the hold also mix tenor and moneyness changes.',
              '- The calendar comes from observed SPXW sessions; any wholly missing market session would be invisible. The audit compares against stored universe sessions.',
              f"- Missing SPXW sessions relative to stored universe calendar: {audit['missing_calendar_sessions']}.",
              '- Daily snapshots cannot validate intraday stops, fill availability, or quote staleness. Selection uses vendor IV rather than independently reinverting prices.', '',
              '![Cumulative P&L](figures/pnl.png)', '',
              'Contract mechanics: [Cboe SPX product specifications](https://www.cboe.com/tradable-products/sp-500/spx-options/).', '',
              'Reproduce: `uv run python -m research.iv_mean_reversion.analysis`.', '']
    (OUTPUT / 'REPORT.md').write_text('\n'.join(lines))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start-year', type=int, default=2017)
    parser.add_argument('--end-year', type=int, default=2025)
    args = parser.parse_args()
    years = list(range(args.start_year, args.end_year + 1))
    panels = []
    for year in years:
        print(f'Signal selection {year}', flush=True)
        panels.append(select_signals(load_pairs(year)))
    signals_df = pl.concat(panels).sort('date').with_columns(
        pl.col('iv').shift(1).rolling_mean(60, min_samples=60).alias('iv_mean'),
        pl.col('iv').shift(1).rolling_std(60, min_samples=60).alias('iv_std'),
    ).with_columns(pl.when(pl.col('iv_std') > 0).then(
        (pl.col('iv') - pl.col('iv_mean')) / pl.col('iv_std')).alias('zscore'))
    contracts_df = signals_df.select('expiration', 'strike').drop_nulls().unique()
    marks = []
    for year in years:
        print(f'Held-contract quote loading {year}', flush=True)
        marks.append(load_pairs(year).join(contracts_df, on=['expiration', 'strike'], how='semi'))
    quotes_df = pl.concat(marks)
    calendar_df = dal.load_universe(with_history=True).select('date').unique().filter(
        pl.col('date').is_between(signals_df['date'].min(), signals_df['date'].max()))
    audit = dict(sessions=signals_df.height,
                 missing_signal_days=signals_df['iv'].null_count(),
                 valid_zscores=signals_df['zscore'].drop_nulls().len(),
                 missing_calendar_sessions=calendar_df.join(signals_df.select('date'), on='date', how='anti').height)
    results = OUTPUT / 'results'
    results.mkdir(exist_ok=True)
    (OUTPUT / 'figures').mkdir(exist_ok=True)
    signals_df.write_csv(results / 'signals.csv')
    signals_df.with_columns(pl.col('date').dt.year().alias('year')).group_by('year').agg(
        pl.len().alias('sessions'), pl.col('iv').null_count().alias('missing_iv'),
        pl.col('zscore').count().alias('valid_zscores')).sort('year').write_csv(results / 'coverage.csv')
    (results / 'data_inventory.txt').write_text(dal.describe_store())
    (results / 'audit.json').write_text(json.dumps(audit, indent=2))
    summaries = []
    fig, axis = plt.subplots(figsize=(11, 5))
    for label, mode, crossing in [('reversion', 'reversion', 1.),
                                   ('long', 'long', 1.), ('short', 'short', 1.),
                                   ('midpoint-fees', 'reversion', 0.),
                                   ('half-crossing', 'reversion', .5),
                                   ('two-sided-exit', 'reversion', 1.)]:
        trades_df, daily_df, skips = run_backtest(signals_df, quotes_df, mode=mode, crossing=crossing,
                                               two_sided_exit=label == 'two-sided-exit')
        summaries.append(summarize_run(label, trades_df, daily_df, skips))
        trades_df.write_csv(results / f'trades_{label}.csv')
        daily_df.write_csv(results / f'daily_{label}.csv')
        if label in ('reversion', 'long', 'short'):
            axis.plot(daily_df['date'], daily_df['net'].cum_sum(), label=label)
        if label == 'reversion':
            strategy_df = trades_df
            annual_df = daily_df.with_columns(pl.col('date').dt.year().alias('year')).group_by('year').agg(
                pl.col('gross').sum(), pl.col('net').sum()).sort('year')
    summary_df = pl.DataFrame(summaries)
    summary_df.write_csv(results / 'summary.csv')
    annual_df.write_csv(results / 'annual.csv')
    axis.set(title='SPXW: one straddle, five-session holds, bid/ask + fees', ylabel='Cumulative P&L ($)')
    axis.axhline(0, color='black', linewidth=.6)
    axis.legend()
    axis.grid(alpha=.2)
    fig.tight_layout()
    fig.savefig(OUTPUT / 'figures' / 'pnl.png', dpi=160)
    plt.close(fig)
    build_report(summary_df, annual_df, strategy_df, audit, signals_df['date'].min(), signals_df['date'].max())
    print(summary_df)
    print(audit)


if __name__ == '__main__':
    main()
