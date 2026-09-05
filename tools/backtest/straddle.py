"""Lagged, nonoverlapping straddle accounting against supplied daily quotes."""

import math

import polars as pl


def compute_crossing(quote, fraction=1.0):
    """Fraction 1 crosses half the full bid/ask spread on each leg."""
    return fraction * (quote['ask_CALL'] - quote['bid_CALL']
                       + quote['ask_PUT'] - quote['bid_PUT']) * 50


def is_markable(quote):
    return quote is not None and all(
        math.isfinite(quote[f'{field}_{right}'])
        for field in ('bid', 'ask') for right in ('CALL', 'PUT')
    ) and all(0 <= quote[f'bid_{right}'] <= quote[f'ask_{right}']
              and quote[f'ask_{right}'] > 0 for right in ('CALL', 'PUT'))


def is_entry_valid(quote):
    return is_markable(quote) and all(
        quote[f'bid_{right}'] > 0
        and quote[f'volume_{right}'] > 0
        and (quote[f'ask_{right}'] - quote[f'bid_{right}'])
        / quote[f'mid_{right}'] <= 0.10
        for right in ('CALL', 'PUT')
    )


def run_backtest(signals_df, quotes_df, mode='reversion', hold=5,
                 threshold=1.0, crossing=1.0, fee=1.0, two_sided_exit=False):
    """One call plus one put; $100 multiplier; fees per contract per side.

    Selection is frozen on the signal day. Entry uses next session quotes.
    Missing marks carry forward with an audit flag; a missing exit delays exit.
    A still-unresolved trade fails the run rather than disappearing.
    """
    signals = signals_df.to_dicts()
    quotes = {(row['date'], row['expiration'], row['strike']): row
              for row in quotes_df.to_dicts()}
    daily = {row['date']: dict(date=row['date'], gross=0., net=0.,
                               costs=0., missing_marks=0) for row in signals}
    trades = []
    position = None
    skips = 0
    for index, signal in enumerate(signals):
        date = signal['date']
        row = daily[date]
        if position is not None:
            quote = quotes.get((date, position['expiration'], position['strike']))
            if not is_markable(quote):
                row['missing_marks'] = 1
                if date >= position['expiration']:
                    raise ValueError('Unresolved quote reached expiration')
                continue
            mark = quote['mid_CALL'] + quote['mid_PUT']
            pnl = position['direction'] * (mark - position['last_mark']) * 100
            row['gross'] += pnl
            row['net'] += pnl
            position['last_mark'] = mark
            exit_allowed = not two_sided_exit or all(
                quote[f'bid_{right}'] > 0 for right in ('CALL', 'PUT'))
            if index >= position['exit_index'] and exit_allowed:
                costs = compute_crossing(quote, crossing) + 2 * fee
                row['costs'] += costs
                row['net'] -= costs
                gross = position['direction'] * (mark - position['entry_mark']) * 100
                trades.append({key: value for key, value in position.items()
                               if key not in ('last_mark', 'exit_index')} | {
                    'exit_date': date, 'exit_mark': mark, 'gross': gross,
                    'zero_bid_exit': any(quote[f'bid_{right}'] == 0 for right in ('CALL', 'PUT')),
                    'net': gross - position['entry_cost'] - costs,
                    'exit_cost': costs,
                    'iv_change': (quote['implied_vol_CALL'] + quote['implied_vol_PUT']) / 2
                                 - position['entry_iv'],
                })
                position = None
            # No close-and-reopen on the same session.
            continue
        if index == 0 or index + hold >= len(signals):
            continue
        previous = signals[index - 1]
        zscore = previous['zscore']
        if zscore is None or not math.isfinite(zscore) or abs(zscore) < threshold:
            continue
        quote = quotes.get((date, previous['expiration'], previous['strike']))
        if not is_entry_valid(quote):
            skips += 1
            continue
        direction = (1 if zscore < 0 else -1) if mode == 'reversion' else (
            1 if mode == 'long' else -1)
        mark = quote['mid_CALL'] + quote['mid_PUT']
        costs = compute_crossing(quote, crossing) + 2 * fee
        row['costs'] += costs
        row['net'] -= costs
        position = dict(signal_date=previous['date'], entry_date=date,
                        expiration=previous['expiration'], strike=previous['strike'],
                        zscore=zscore, direction=direction, entry_mark=mark,
                        last_mark=mark, entry_cost=costs, exit_index=index + hold,
                        entry_iv=(quote['implied_vol_CALL'] + quote['implied_vol_PUT']) / 2)
    if position is not None:
        raise ValueError('Unresolved position at end of sample')
    trades_df = pl.DataFrame(trades)
    daily_df = pl.DataFrame(list(daily.values()))
    if trades:
        assert abs(trades_df['net'].sum() - daily_df['net'].sum()) < 1e-6
        assert abs(trades_df['gross'].sum() - daily_df['gross'].sum()) < 1e-6
        assert abs(daily_df['costs'].sum() - trades_df['entry_cost'].sum()
                   - trades_df['exit_cost'].sum()) < 1e-6
    return trades_df, daily_df, skips
