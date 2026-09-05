"""Synthetic checks for lag, spread accounting, and missing adverse quotes."""

from datetime import date, timedelta
import unittest

import polars as pl

from tools.backtest.straddle import run_backtest


def build_fixture():
    dates = [date(2025, 1, 6) + timedelta(days=index) for index in range(9)]
    expiry = date(2025, 2, 7)
    signals_df = pl.DataFrame(dict(date=dates, expiration=[expiry] * 9,
                                   strike=[100.] * 9, zscore=[-2.] + [None] * 8))
    quotes = []
    for index, day in enumerate(dates):
        row = dict(date=day, expiration=expiry, strike=100.)
        for right in ('CALL', 'PUT'):
            mark = 20. + index
            row.update({f'bid_{right}': mark - .5, f'ask_{right}': mark + .5,
                        f'mid_{right}': mark, f'volume_{right}': 10,
                        f'implied_vol_{right}': .2})
        quotes.append(row)
    return signals_df, pl.DataFrame(quotes)


class TestStraddle(unittest.TestCase):
    def test_lag_and_both_crossings(self):
        signals_df, quotes_df = build_fixture()
        trades_df, daily_df, skips = run_backtest(signals_df, quotes_df)
        trade = trades_df.row(0, named=True)
        self.assertEqual(trade['entry_date'], date(2025, 1, 7))
        self.assertEqual(trade['exit_date'], date(2025, 1, 12))
        self.assertEqual(trade['gross'], 1000.)
        self.assertEqual(trade['net'], 796.)  # 4 half-spreads x $50, plus 4 fees.
        self.assertEqual(daily_df['net'][1], -102.)
        self.assertEqual(skips, 0)

    def test_missing_exit_defers_and_keeps_adverse_move(self):
        signals_df, quotes_df = build_fixture()
        quotes_df = quotes_df.filter(pl.col('date') != date(2025, 1, 12))
        quotes_df = quotes_df.with_columns([
            pl.when(pl.col('date') >= date(2025, 1, 13)).then(pl.col(field) - 15)
            .otherwise(pl.col(field)).alias(field)
            for field in ['bid_CALL', 'ask_CALL', 'mid_CALL', 'bid_PUT', 'ask_PUT', 'mid_PUT']])
        trades_df, daily_df, skips = run_backtest(signals_df, quotes_df)
        self.assertEqual(trades_df['exit_date'][0], date(2025, 1, 13))
        self.assertEqual(trades_df['gross'][0], -1800.)
        self.assertEqual(daily_df['missing_marks'].sum(), 1)
        self.assertAlmostEqual(daily_df['net'].sum(), trades_df['net'].sum())

    def test_invalid_entry_is_not_reselected(self):
        signals_df, quotes_df = build_fixture()
        quotes_df = quotes_df.with_columns(pl.when(pl.col('date') == date(2025, 1, 7))
                                          .then(0).otherwise(pl.col('volume_CALL')).alias('volume_CALL'))
        trades_df, daily_df, skips = run_backtest(signals_df, quotes_df)
        self.assertEqual(trades_df.height, 0)
        self.assertEqual(skips, 1)
        self.assertEqual(daily_df['net'].sum(), 0.)

    def test_zero_bid_exit_sensitivity_retains_position(self):
        signals_df, quotes_df = build_fixture()
        quotes_df = quotes_df.with_columns(
            pl.when(pl.col('date') == date(2025, 1, 12)).then(0.)
            .otherwise(pl.col('bid_PUT')).alias('bid_PUT'))
        quotes_df = quotes_df.with_columns(((pl.col('bid_PUT') + pl.col('ask_PUT')) / 2).alias('mid_PUT'))
        base_df, daily_df, skips = run_backtest(signals_df, quotes_df)
        alternate_df, daily_df, skips = run_backtest(signals_df, quotes_df, two_sided_exit=True)
        self.assertTrue(base_df['zero_bid_exit'][0])
        self.assertEqual(alternate_df['exit_date'][0], date(2025, 1, 13))
        self.assertEqual(alternate_df['gross'][0], 1200.)
        self.assertAlmostEqual(daily_df['net'].sum(), alternate_df['net'].sum())


if __name__ == '__main__':
    unittest.main()
